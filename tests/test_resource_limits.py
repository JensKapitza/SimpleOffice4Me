import os
import unittest
from unittest.mock import patch

from app.document_store import ocr_subprocess_environment, pdf_ocr_page_limit
from tools.launcher import should_report_scan_progress


class ResourceLimitTest(unittest.TestCase):
    def test_scan_progress_is_batched_by_file_count(self):
        self.assertFalse(should_report_scan_progress(249, 0, 1.99, 0.0))
        self.assertTrue(should_report_scan_progress(250, 0, 1.99, 0.0))

    def test_scan_progress_is_reported_after_time_interval(self):
        self.assertTrue(should_report_scan_progress(1, 0, 2.0, 0.0))

    def test_ocr_thread_limit_is_configurable_and_bounded(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_OCR_THREADS": "4"}, clear=True):
            self.assertEqual("4", ocr_subprocess_environment()["OMP_THREAD_LIMIT"])
        with patch.dict(os.environ, {"SIMPLEOFFICE_OCR_THREADS": "99"}, clear=True):
            self.assertEqual("8", ocr_subprocess_environment()["OMP_THREAD_LIMIT"])
        with patch.dict(os.environ, {"SIMPLEOFFICE_OCR_THREADS": "invalid"}, clear=True):
            self.assertEqual("1", ocr_subprocess_environment()["OMP_THREAD_LIMIT"])

    def test_pdf_ocr_page_limit_is_configurable_and_bounded(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(10, pdf_ocr_page_limit())
        with patch.dict(os.environ, {"SIMPLEOFFICE_PDF_OCR_MAX_PAGES": "7"}, clear=True):
            self.assertEqual(7, pdf_ocr_page_limit())
        with patch.dict(os.environ, {"SIMPLEOFFICE_PDF_OCR_MAX_PAGES": "500"}, clear=True):
            self.assertEqual(50, pdf_ocr_page_limit())
        with patch.dict(os.environ, {"SIMPLEOFFICE_PDF_OCR_MAX_PAGES": "bad"}, clear=True):
            self.assertEqual(10, pdf_ocr_page_limit())


if __name__ == "__main__":
    unittest.main()
