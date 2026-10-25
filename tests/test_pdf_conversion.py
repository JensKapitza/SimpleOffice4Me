from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from app.pdf_conversion import make_grayscale_searchable_pdf


class GrayscalePdfConversionTests(unittest.TestCase):
    @staticmethod
    def _source_pdf(path: Path) -> bytes:
        from PIL import Image

        image_path = path / "red-page.png"
        Image.new("RGB", (160, 160), (240, 20, 40)).save(image_path)
        output = io.BytesIO()
        pdf = Canvas(output, pagesize=(300, 300))
        pdf.drawImage(str(image_path), 0, 0, width=300, height=300)
        pdf.showPage()
        pdf.setFont("Helvetica", 18)
        pdf.drawString(30, 240, "NATIVE VECTOR PAGE")
        pdf.showPage()
        pdf.drawImage(str(image_path), 0, 0, width=300, height=300)
        pdf.save()
        return output.getvalue()

    def test_grayscale_download_is_searchable_and_ocr_layer_does_not_render(self):
        actual_which = shutil.which
        required = {name: actual_which(name) for name in ("gs", "pdftoppm")}
        if not all(required.values()):
            self.skipTest("Ghostscript and Poppler are required for the PDF conversion integration test")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._source_pdf(root)
            original_run = subprocess.run
            ocr_pages: list[int] = []

            def fake_which(command: str):
                if command == "tesseract":
                    return "/mock/tesseract"
                if command in required:
                    return required[command]
                return actual_which(command)

            def fake_run(command, **kwargs):
                if command[0] == "/mock/tesseract":
                    filename = Path(command[1]).name
                    page = int(filename.split("-")[1].split(".", 1)[0])
                    ocr_pages.append(page)
                    marker = f"HIDDEN OCR PAGE {page}"
                    tsv = (
                        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
                        f"5\t1\t1\t1\t1\t1\t100\t100\t200\t50\t95\t{marker}\n"
                    )
                    return subprocess.CompletedProcess(command, 0, tsv, "")
                return original_run(command, **kwargs)

            unavailable_ml = {
                "engine": "rapidocr",
                "status": "unavailable",
                "text": "",
                "confidence": None,
                "blocks": [],
                "error": "RapidOCR not installed in this regression test",
            }
            with patch("app.pdf_conversion.shutil.which", side_effect=fake_which), patch(
                "app.pdf_conversion.subprocess.run", side_effect=fake_run,
            ), patch("app.object_vision.analyze_ocr", return_value=unavailable_ml):
                result = make_grayscale_searchable_pdf(source)

            reader = PdfReader(io.BytesIO(result))
            self.assertEqual(3, len(reader.pages))
            self.assertEqual([1, 2, 3], ocr_pages)
            self.assertIn("HIDDEN OCR PAGE 1", reader.pages[0].extract_text())
            self.assertIn("NATIVE VECTOR PAGE", reader.pages[1].extract_text())
            self.assertIn("HIDDEN OCR PAGE 2", reader.pages[1].extract_text())
            self.assertIn("HIDDEN OCR PAGE 3", reader.pages[2].extract_text())

            converted = root / "converted.pdf"
            converted.write_bytes(result)
            rendered_prefix = root / "preview"
            render = original_run(
                [required["pdftoppm"], "-f", "1", "-l", "1", "-scale-to", "600", "-png", str(converted), str(rendered_prefix)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, render.returncode, render.stderr)
            from PIL import Image

            with Image.open(root / "preview-1.png") as rendered:
                rgb = rendered.convert("RGB")
                pixel = rgb.getpixel((rgb.width // 2, rgb.height // 2))
                self.assertEqual(pixel[0], pixel[1])
                self.assertEqual(pixel[1], pixel[2])
                # The OCR bounding box is near the top-left. A uniform patch
                # there proves the extracted words were not painted visibly.
                patch_pixels = rgb.crop((20, 20, min(250, rgb.width), min(120, rgb.height)))
                self.assertTrue(all(low == high for low, high in patch_pixels.getextrema()))

    def test_invalid_pdf_fails_before_external_tools_are_called(self):
        with patch("app.pdf_conversion.shutil.which") as which:
            with self.assertRaisesRegex(ValueError, "beschädigt"):
                make_grayscale_searchable_pdf(b"not a PDF")
        which.assert_not_called()


if __name__ == "__main__":
    unittest.main()
