"""
Tests for TEasy Batch Upload Pipeline with 25s Sequential Throttle Delay and Smart 429 Backoff.

Covers:
1. Sequential batch upload execution with 25-second throttle delay between files.
2. Smart 429 rate limit parsing: extracts wait time from Groq error messages and adds safety buffer.
3. Automatic 429 retry: when a file encounters 429, queue pauses and retries file automatically.
4. Error resilience: non-429 or persistent errors are logged without crashing remaining queue items.
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure backend root is on sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from fastapi import HTTPException, UploadFile
from app.routers.documents import batch_upload_documents, parse_rate_limit_delay
from app import schemas


class TestBatchUploadPipeline(unittest.TestCase):
    def test_01_parse_rate_limit_delay(self):
        """Tests parsing of wait times from various Groq/LLM 429 error messages."""
        # Standard Groq message: "Please try again in 18.23s."
        groq_msg = (
            "Rate limit reached for model `llama-3.2-90b-vision-preview` on tokens per minute (TPM): "
            "Limit 7000, Used 6500, Requested 2300. Please try again in 18.234s. Visit https://console.groq.com"
        )
        self.assertAlmostEqual(parse_rate_limit_delay(groq_msg, safety_buffer=3.0), 21.23, places=1)

        # Minutes and seconds format
        min_sec_msg = "Rate limit exceeded. Please try again in 1m 15s."
        self.assertEqual(parse_rate_limit_delay(min_sec_msg, safety_buffer=3.0), 78.0)

        # Integer seconds format
        int_sec_msg = "Too many requests. Please try again in 30 seconds."
        self.assertEqual(parse_rate_limit_delay(int_sec_msg, safety_buffer=3.0), 33.0)

        # Generic message without explicit time falls back to default 25s + buffer
        fallback_msg = "Rate limit reached on TPM. Please slow down."
        self.assertEqual(parse_rate_limit_delay(fallback_msg, default_delay=25.0, safety_buffer=3.0), 28.0)

    def test_02_batch_upload_sequential_with_25s_delay(self):
        """Batch upload processes multiple files sequentially with default 25-second throttle delay."""
        mock_file1 = MagicMock(spec=UploadFile)
        mock_file1.filename = "bill1.jpg"
        mock_file1.file = MagicMock()

        mock_file2 = MagicMock(spec=UploadFile)
        mock_file2.filename = "bill2.jpg"
        mock_file2.file = MagicMock()

        mock_doc1 = MagicMock()
        mock_doc1.id = 101
        mock_doc1.file_name = "bill1.jpg"
        mock_doc1.document_type = "SALES"
        mock_doc1.status = "NEEDS_REVIEW"
        mock_doc1.uploaded_at = "2026-10-20T12:00:00"
        mock_doc1.bills = []

        mock_doc2 = MagicMock()
        mock_doc2.id = 102
        mock_doc2.file_name = "bill2.jpg"
        mock_doc2.document_type = "SALES"
        mock_doc2.status = "NEEDS_REVIEW"
        mock_doc2.uploaded_at = "2026-10-20T12:00:25"
        mock_doc2.bills = []

        mock_db = MagicMock()

        with patch("app.routers.documents.upload_document", side_effect=[mock_doc1, mock_doc2]) as mock_upload, \
             patch("time.sleep") as mock_sleep:

            response = batch_upload_documents(
                files=[mock_file1, mock_file2],
                document_type="SALES",
                delay_seconds=25.0,
                db=mock_db,
            )

            self.assertIsInstance(response, schemas.BatchUploadResponse)
            self.assertEqual(response.total, 2)
            self.assertEqual(response.successful, 2)
            self.assertEqual(response.failed, 0)
            self.assertEqual(response.throttle_delay_seconds, 25.0)
            self.assertEqual(len(response.results), 2)
            self.assertEqual(response.results[0].filename, "bill1.jpg")
            self.assertEqual(response.results[0].status, "success")
            self.assertEqual(response.results[1].filename, "bill2.jpg")
            self.assertEqual(response.results[1].status, "success")

            # Verify throttle delay: called once between file 1 and file 2 with 25.0s
            mock_sleep.assert_called_once_with(25.0)
            self.assertEqual(mock_upload.call_count, 2)

    def test_03_batch_upload_smart_429_backoff_and_retry(self):
        """When a file encounters 429 rate limit, it extracts wait time, sleeps, and retries successfully."""
        mock_file1 = MagicMock(spec=UploadFile)
        mock_file1.filename = "bill1.jpg"
        mock_file1.file = MagicMock()

        mock_file2 = MagicMock(spec=UploadFile)
        mock_file2.filename = "bill2_rate_limited.jpg"
        mock_file2.file = MagicMock()

        mock_doc1 = MagicMock(id=201, file_name="bill1.jpg", document_type="SALES", status="NEEDS_REVIEW", uploaded_at="2026-10-20T12:00:00", bills=[])
        # file2 fails with 429 on attempt 1, then succeeds on attempt 2!
        rate_limit_exc = HTTPException(
            status_code=429,
            detail="Rate limit reached for model `llama-3.2-90b-vision-preview` on TPM. Please try again in 18.2s.",
        )
        mock_doc2 = MagicMock(id=202, file_name="bill2_rate_limited.jpg", document_type="SALES", status="NEEDS_REVIEW", uploaded_at="2026-10-20T12:00:46", bills=[])

        side_effects = [
            mock_doc1,       # file 1
            rate_limit_exc,  # file 2 (attempt 1 -> 429)
            mock_doc2,       # file 2 (attempt 2 -> success!)
        ]

        mock_db = MagicMock()

        with patch("app.routers.documents.upload_document", side_effect=side_effects) as mock_upload, \
             patch("time.sleep") as mock_sleep:

            response = batch_upload_documents(
                files=[mock_file1, mock_file2],
                document_type="SALES",
                delay_seconds=25.0,
                db=mock_db,
            )

            self.assertEqual(response.total, 2)
            self.assertEqual(response.successful, 2)
            self.assertEqual(response.failed, 0)

            # Both files succeeded in the end
            self.assertEqual(response.results[0].status, "success")
            self.assertEqual(response.results[1].status, "success")

            # Check time.sleep calls:
            # 1. 25.0s between file 1 and file 2
            # 2. 18.2s + 3s buffer = 21.2s for 429 backoff before retry
            sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
            self.assertIn(25.0, sleep_calls)
            self.assertTrue(any(abs(s - 21.2) < 0.5 for s in sleep_calls))
            self.assertEqual(mock_upload.call_count, 3)

    def test_04_batch_upload_resilient_error_handling(self):
        """If one bill fails permanently, it logs and continues processing remaining bills in the batch."""
        mock_file1 = MagicMock(spec=UploadFile)
        mock_file1.filename = "bill1.jpg"
        mock_file1.file = MagicMock()

        mock_file2 = MagicMock(spec=UploadFile)
        mock_file2.filename = "corrupted_bill.jpg"
        mock_file2.file = MagicMock()

        mock_file3 = MagicMock(spec=UploadFile)
        mock_file3.filename = "bill3.jpg"
        mock_file3.file = MagicMock()

        mock_doc1 = MagicMock(id=301, file_name="bill1.jpg", document_type="SALES", status="NEEDS_REVIEW", uploaded_at="2026-10-20T12:00:00", bills=[])
        mock_doc3 = MagicMock(id=303, file_name="bill3.jpg", document_type="SALES", status="NEEDS_REVIEW", uploaded_at="2026-10-20T12:00:50", bills=[])

        error_side_effects = [
            mock_doc1,
            HTTPException(status_code=400, detail="Corrupted image file: cannot detect bill contours."),
            mock_doc3,
        ]

        mock_db = MagicMock()

        with patch("app.routers.documents.upload_document", side_effect=error_side_effects) as mock_upload, \
             patch("time.sleep") as mock_sleep:

            response = batch_upload_documents(
                files=[mock_file1, mock_file2, mock_file3],
                document_type="SALES",
                delay_seconds=25.0,
                db=mock_db,
            )

            self.assertEqual(response.total, 3)
            self.assertEqual(response.successful, 2)
            self.assertEqual(response.failed, 1)

            # File 1 succeeded
            self.assertEqual(response.results[0].status, "success")

            # File 2 failed gracefully without aborting batch queue
            self.assertEqual(response.results[1].filename, "corrupted_bill.jpg")
            self.assertEqual(response.results[1].status, "error")
            self.assertIn("Corrupted image file", response.results[1].error)

            # File 3 succeeded
            self.assertEqual(response.results[2].status, "success")
            self.assertEqual(mock_upload.call_count, 3)

    def test_05_internal_crop_sequential_delay_and_retry(self):
        """When a bill splits into 4 sub-crops, they are processed sequentially with 25s delays and 429 auto-retry."""
        from app.routers.documents import _process_document
        import numpy as np

        mock_doc = MagicMock()
        mock_doc.id = 999
        mock_doc.file_path = "dummy_path.jpg"
        mock_doc.file_name = "test_sales_grid.jpg"
        mock_doc.document_type = "SALES"
        mock_doc.company_id = 1

        mock_db = MagicMock()
        mock_db.query().get.return_value = mock_doc

        dummy_img = np.zeros((200, 200, 3), dtype=np.uint8)
        dummy_crops = [
            ((0, 0, 50, 50), dummy_img[:50, :50]),
            ((50, 0, 50, 50), dummy_img[:50, 50:100]),
            ((0, 50, 50, 50), dummy_img[50:100, :50]),
            ((50, 50, 50, 50), dummy_img[50:100, 50:100]),
        ]

        ai_res_success = {
            "party": "Test Shop",
            "date": "2026-10-20",
            "invoice_number": "1001",
            "taxable_value": 1000.0,
            "gst_rate": 18.0,
            "cgst": 90.0,
            "sgst": 90.0,
            "total_value": 1180.0,
            "confidence": 0.95,
        }

        # Crop 0: succeeds immediately
        # Crop 1: fails with 429 on first try, then succeeds on retry
        # Crop 2: succeeds
        # Crop 3: succeeds
        rate_limit_exc = HTTPException(
            status_code=429,
            detail="Rate limit reached for model `llama-3.2-90b-vision-preview` on TPM. Please try again in 14.5s.",
        )
        ai_side_effects = [
            ai_res_success,   # Crop 0
            rate_limit_exc,   # Crop 1 attempt 1 (429)
            ai_res_success,   # Crop 1 attempt 2 (retry success)
            ai_res_success,   # Crop 2
            ai_res_success,   # Crop 3
        ]

        with patch("app.routers.documents.preprocess_pipeline", return_value=dummy_img), \
             patch("app.routers.documents.detect_four_bill_grid", return_value=[b[0] for b in dummy_crops]), \
             patch("app.routers.documents.crop_bills", return_value=dummy_crops), \
             patch("cv2.imwrite", return_value=True), \
             patch("os.path.exists", return_value=True), \
             patch("app.routers.documents.extract_bill_with_ai", side_effect=ai_side_effects) as mock_extract, \
             patch("time.sleep") as mock_sleep:

            _process_document(document_id=999, db=mock_db)

            # 4 crops:
            # - crop 0: no initial crop delay
            # - crop 1: 25s crop delay, hits 429, sleeps 14.5 + 3 = 17.5s, retries & succeeds
            # - crop 2: 25s crop delay
            # - crop 3: 25s crop delay
            sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]

            # Verify 25.0s mandatory delays between crops (crops 1, 2, 3 = 3 times)
            self.assertEqual(sleep_calls.count(25.0), 3)

            # Verify 429 retry sleep was called with 14.5 + 3.0 = 17.5s
            self.assertTrue(any(abs(s - 17.5) < 0.5 for s in sleep_calls))

            # Total extract_bill_with_ai calls = 5 (4 crops + 1 retry)
            self.assertEqual(mock_extract.call_count, 5)

            # Final document status should be NEEDS_REVIEW
            self.assertEqual(mock_doc.status, "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
