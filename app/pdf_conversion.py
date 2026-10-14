"""Create grayscale PDF downloads with optional invisible OCR text layers."""
from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from .document_store_core import ocr_subprocess_environment

MAX_PDF_BYTES = 200 * 1024 * 1024
MAX_OUTPUT_BYTES = 400 * 1024 * 1024
MAX_RENDER_EDGE = 1800


def _checked_pdf(payload: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(payload), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ValueError("Passwortgeschützte PDFs können nicht konvertiert werden.")
            except Exception as exc:
                raise ValueError("Passwortgeschützte PDFs können nicht konvertiert werden.") from exc
        if not reader.pages:
            raise ValueError("Das PDF enthält keine Seiten.")
        return reader
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Das PDF ist beschädigt oder kann nicht gelesen werden.") from exc


def _run(
    command: list[str], *, timeout: int, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout, check=False, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Die PDF-Konvertierung hat das Zeitlimit überschritten.") from exc
    except OSError as exc:
        raise RuntimeError("Ein benötigtes PDF-Werkzeug konnte nicht gestartet werden.") from exc
    if result.returncode:
        raise RuntimeError("Die PDF-Konvertierung ist fehlgeschlagen.")
    return result


def _ocr_overlay(
    tsv: str, width: float, height: float, image_width: int, image_height: int,
    existing_text: str = "",
) -> bytes:
    """Build a text-only page whose glyphs use PDF rendering mode 3 (invisible)."""
    lines: dict[tuple[str, str, str, str], list[tuple[int, str, float, float, float]]] = defaultdict(list)
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t"):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        try:
            confidence = float(row.get("conf", "-1"))
            left = float(row.get("left", "0"))
            top = float(row.get("top", "0"))
            box_height = float(row.get("height", "0"))
        except ValueError:
            continue
        if confidence < 0 or box_height <= 0:
            continue
        key = (row.get("page_num", ""), row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))
        lines[key].append((int(left), row["text"].strip(), top, box_height, confidence))

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(width, height), pageCompression=1)
    known_lines = {" ".join(line.split()).casefold() for line in existing_text.splitlines() if line.strip()}
    # PDF text rendering mode 3 keeps OCR available to search/copy tools while
    # ensuring that no recognized glyph is painted over the scanned page.
    invisible_text = pdf.beginText()
    invisible_text.setTextRenderMode(3)
    for words in lines.values():
        words.sort(key=lambda word: word[0])
        left = words[0][0]
        top = min(word[2] for word in words)
        box_height = max(word[3] for word in words)
        line = " ".join(word[1] for word in words)
        if " ".join(line.split()).casefold() in known_lines:
            continue
        x = left * width / image_width
        y = height - (top + box_height) * height / image_height
        font_size = max(1.0, min(36.0, box_height * height / image_height))
        invisible_text.setTextOrigin(x, y)
        invisible_text.setFont("Helvetica", font_size)
        invisible_text.textLine(line)
    pdf.drawText(invisible_text)
    pdf.showPage()
    pdf.save()
    return stream.getvalue()


def make_grayscale_searchable_pdf(payload: bytes) -> bytes:
    """Return a grayscale PDF, adding invisible OCR text only to scanned pages.

    The source is only read. Any parse, rendering, or OCR failure rejects the
    whole conversion, so callers never download a silently partial result.
    """
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_PDF_BYTES:
        raise ValueError("Das PDF muss zwischen 1 Byte und 200 MiB groß sein.")
    source_reader = _checked_pdf(payload)
    gs = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
    renderer = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not gs:
        raise RuntimeError("Ghostscript für die Schwarz-Weiß-Konvertierung ist nicht installiert.")
    if not renderer or not tesseract:
        raise RuntimeError("Poppler und Tesseract für die vollständige OCR sind nicht installiert.")

    with tempfile.TemporaryDirectory(prefix="simpleoffice-pdf-gray-") as temp:
        root = Path(temp)
        original = root / "source.pdf"
        grayscale = root / "grayscale.pdf"
        original.write_bytes(payload)
        _run(
            [
                gs, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dQUIET", "-dPDFSTOPONERROR",
                "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7",
                "-sColorConversionStrategy=Gray", "-dProcessColorModel=/DeviceGray",
                "-dAutoRotatePages=/None", f"-sOutputFile={grayscale}", str(original),
            ],
            timeout=900,
        )
        if not grayscale.is_file() or grayscale.stat().st_size > MAX_OUTPUT_BYTES:
            raise RuntimeError("Das erzeugte PDF ist leer oder überschreitet 400 MiB.")
        try:
            converted_reader = PdfReader(str(grayscale), strict=False)
            if len(converted_reader.pages) != len(source_reader.pages):
                raise RuntimeError("Die Konvertierung hat die PDF-Seitenzahl verändert.")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Das Schwarz-Weiß-PDF konnte nicht geprüft werden.") from exc

        writer = PdfWriter()
        for page_number, page in enumerate(converted_reader.pages, start=1):
            try:
                native_text = page.extract_text() or ""
            except Exception:
                # OCR remains the recovery path when a page's existing text
                # operators cannot be decoded safely by the PDF text parser.
                native_text = ""
            prefix = root / f"render-{page_number:06d}"
            _run(
                [
                    renderer, "-f", str(page_number), "-l", str(page_number),
                    "-scale-to", str(MAX_RENDER_EDGE), "-gray", "-png", "--",
                    str(grayscale), str(prefix),
                ],
                timeout=90,
            )
            images = sorted(root.glob(f"{prefix.name}-*.png"))
            if len(images) != 1:
                raise RuntimeError(f"PDF-Seite {page_number} konnte nicht gerendert werden.")
            image_path = images[0]
            try:
                from PIL import Image

                with Image.open(image_path) as image:
                    image_width, image_height = image.size
                try:
                    result = _run(
                        [tesseract, str(image_path), "stdout", "-l", "deu+eng", "tsv"],
                        timeout=30, env=ocr_subprocess_environment(),
                    )
                except RuntimeError:
                    result = _run(
                        [tesseract, str(image_path), "stdout", "-l", "eng", "tsv"],
                        timeout=30, env=ocr_subprocess_environment(),
                    )
                page_width = float(page.mediabox.width)
                page_height = float(page.mediabox.height)
                overlay_bytes = _ocr_overlay(
                    result.stdout, page_width, page_height, image_width, image_height,
                    existing_text=native_text,
                )
                writer.add_page(page)
                overlay = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
                writer.pages[-1].merge_page(overlay, over=True)
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"OCR für PDF-Seite {page_number} ist fehlgeschlagen.") from exc
            finally:
                image_path.unlink(missing_ok=True)

        output = io.BytesIO()
        metadata = {
            str(key): str(value)
            for key, value in (converted_reader.metadata or {}).items()
            if value is not None
        }
        metadata["/Producer"] = "SimpleOffice4Me PDF grayscale and OCR export"
        writer.add_metadata(metadata)
        writer.write(output)
        result = output.getvalue()
        if len(result) > MAX_OUTPUT_BYTES:
            raise RuntimeError("Das erzeugte PDF überschreitet 400 MiB.")
        verified = _checked_pdf(result)
        if len(verified.pages) != len(source_reader.pages):
            raise RuntimeError("Das fertige PDF konnte nicht vollständig validiert werden.")
        return result
