"""
Tests for Configurable, Multi-Provider AI Vision Extraction Engine in TEasy.

Covers:
1. Provider routing: "groq", "gemini", "ollama", "offline".
2. Dynamic model name configuration: custom model overrides default provider models.
3. Structured JSON extraction: party_name, invoice_number, invoice_date, taxable_value, cgst, sgst, igst, total_amount.
4. Fallback resilience: network/API failures fall back gracefully to offline deterministic HTR/OCR.
5. Settings endpoints: saving and retrieving dynamic AI provider preferences.
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import cv2
import numpy as np

# Ensure backend root is on sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.ocr import ai_vision
from app import settings


class TestMultiProviderAIVision(unittest.TestCase):
    def setUp(self):
        # Create a temporary dummy image for vision tests
        self.temp_img = np.full((120, 200, 3), 255, dtype=np.uint8)
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        self.temp_path = self.temp_file.name
        self.temp_file.close()
        cv2.imwrite(self.temp_path, self.temp_img)

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)

    def test_01_offline_provider_makes_zero_network_calls(self):
        """When ai_provider is 'offline', call_vision_ai returns None and makes zero external requests."""
        with patch.object(settings, "get_settings", return_value={"ai_provider": "offline"}), \
             patch("requests.post") as mock_post:
            result = ai_vision.call_vision_ai(self.temp_path)
            self.assertIsNone(result)
            mock_post.assert_not_called()

    def test_02_groq_vision_structured_extraction(self):
        """Test Groq vision provider with structured JSON extraction."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "party_name": "Radha Krishna Traders",
                            "invoice_number": "RK-2026/045",
                            "invoice_date": "15-08-2026",
                            "taxable_value": 2000.0,
                            "cgst": 180.0,
                            "sgst": 180.0,
                            "igst": 0.0,
                            "total_amount": 2360.0
                        })
                    }
                }
            ]
        }

        mock_settings = {
            "ai_provider": "groq",
            "groq_api_key": "gsk_test_mock_key",
            "groq_vision_model": "llama-3.2-90b-vision-preview",
            "ai_model": ""
        }

        with patch.object(settings, "get_settings", return_value=mock_settings), \
             patch("requests.post", return_value=mock_response) as mock_post:
            result = ai_vision.call_vision_ai(self.temp_path)

            self.assertIsNotNone(result)
            self.assertEqual(result["party"], "Radha Krishna Traders")
            self.assertEqual(result["invoice_number"], "RK-2026/045")
            self.assertEqual(result["date"], "15-08-2026")
            self.assertEqual(result["taxable_value"], 2000.0)
            self.assertEqual(result["cgst"], 180.0)
            self.assertEqual(result["sgst"], 180.0)
            self.assertEqual(result["total_value"], 2360.0)
            self.assertGreaterEqual(result["confidence"], 0.85)
            self.assertIn("groq", result["notes"].lower())

            # Verify Groq request structure
            mock_post.assert_called_once()
            call_url = mock_post.call_args[0][0]
            self.assertIn("api.groq.com", call_url)
            call_payload = mock_post.call_args[1]["json"]
            self.assertEqual(call_payload["model"], "llama-3.2-90b-vision-preview")

    def test_03_gemini_vision_structured_extraction(self):
        """Test Google Gemini vision provider extraction."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "party_name": "Bharat Hardware Stores",
                                    "invoice_number": "BHS-8891",
                                    "invoice_date": "20-09-2026",
                                    "taxable_value": 5000.0,
                                    "cgst": 0.0,
                                    "sgst": 0.0,
                                    "igst": 900.0,
                                    "total_amount": 5900.0
                                })
                            }
                        ]
                    }
                }
            ]
        }

        mock_settings = {
            "ai_provider": "gemini",
            "gemini_api_key": "AIzaSy_mock_key",
            "gemini_vision_model": "gemini-1.5-flash",
            "ai_model": ""
        }

        with patch.object(settings, "get_settings", return_value=mock_settings), \
             patch("requests.post", return_value=mock_response) as mock_post:
            result = ai_vision.call_vision_ai(self.temp_path)

            self.assertIsNotNone(result)
            self.assertEqual(result["party"], "Bharat Hardware Stores")
            self.assertEqual(result["invoice_number"], "BHS-8891")
            self.assertEqual(result["igst"], 900.0)
            self.assertEqual(result["total_value"], 5900.0)

            mock_post.assert_called_once()
            call_url = mock_post.call_args[0][0]
            self.assertIn("generativelanguage.googleapis.com", call_url)
            self.assertIn("gemini-1.5-flash", call_url)

    def test_04_ollama_local_vision_extraction(self):
        """Test local Ollama endpoint extraction."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps({
                    "party_name": "Local Cash Vendor",
                    "invoice_number": "LV-102",
                    "invoice_date": "05-10-2026",
                    "taxable_value": 800.0,
                    "cgst": 0.0,
                    "sgst": 0.0,
                    "igst": 0.0,
                    "total_amount": 800.0
                })
            }
        }

        mock_settings = {
            "ai_provider": "ollama",
            "ollama_endpoint": "http://127.0.0.1:11434",
            "ollama_vision_model": "qwen2-vl",
            "ai_model": ""
        }

        with patch.object(settings, "get_settings", return_value=mock_settings), \
             patch("requests.post", return_value=mock_response) as mock_post:
            result = ai_vision.call_vision_ai(self.temp_path)

            self.assertIsNotNone(result)
            self.assertEqual(result["party"], "Local Cash Vendor")
            self.assertEqual(result["invoice_number"], "LV-102")
            self.assertEqual(result["total_value"], 800.0)

            mock_post.assert_called_once()
            call_url = mock_post.call_args[0][0]
            self.assertEqual(call_url, "http://127.0.0.1:11434/api/chat")
            payload = mock_post.call_args[1]["json"]
            self.assertEqual(payload["model"], "qwen2-vl")

    def test_05_dynamic_custom_model_override(self):
        """User-typed ai_model dynamically overrides default model name."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "party_name": "Test Party",
                            "invoice_number": "123",
                            "total_amount": 100.0
                        })
                    }
                }
            ]
        }

        mock_settings = {
            "ai_provider": "groq",
            "groq_api_key": "gsk_test",
            "groq_vision_model": "llama-3.2-90b-vision-preview",
            "ai_model": "llama-3.2-11b-vision-preview"  # Custom user model override
        }

        with patch.object(settings, "get_settings", return_value=mock_settings), \
             patch("requests.post", return_value=mock_response) as mock_post:
            result = ai_vision.call_vision_ai(self.temp_path)
            self.assertIsNotNone(result)
            payload = mock_post.call_args[1]["json"]
            self.assertEqual(payload["model"], "llama-3.2-11b-vision-preview")

    def test_06_cloud_api_failure_graceful_fallback(self):
        """When an external AI API raises an exception, call_vision_ai logs and returns None, falling back to local OCR."""
        mock_settings = {
            "ai_provider": "groq",
            "groq_api_key": "gsk_invalid",
            "groq_vision_model": "llama-3.2-90b-vision-preview",
            "ai_model": ""
        }

        with patch.object(settings, "get_settings", return_value=mock_settings), \
             patch("requests.post", side_effect=Exception("Connection refused or Rate limited")):
            # Must not crash!
            result = ai_vision.call_vision_ai(self.temp_path)
            self.assertIsNone(result)

            # End-to-end extract_bill_with_ai continues with local deterministic OCR
            full_res = ai_vision.extract_bill_with_ai(self.temp_path)
            self.assertIsInstance(full_res, dict)
            self.assertIn("party", full_res)
            self.assertIn("confidence", full_res)


if __name__ == "__main__":
    unittest.main()
