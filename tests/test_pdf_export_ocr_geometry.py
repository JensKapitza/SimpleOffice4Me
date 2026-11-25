"""Poppler selection must follow the ink in a real searchable-copy export."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from app import app
from app.document_store import DocumentStore
from app.pdf_conversion import _rapidocr_lines, _tesseract_lines

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:
    from http_api_fixture import PublicHttpTestCase


def mixed_source() -> bytes:
    output = io.BytesIO()
    pdf = Canvas(output, pagesize=(600, 430))
    font = ImageFont.truetype("DejaVuSans.ttf", size=80)

    def scan(label: str) -> ImageReader:
        image = Image.new("RGB", (1000, 160), "white")
        ImageDraw.Draw(image).text((35, 20), label, font=font, fill="#243F8E")
        return ImageReader(image)

    pdf.translate(600, 0)
    pdf.rotate(90)
    pdf.drawImage(scan("ROTATIONSBELEG"), 29, 420, width=340, height=54)
    pdf.showPage()
    pdf.setFont("Helvetica", 24)
    pdf.drawString(35, 320, "NATIVTEXT")
    pdf.drawImage(scan("ARCHIVSCAN"), 42, 100, width=340, height=54)
    pdf.showPage()
    pdf.save()
    pages = PdfReader(io.BytesIO(output.getvalue())).pages
    pages[0].rotate(90)
    pages[0].cropbox = RectangleObject((45, 26, 555, 390))
    result = io.BytesIO()
    writer = PdfWriter()
    for page in pages:
        writer.add_page(page)
    writer.write(result)
    return result.getvalue()


def rendered_ink(file: Path, prefix: Path) -> tuple[tuple[int, int], tuple[int, int, int, int]]:
    subprocess.run(
        ["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-r", "72", "-cropbox",
         "-gray", "-png", str(file), str(prefix)],
        capture_output=True, check=True, timeout=20,
    )
    with Image.open(prefix.with_suffix(".png")) as image:
        with image.point(lambda value: 255 if value < 170 else 0) as dark:
            box = dark.getbbox()
        assert box is not None
        return image.size, box


class PdfExportOcrGeometryTests(PublicHttpTestCase):
    def test_real_rotated_cropped_mixed_reader_export_lines_match_ink(self):
        if not all(shutil.which(tool) for tool in ("gs", "pdftoppm", "pdftotext", "tesseract")):
            self.skipTest("Real Ghostscript, Poppler, and Tesseract tools required")
        try:
            original = mixed_source()
        except OSError:
            self.skipTest("Synthetic scan requires DejaVu Sans")
        store = DocumentStore(app.config["DOCUMENT_ROOT"])
        document = store.import_upload(io.BytesIO(original), "synthetic-mixed.pdf", "owner")
        home = f'/documents/reader/{document["document_id"]}'
        dom = BeautifulSoup(self.client.get(home).data, "html.parser")
        action = dom.select_one('form[action$="/pdf/grayscale"]')["action"]
        self.assertEqual(403, self.client.post(action).status_code)
        response = self.client.post(action, headers=self._csrf_headers(self.client, home))
        self.assertEqual(200, response.status_code)
        self.assertEqual("application/pdf", response.mimetype)
        self.assertEqual(original, (store.root / document["last_path"]).read_bytes())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "base.pdf"
            result = root / "ocr.pdf"
            source.write_bytes(original)
            result.write_bytes(response.data)
            xml = root / "words.xhtml"
            subprocess.run(
                ["pdftotext", "-bbox", "-cropbox", str(result), str(xml)],
                capture_output=True, check=True, timeout=20,
            )
            # pdftotext emits no user-provided XML; BeautifulSoup already used
            # for the browser response and can read the local bbox XHTML too.
            words_dom = BeautifulSoup(xml.read_text(encoding="utf-8"), "html.parser")
            pages = words_dom.select("page")
            self.assertEqual(2, len(pages))
            word = next(item for item in pages[0].select("word") if item.get_text() == "ROTATIONSBELEG")
            page_two = [item.get_text() for item in pages[1].select("word")]
            self.assertEqual(1, page_two.count("NATIVTEXT"))
            self.assertIn("ARCHIVSCAN", page_two)
            source_size, source_ink = rendered_ink(source, root / "source")
            result_size, result_ink = rendered_ink(result, root / "export")
            self.assertEqual(source_size, result_size)
            self.assertEqual(source_ink, result_ink)
            self.assertAlmostEqual(source_ink[0], float(word["xmin"]), delta=8)
            self.assertAlmostEqual(source_ink[2], float(word["xmax"]), delta=8)

    def test_backend_widths_include_all_words_and_ignore_unavailable_width(self):
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        rows = "5\t1\t1\t1\t1\t1\t15\t20\t35\t12\t97\tFirst\n5\t1\t1\t1\t1\t2\t72\t20\t20\t12\t97\tSecond\n"
        self.assertEqual([(15.0, 20.0, 12.0, "First Second", 77.0)], _tesseract_lines(header + rows))
        self.assertEqual(0.0, _tesseract_lines(header + rows.replace("\t20\t12\t97\tSecond", "\tnan\t12\t97\tSecond"))[0][4])
        self.assertEqual([(25.0, 10.0, 20.0, "Local vision", 150.0)], _rapidocr_lines({"blocks": [{
            "text": "Local vision", "confidence": 0.9, "box": [[25, 10], [175, 10], [175, 30], [25, 30]],
        }]}, 300, 300))
