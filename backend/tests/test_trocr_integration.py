"""
Verification & Diagnostic Test Suite for Local Offline TrOCR Handwritten Text Recognition (HTR).

Tests:
1. Strict Offline Environment Enforcement (HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1, local_files_only=True).
2. Local Model Directory & Weight Validation (Actionable messaging on missing weights, path resolution).
3. End-to-End Local Inference & Preprocessing (Real bill crops, RGB conversion, aspect-ratio scaling, cleaned extractors).
4. Graceful Fallback & Resilience (Zero crashes when weights/dependencies are absent, clean NEEDS_REVIEW state).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import cv2
import numpy as np
from PIL import Image

# Ensure backend root is on sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.ocr import trocr_engine
from app.ocr import field_extractor
from app.ocr import ocr_engine
from app.ocr import ai_vision
from app.paths import get_data_dir, get_models_dir


class TestStrictOfflineEnforcement(unittest.TestCase):
    def test_01_offline_environment_variables(self):
        """Verify that offline environment variables are strictly enforced."""
        self.assertEqual(os.environ.get("HF_HUB_OFFLINE"), "1")
        self.assertEqual(os.environ.get("TRANSFORMERS_OFFLINE"), "1")

        status = trocr_engine.get_model_status()
        self.assertTrue(status["offline_only"])
        self.assertTrue(status["zero_cloud_calls"])
        self.assertTrue(status["offline_env_enforced"])

    def test_02_local_files_only_flag_enforced(self):
        """Confirm that local_files_only=True is enforced across from_pretrained calls."""
        # Reset load attempted flag to test loader call
        with patch.object(trocr_engine, "HAS_TROCR_DEPS", True), \
             patch("app.ocr.trocr_engine.TrOCRProcessor") as mock_proc_cls, \
             patch("app.ocr.trocr_engine.VisionEncoderDecoderModel") as mock_model_cls:

            trocr_engine._load_attempted = False
            trocr_engine._processor = None
            trocr_engine._model = None

            mock_model = MagicMock()
            mock_model_cls.from_pretrained.return_value = mock_model
            mock_proc = MagicMock()
            mock_proc_cls.from_pretrained.return_value = mock_proc

            proc, model = trocr_engine.load_trocr_model()

            # Verify local_files_only was explicitly passed to both
            self.assertTrue(mock_proc_cls.from_pretrained.called)
            proc_kwargs = mock_proc_cls.from_pretrained.call_args[1]
            self.assertTrue(proc_kwargs.get("local_files_only"), "TrOCRProcessor must have local_files_only=True")

            self.assertTrue(mock_model_cls.from_pretrained.called)
            model_kwargs = mock_model_cls.from_pretrained.call_args[1]
            self.assertTrue(model_kwargs.get("local_files_only"), "VisionEncoderDecoderModel must have local_files_only=True")


class TestLocalModelDirectoryAndWeights(unittest.TestCase):
    def test_03_model_paths_and_validation(self):
        """Verify models directory resolution and weight validation logic."""
        models_dir = get_models_dir()
        self.assertTrue(os.path.isdir(models_dir))

        # Test validation on non-existent directory provides actionable message
        ok, msg = trocr_engine.validate_local_weights("C:/non_existent_model_dir_xyz")
        self.assertFalse(ok)
        self.assertIn("Action required", msg or "Offline TrOCR weights not found")
        self.assertIn("copy the model directory", msg)

        # Test validation on simulated valid local model directory
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create simulated config and weight files
            with open(os.path.join(tmp_dir, "config.json"), "w") as f:
                f.write('{"architectures": ["VisionEncoderDecoderModel"]}')
            with open(os.path.join(tmp_dir, "model.safetensors"), "wb") as f:
                f.write(b"SIMULATED_WEIGHTS")

            ok_sim, msg_sim = trocr_engine.validate_local_weights(tmp_dir)
            self.assertTrue(ok_sim)
            self.assertIn("Found local offline TrOCR weights", msg_sim)

    def test_04_status_summary_contains_actionable_diagnostics(self):
        """Verify get_model_status returns comprehensive offline diagnostics."""
        status = trocr_engine.get_model_status()
        self.assertIn("engine", status)
        self.assertIn("dependencies_installed", status)
        self.assertIn("model_loaded", status)
        self.assertIn("is_available", status)
        self.assertIn("model_path", status)
        self.assertIn("offline_env_enforced", status)
        self.assertIn("weights_validated", status)
        self.assertIn("weights_validation_message", status)


class TestEndToEndLocalInferenceAndPreprocessing(unittest.TestCase):
    def test_05_preprocessing_on_real_and_synthetic_crops(self):
        """Verify preprocessing pipeline across real processed crops and synthetic matrices."""
        # 1. Test real processed crop if available
        processed_dir = os.path.join(get_data_dir(), "processed")
        if os.path.isdir(processed_dir):
            crop_files = [f for f in os.listdir(processed_dir) if f.endswith(".jpg")]
            if crop_files:
                sample_path = os.path.join(processed_dir, crop_files[0])
                real_crop = cv2.imread(sample_path)
                pil_real = trocr_engine.preprocess_image_for_trocr(real_crop)
                self.assertIsNotNone(pil_real)
                self.assertEqual(pil_real.mode, "RGB")
                self.assertGreaterEqual(pil_real.size[1], 32)

        # 2. Test Grayscale conversion
        gray = np.full((40, 150), 128, dtype=np.uint8)
        pil_gray = trocr_engine.preprocess_image_for_trocr(gray)
        self.assertEqual(pil_gray.mode, "RGB")

        # 3. Test BGRA conversion
        bgra = np.full((50, 100, 4), (10, 20, 30, 255), dtype=np.uint8)
        pil_bgra = trocr_engine.preprocess_image_for_trocr(bgra)
        self.assertEqual(pil_bgra.mode, "RGB")

        # 4. Test aspect-ratio preserving scaling on tiny crop (< 32px height)
        tiny = np.full((16, 80, 3), 255, dtype=np.uint8)
        pil_tiny = trocr_engine.preprocess_image_for_trocr(tiny, min_height=32, target_height=64)
        self.assertEqual(pil_tiny.size[1], 64)
        self.assertEqual(pil_tiny.size[0], 320)  # 80 * (64 / 16) = 320

    def test_06_specialized_extractors_clean_parsing(self):
        """Verify specialized extractors (digits, amount, date) parse and clean output."""
        dummy_img = np.full((64, 128, 3), 255, dtype=np.uint8)

        mock_proc = MagicMock()
        mock_proc.return_value.pixel_values = MagicMock()
        mock_model = MagicMock()
        mock_model.parameters.side_effect = lambda: iter([MagicMock(device="cpu")])

        # A. Invoice Digits Extractor
        mock_proc.batch_decode.return_value = [" Bill No: #004521/26 "]
        with patch.object(trocr_engine, "is_trocr_available", return_value=True), \
             patch.object(trocr_engine, "_processor", mock_proc), \
             patch.object(trocr_engine, "_model", mock_model):
            digits = trocr_engine.recognize_handwritten_digits(dummy_img)
            self.assertEqual(digits, "00452126")

        # B. Currency Amount Extractor
        mock_proc.batch_decode.return_value = [" Total = ₹ 8,950.75 /- "]
        with patch.object(trocr_engine, "is_trocr_available", return_value=True), \
             patch.object(trocr_engine, "_processor", mock_proc), \
             patch.object(trocr_engine, "_model", mock_model):
            amt = trocr_engine.recognize_handwritten_amount(dummy_img)
            self.assertEqual(amt, 8950.75)

        # C. Date Extractor
        mock_proc.batch_decode.return_value = [" Date: 19/10/2026 "]
        with patch.object(trocr_engine, "is_trocr_available", return_value=True), \
             patch.object(trocr_engine, "_processor", mock_proc), \
             patch.object(trocr_engine, "_model", mock_model):
            dt = trocr_engine.recognize_handwritten_date(dummy_img)
            self.assertEqual(dt, "19-10-2026")


class TestGracefulFallbackAndResilience(unittest.TestCase):
    def test_07_trocr_engine_returns_none_when_unavailable(self):
        """Verify recognize functions return None when model is absent, never crashing."""
        img = np.full((50, 100, 3), 255, dtype=np.uint8)
        with patch.object(trocr_engine, "is_trocr_available", return_value=False):
            self.assertIsNone(trocr_engine.recognize_handwriting(img))
            self.assertIsNone(trocr_engine.recognize_handwritten_digits(img))
            self.assertIsNone(trocr_engine.recognize_handwritten_amount(img))
            self.assertIsNone(trocr_engine.recognize_handwritten_date(img))

    def test_08_field_extractor_falls_back_cleanly(self):
        """Verify field_extractor functions fall back without error when TrOCR is absent."""
        img = np.full((100, 200, 3), 255, dtype=np.uint8)

        # Case 1: Tesseract fails and TrOCR unavailable -> returns None
        with patch("pytesseract.image_to_string", side_effect=Exception("Tesseract missing")), \
             patch.object(trocr_engine, "is_trocr_available", return_value=False):
            inv = field_extractor.extract_invoice_number(img)
            self.assertIsNone(inv)

            dt = field_extractor.extract_date_from_crop(img)
            self.assertIsNone(dt)

            summary = field_extractor.extract_bill_summary(img)
            self.assertIsInstance(summary, dict)
            self.assertIsNone(summary["invoice_no"])
            self.assertIsNone(summary["date"])

    def test_09_ocr_engine_graceful_degradation(self):
        """Verify ocr_engine.run_ocr returns valid tuple when OCR engines fail."""
        img = np.full((50, 50, 3), 255, dtype=np.uint8)
        with patch("pytesseract.image_to_data", side_effect=Exception("Binary missing")), \
             patch.object(trocr_engine, "is_trocr_available", return_value=False):
            raw_text, conf = ocr_engine.run_ocr(img)
            self.assertEqual(raw_text, "")
            self.assertEqual(conf, 0.0)

    def test_10_ai_vision_graceful_needs_review_state(self):
        """Verify extract_bill_with_ai produces a safe NEEDS_REVIEW dict without unhandled exceptions."""
        import tempfile
        temp_img = np.full((200, 200, 3), 255, dtype=np.uint8)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            temp_path = f.name
        cv2.imwrite(temp_path, temp_img)

        try:
            with patch.object(trocr_engine, "is_trocr_available", return_value=False), \
                 patch("pytesseract.image_to_string", return_value=""):
                result = ai_vision.extract_bill_with_ai(temp_path)
                self.assertIsInstance(result, dict)
                self.assertEqual(result["party"], "Cash")
                self.assertEqual(result["total_value"], 0.0)
                self.assertIn("confidence", result)
                self.assertIn("notes", result)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
