"""Read-only checks used by normal startup and --check-system."""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools import system_requirements as check


class NativeSystemReportTests(unittest.TestCase):
    def output(self, available, *, only_missing, result=None, family="debian"):
        with (
            patch.object(check, "system_family", return_value=family),
            patch.object(check.shutil, "which", side_effect=lambda name: "/test/" + name if name in available else None),
            patch("subprocess.run", side_effect=result if isinstance(result, Exception) else None,
                  return_value=result) as process,
            redirect_stdout(io.StringIO()) as buffer,
        ):
            self.assertEqual(0, check.report(only_missing=only_missing))
        return buffer.getvalue(), process

    def test_startup_missing_only_lists_absent_ocr_tools_not_installed_ones(self):
        out, process = self.output({"pdftoppm", "ffmpeg"}, only_missing=True)
        self.assertIn("FEHLT Foto-/Scan-OCR: tesseract", out)
        self.assertIn("FEHLT PDF-Text: pdftotext", out)
        self.assertIn("tesseract-ocr-deu", out)
        self.assertNotIn("  OK ", out)
        process.assert_not_called()

    def test_missing_pdfimages_is_reported_even_when_other_ocr_tools_are_present(self):
        available = {command for commands in check.TOOLS.values() for command in commands} - {"pdfimages"}
        out, process = self.output(available, only_missing=True)
        self.assertIn("FEHLT PDF-Bildprüfung für Reader-OCR: pdfimages", out)
        self.assertIn("poppler-utils", out)
        self.assertNotIn("FEHLT PDF-Text", out)
        self.assertNotIn("FEHLT PDF-Vorschau", out)
        process.assert_not_called()

    def test_explicit_check_reports_pdfimages_and_language_packs_separately(self):
        langs = subprocess.CompletedProcess([], 0, stdout=b"deu\neng\n")
        tools = {"pdftoppm", "pdftotext", "tesseract"}
        missing, _ = self.output(tools, only_missing=False, result=langs)
        ready, probe = self.output(tools | {"pdfimages"}, only_missing=False, result=langs)
        self.assertIn("FEHLT PDF-Bildprüfung für Reader-OCR: pdfimages", missing)
        self.assertIn("OK PDF-Bildprüfung für Reader-OCR: /test/pdfimages", ready)
        self.assertIn("OK OCR-Sprachdaten gelistet: deu, eng", missing)
        self.assertIn("OK OCR-Sprachdaten gelistet: deu, eng", ready)
        probe.assert_called_once()

    def test_healthy_startup_is_quiet_and_never_runs_tesseract(self):
        available = {command for commands in check.TOOLS.values() for command in commands}
        out, process = self.output(available, only_missing=True)
        self.assertEqual("", out)
        process.assert_not_called()

    def test_explicit_check_distinguishes_listed_and_missing_language_data(self):
        msg = subprocess.CompletedProcess([], 0, stdout=b'Available languages in "/private/test-data/" (2):\neng\nosd\n')
        out, call = self.output({"tesseract", "pdftotext"}, only_missing=False, result=msg, family="macos")
        self.assertIn("OK Foto-/Scan-OCR: /test/tesseract", out)
        self.assertIn("OK PDF-Text: /test/pdftotext", out)
        self.assertIn("FEHLT OCR-Sprachdaten: deu", out)
        self.assertIn("brew install tesseract tesseract-lang", out)
        self.assertNotIn("/private/test-data/", out)
        self.assertEqual(["/test/tesseract", "--list-langs"], call.call_args.args[0])
        self.assertEqual(4, call.call_args.kwargs["timeout"])
        both = subprocess.CompletedProcess([], 0, stdout=b"deu\neng\n")
        out, _ = self.output({"tesseract"}, only_missing=False, result=both)
        self.assertIn("OK OCR-Sprachdaten gelistet: deu, eng", out)

    def test_failed_language_probe_is_unknown_not_a_missing_language_claim(self):
        for error in (OSError("synthetic path must not leak"), subprocess.TimeoutExpired(["test"], 4),
                      subprocess.CompletedProcess([], 1, stdout=b"deu\neng\n", stderr=b"synthetic path must not leak")):
            with self.subTest(error=type(error).__name__):
                out, _ = self.output({"tesseract"}, only_missing=False, result=error)
                self.assertIn("UNKLAR OCR-Sprachdaten", out)
                self.assertIn("TESSDATA_PREFIX", out)
                self.assertNotIn("FEHLT OCR-Sprachdaten:", out)
                self.assertNotIn("synthetic path must not leak", out)


if __name__ == "__main__":
    unittest.main()
