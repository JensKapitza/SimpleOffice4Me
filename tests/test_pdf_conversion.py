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

from app.pdf_conversion import _tesseract_lines, make_grayscale_searchable_pdf


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
                        + (f"5\t1\t1\t1\t1\t1\t100\t100\t200\t50\t95\t{marker}\n"
                           if page != 3 else "1\t1\t0\t0\t0\t0\t0\t0\t300\t300\t-1\t\n")
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
            self.assertEqual("", reader.pages[2].extract_text().strip())
            self.assertEqual(1, len(reader.pages[2].images), "non-text illustration must remain in the export")

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

    def test_real_tesseract_preserves_blank_and_recognizes_raster_text_below_native_header(self):
        tools = {name: shutil.which(name) for name in ("gs", "pdftoppm", "tesseract")}
        if not all(tools.values()):
            self.skipTest("Ghostscript, Poppler and Tesseract are required for this integration test")
        installed = subprocess.run([tools["tesseract"], "--list-langs"], capture_output=True, text=True,
                                   check=True, timeout=5).stdout.splitlines()
        if "eng" not in installed:
            self.skipTest("Tesseract English language data is required")

        from PIL import Image, ImageDraw, ImageFont
        from reportlab.lib.utils import ImageReader

        scan = Image.new("RGB", (1200, 500), "white")
        font = ImageFont.load_default(size=90)
        ImageDraw.Draw(scan).text((90, 170), "SCANNED SAMPLE", fill="black", font=font)
        source = io.BytesIO()
        pdf = Canvas(source, pagesize=(480, 200))
        pdf.showPage()
        pdf.drawString(20, 140, "NATIVE TEXT")
        pdf.showPage()
        pdf.drawImage(ImageReader(scan), 0, 0, width=480, height=150)
        pdf.setFont("Helvetica", 20)
        pdf.drawString(20, 170, "NATIVE HEADER")
        pdf.save()
        original = source.getvalue()
        original_text = PdfReader(io.BytesIO(original)).pages[2].extract_text()
        self.assertIn("NATIVE HEADER", original_text)
        self.assertNotIn("SCANNED SAMPLE", original_text)
        with patch("app.object_vision.analyze_ocr", return_value={"status": "unavailable"}):
            result = make_grayscale_searchable_pdf(original)
        pages = PdfReader(io.BytesIO(result)).pages
        self.assertEqual(3, len(pages))
        self.assertEqual("", pages[0].extract_text().strip())
        self.assertIn("NATIVE TEXT", pages[1].extract_text())
        mixed_page_text = pages[2].extract_text()
        self.assertEqual(1, mixed_page_text.count("NATIVE HEADER"))
        self.assertIn("SCANNED SAMPLE", mixed_page_text)
        self.assertEqual(original, source.getvalue())

    def test_failed_ocr_cannot_create_an_apparently_successful_export(self):
        required = {name: shutil.which(name) for name in ("gs", "pdftoppm")}
        if not all(required.values()):
            self.skipTest("Ghostscript and Poppler are required for this integration test")
        real_run = subprocess.run

        with tempfile.TemporaryDirectory() as temp:
            source = self._source_pdf(Path(temp))

        def fake_run(command, **kwargs):
            if command[0] == "/mock/tesseract":
                return subprocess.CompletedProcess(command, 1, "", "synthetic OCR failure")
            return real_run(command, **kwargs)

        with patch("app.pdf_conversion.shutil.which", side_effect=lambda name: (
            "/mock/tesseract" if name == "tesseract" else required[name]
        )), patch("app.pdf_conversion.subprocess.run", side_effect=fake_run), patch(
            "app.object_vision.analyze_ocr", return_value={"status": "failed"},
        ), self.assertRaisesRegex(RuntimeError, "Konvertierung ist fehlgeschlagen"):
            make_grayscale_searchable_pdf(source)

    def test_invalid_ocr_output_is_not_confused_with_a_successfully_empty_page(self):
        with self.assertRaisesRegex(RuntimeError, "gültigen OCR-Daten"):
            _tesseract_lines("unexpected output")
        with self.assertRaisesRegex(RuntimeError, "unvollständige OCR-Daten"):
            _tesseract_lines("level\tpage_num\tblock_num\tpar_num\tline_num\tleft\ttop\theight\tconf\ttext\n5\t1\n")

    def test_invalid_pdf_fails_before_external_tools_are_called(self):
        with patch("app.pdf_conversion.shutil.which") as which:
            with self.assertRaisesRegex(ValueError, "beschädigt"):
                make_grayscale_searchable_pdf(b"not a PDF")
        which.assert_not_called()


if __name__ == "__main__":
    unittest.main()
