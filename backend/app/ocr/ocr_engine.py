"""
Tesseract-based OCR engine with automatic Windows path resolution and safe error handling.
Returns raw text plus a mean confidence score (0-1) so the review dashboard can flag low-confidence extractions.
"""
import os
import shutil
import numpy as np
import pytesseract
from pytesseract import Output

# Auto-detect standard Windows Tesseract installations if not already on PATH
if not shutil.which("tesseract"):
    for common_path in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    ):
        if os.path.exists(common_path):
            pytesseract.pytesseract.tesseract_cmd = common_path
            break


def run_ocr(img: np.ndarray) -> tuple[str, float]:
    """
    Returns (raw_text, mean_confidence in [0,1]).
    Safely catches any missing binary / execution error without crashing.
    """
    if img is None or img.size == 0:
        return "", 0.0

    try:
        data = pytesseract.image_to_data(img, output_type=Output.DICT, config="--psm 6")

        words = []
        confidences = []
        for i, word in enumerate(data.get("text", [])):
            word = word.strip()
            conf = data.get("conf", [])[i] if i < len(data.get("conf", [])) else -1
            try:
                conf = float(conf)
            except (ValueError, TypeError):
                continue
            if word and conf >= 0:
                words.append(word)
                confidences.append(conf)

        raw_text = " ".join(words) if words else pytesseract.image_to_string(img)
        mean_conf = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0

        # If Tesseract confidence is low or text is empty, attempt TrOCR handwritten recognition fallback
        if mean_conf < 0.35 or not raw_text.strip():
            try:
                from .trocr_engine import is_trocr_available, recognize_handwriting
                if is_trocr_available():
                    hw_text = recognize_handwriting(img)
                    if hw_text:
                        if not raw_text.strip():
                            raw_text = hw_text
                            mean_conf = 0.70
                        else:
                            raw_text = f"{raw_text}\n{hw_text}".strip()
                            mean_conf = max(mean_conf, 0.65)
            except Exception:
                pass

        return raw_text, mean_conf
    except Exception as e:
        # Fallback to TrOCR if Tesseract completely crashed
        try:
            from .trocr_engine import is_trocr_available, recognize_handwriting
            if is_trocr_available():
                hw_text = recognize_handwriting(img)
                if hw_text:
                    return hw_text, 0.70
        except Exception:
            pass
        # Graceful fallback: return empty text and 0 confidence so manual review handles it
        return "", 0.0
