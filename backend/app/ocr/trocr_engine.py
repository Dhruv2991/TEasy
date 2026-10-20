"""
Microsoft TrOCR (Transformer-based Optical Character Recognition) Engine
for Automated, 100% Offline Handwritten Text Recognition (HTR) on Physical Bill-Book Crops.

Features:
- Completely offline: zero external API calls, billing, or cloud telemetry.
- Runs using locally cached weights (e.g. microsoft/trocr-small-handwritten or microsoft/trocr-base-handwritten).
- Robust fallback: if PyTorch/transformers dependencies or weights are missing,
  gracefully falls back without crashing the backend or raising uncaught exceptions.
"""
import os
import re
import logging
from typing import Optional, Union, Tuple, Any

import cv2
import numpy as np
from PIL import Image

from ..paths import get_models_dir

logger = logging.getLogger("teasy.ocr.trocr")

# Explicitly force Hugging Face transformers/hub into strict offline mode to guarantee zero cloud dependencies
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

# Global singleton handles for lazy loading
_processor: Optional[Any] = None
_model: Optional[Any] = None
_load_attempted: bool = False
_is_available: bool = False
_last_error_message: Optional[str] = None

# Try importing dependencies defensively
try:
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel
    HAS_TROCR_DEPS = True
except Exception as e:
    HAS_TROCR_DEPS = False
    torch = None
    TrOCRProcessor = None
    VisionEncoderDecoderModel = None
    logger.info("TrOCR optional dependencies (torch/transformers) not present: %s", e)


def get_default_model_identifier() -> str:
    """Returns local path or model identifier for TrOCR.
    Defaults to trocr-small-handwritten for fast CPU inference.
    """
    # 1. Explicit path in environment
    env_path = os.environ.get("TROCR_MODEL_PATH") or os.environ.get("TROCR_MODEL_DIR")
    if env_path and os.path.exists(env_path):
        return env_path

    # 2. Check local models directory under app data
    models_dir = get_models_dir()
    for candidate in ("trocr-small-handwritten", "trocr", "trocr-base-handwritten"):
        candidate_path = os.path.join(models_dir, candidate)
        if os.path.isdir(candidate_path) and os.path.exists(os.path.join(candidate_path, "config.json")):
            return candidate_path

    # 3. Fallback to default HuggingFace identifier
    return os.environ.get("TROCR_MODEL_NAME", "microsoft/trocr-small-handwritten")


def validate_local_weights(model_path: Optional[str] = None) -> Tuple[bool, str]:
    """Validates whether local offline model weights exist on disk for TrOCR."""
    target = model_path or get_default_model_identifier()
    models_dir = get_models_dir()
    default_expected = os.path.join(models_dir, "trocr-small-handwritten")

    if os.path.isdir(target):
        has_config = os.path.exists(os.path.join(target, "config.json"))
        has_weights = (
            os.path.exists(os.path.join(target, "pytorch_model.bin"))
            or os.path.exists(os.path.join(target, "model.safetensors"))
        )
        if has_config and has_weights:
            return True, f"Found local offline TrOCR weights at: {target}"
        else:
            return False, (
                f"Directory exists at '{target}', but is missing 'config.json' or model weights "
                f"('pytorch_model.bin' / 'model.safetensors')."
            )

    return False, (
        f"Action required: Offline TrOCR weights not found at '{target}'. "
        f"To enable 100% offline handwritten OCR, copy the model directory into '{default_expected}' "
        f"or configure TROCR_MODEL_PATH. System will fall back cleanly to standard OCR / manual review."
    )


def is_trocr_available() -> bool:
    """Checks whether TrOCR dependencies and local model weights are ready to serve."""
    global _is_available, _load_attempted
    if not HAS_TROCR_DEPS:
        return False
    if _load_attempted:
        return _is_available

    proc, model = load_trocr_model()
    return model is not None and proc is not None


def load_trocr_model() -> Tuple[Optional[Any], Optional[Any]]:
    """Loads TrOCR model and processor with strict offline caching.
    Safely catches any missing weights or execution error without crashing.
    """
    global _processor, _model, _load_attempted, _is_available, _last_error_message

    if _load_attempted:
        return _processor, _model

    _load_attempted = True
    # Ensure offline environment is enforced
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    if not HAS_TROCR_DEPS:
        _is_available = False
        _last_error_message = (
            "PyTorch or Transformers dependencies are not installed in the local environment. "
            "Offline TrOCR is disabled; falling back to Tesseract OCR."
        )
        return None, None

    model_identifier = get_default_model_identifier()
    logger.info("Initializing offline TrOCR model from: %s", model_identifier)

    # Check local files first
    valid, validation_msg = validate_local_weights(model_identifier)
    if not valid:
        logger.warning(validation_msg)

    try:
        # Strict local_files_only ensures zero external network requests
        processor = TrOCRProcessor.from_pretrained(
            model_identifier,
            local_files_only=True,
        )
        model = VisionEncoderDecoderModel.from_pretrained(
            model_identifier,
            local_files_only=True,
        )

        device = "cuda" if (torch.cuda.is_available() and os.environ.get("USE_CUDA", "0") == "1") else "cpu"
        model.to(device)
        model.eval()

        _processor = processor
        _model = model
        _is_available = True
        _last_error_message = None
        logger.info("TrOCR offline handwritten model successfully loaded on %s", device)
        return _processor, _model

    except Exception as e:
        _last_error_message = (
            f"TrOCR model could not be loaded offline (weights not found at '{model_identifier}'): {e}. "
            f"Action required: Copy offline model files to '{os.path.join(get_models_dir(), 'trocr-small-handwritten')}' "
            f"or set TROCR_MODEL_PATH."
        )
        logger.warning(_last_error_message)
        _processor = None
        _model = None
        _is_available = False
        return None, None


