"""Keep a recognized Latin dotless i when copying a searchable PDF."""
from __future__ import annotations

import io
import shutil
import subprocess
import unittest

from pypdf import PdfReader

from app.pdf_conversion import _invisible_text_overlay


class PdfDotlessITest(unittest.TestCase):
    def test_pdf_extractors_preserve_dotless_i_with_other_latin_characters(self):
        names = 'Elif Yılmaz, Deniz Aydın & Müller: 45,20 € • „Café“'
        ordinary = 'Überprüfung: Straße 18, Noël und Renée'
        content = _invisible_text_overlay(
            [(80, 65, 60, names, 1100), (80, 220, 60, ordinary, 1100)],
            720, 640, 1600, 1400,
        )
        extract = PdfReader(io.BytesIO(content)).pages[0].extract_text()
        self.assertIn(names, extract)
        self.assertIn(ordinary, extract)
        if shutil.which("pdftotext"):
            converted = subprocess.run(
                ["pdftotext", "-", "-"], input=content, capture_output=True, check=True, timeout=20,
            ).stdout.decode("utf-8")
            self.assertIn(names, converted)
            self.assertIn(ordinary, converted)

    def test_unsupported_characters_keep_the_existing_fallback_in_mixed_lines(self):
        content = _invisible_text_overlay(
            [(80, 65, 40, "MARK東京", 450), (80, 220, 40, "ı: MARK東京", 450)],
            720, 640, 1600, 1400,
        )
        plain, aliased = PdfReader(io.BytesIO(content)).pages[0].extract_text().splitlines()
        self.assertEqual("ı: " + plain, aliased)