def preprocess_image_for_trocr(
    image: Union[np.ndarray, Image.Image],
    min_height: int = 32,
    target_height: int = 64,
) -> Optional[Image.Image]:
    """Converts numpy array (OpenCV BGR/Grayscale) or PIL image to an RGB PIL Image,
    handling aspect ratio and minimum height scaling for optimal transformer attention.
    """
    if image is None:
        return None

    try:
        pil_img: Image.Image
        if isinstance(image, np.ndarray):
            if image.size == 0:
                return None
            if len(image.shape) == 2:  # Grayscale
                pil_img = Image.fromarray(image).convert("RGB")
            elif image.shape[2] == 4:  # BGRA
                rgb = cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
                pil_img = Image.fromarray(rgb)
            elif image.shape[2] == 3:  # BGR
                rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
            else:
                pil_img = Image.fromarray(image).convert("RGB")
        elif isinstance(image, Image.Image):
            pil_img = image.convert("RGB")
        else:
            return None

        # Resize small crops (such as digit boxes) so that features are distinct
        w, h = pil_img.size
        if h < min_height and h > 0:
            scale = target_height / float(h)
            new_w = max(int(w * scale), 16)
            pil_img = pil_img.resize((new_w, target_height), Image.Resampling.BICUBIC)

        return pil_img

    except Exception as e:
        logger.debug("Error preprocessing image for TrOCR: %s", e)
        return None


def recognize_handwriting(
    image: Union[np.ndarray, Image.Image],
    max_new_tokens: int = 64,
    digits_only: bool = False,
) -> Optional[str]:
    """Executes offline handwritten text recognition on an image crop.

    Returns the decoded text, or None if the model is unavailable or inference fails.
    """
    if not is_trocr_available():
        return None

    proc, model = _processor, _model
    if proc is None or model is None:
        return None

    pil_img = preprocess_image_for_trocr(image)
    if pil_img is None:
        return None

    try:
        try:
            device = next(model.parameters()).device
        except Exception:
            device = "cpu"
        pixel_values = proc(pil_img, return_tensors="pt").pixel_values.to(device)

        from contextlib import nullcontext
        ctx = torch.no_grad() if torch is not None else nullcontext()
        with ctx:
            generated_ids = model.generate(
                pixel_values,
                max_new_tokens=max_new_tokens,
                num_beams=1,  # Greedy search for rapid CPU inference
            )

        text = proc.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()
        if not text:
            return None

        if digits_only:
            # Filter non-digits except common separators
            cleaned = re.sub(r"[^\d\.\-\/]", "", text)
            return cleaned or None

        return text

    except Exception as e:
        logger.debug("TrOCR handwritten inference failed: %s", e)
        return None


def recognize_handwritten_digits(image: Union[np.ndarray, Image.Image]) -> Optional[str]:
    """Specialized helper for extracting handwritten invoice numbers, serials, and quantities."""
    raw = recognize_handwriting(image, max_new_tokens=32, digits_only=True)
    if not raw:
        return None
    # Strip any remaining non-digit characters
    digits = re.sub(r"[^\d]", "", raw)
    return digits or None


def recognize_handwritten_amount(image: Union[np.ndarray, Image.Image]) -> Optional[float]:
    """Extracts a handwritten currency amount from a cropped box (e.g. '1,450.00' or '240/-')."""
    raw = recognize_handwriting(image, max_new_tokens=32, digits_only=True)
    if not raw:
        return None
    # Remove slash/hyphen notations like '/-'
    cleaned = raw.replace("/-", "").replace(",", "").strip()
    match = re.search(r"\d+(?:\.\d{1,2})?", cleaned)
    if match:
        try:
            val = float(match.group(0))
            return val if val > 0 else None
        except ValueError:
            return None
    return None


def recognize_handwritten_date(image: Union[np.ndarray, Image.Image]) -> Optional[str]:
    """Extracts a handwritten date string from a cropped date box."""
    raw = recognize_handwriting(image, max_new_tokens=32, digits_only=False)
    if not raw:
        return None

    # Look for common Indian date formats: DD/MM/YYYY, DD-MM-YYYY, DD/MM/YY
    date_match = re.search(r"\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b", raw)
    if date_match:
        return date_match.group(1).replace(".", "-").replace("/", "-")
    return None


def get_model_status() -> dict:
    """Returns diagnostic information on the local TrOCR runtime status."""
    target = get_default_model_identifier()
    weights_ok, weights_msg = validate_local_weights(target)
    offline_enforced = (
        os.environ.get("HF_HUB_OFFLINE") == "1"
        and os.environ.get("TRANSFORMERS_OFFLINE") == "1"
    )
    return {
        "engine": "Microsoft TrOCR (Offline HTR)",
        "dependencies_installed": HAS_TROCR_DEPS,
        "model_loaded": _model is not None and _processor is not None,
        "is_available": is_trocr_available(),
        "model_path": target,
        "offline_only": True,
        "zero_cloud_calls": True,
        "offline_env_enforced": offline_enforced,
        "weights_validated": weights_ok,
        "weights_validation_message": weights_msg,
        "last_error": _last_error_message,
    }
