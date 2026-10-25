"""Create grayscale PDF downloads with optional invisible OCR text layers."""
from __future__ import annotations

import csv
import io
import math
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from .document_store_core import ocr_subprocess_environment
from .ocr_preprocessing import OCR_RENDER_EDGE, needs_ocr_supplement, prepare_ocr_image
from .pdf_limits import MAX_PDF_BYTES

MAX_OUTPUT_BYTES = MAX_PDF_BYTES
MAX_RENDER_EDGE = OCR_RENDER_EDGE


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


def _tesseract_lines(tsv: str) -> list[tuple[float, float, float, str]]:
    """Convert Tesseract word geometry into positioned text lines."""
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

    positioned: list[tuple[float, float, float, str]] = []
    for words in lines.values():
        words.sort(key=lambda word: word[0])
        positioned.append((
            float(words[0][0]), min(word[2] for word in words),
            max(word[3] for word in words),
            " ".join(word[1] for word in words),
        ))
    return positioned


def _ocr_overlay(
    tsv: str, width: float, height: float, image_width: int, image_height: int,
    existing_text: str = "",
) -> bytes:
    """Build an invisible overlay from Tesseract's positioned words."""
    return _invisible_text_overlay(
        _tesseract_lines(tsv), width, height, image_width, image_height, existing_text,
    )


def _rapidocr_lines(result: dict, image_width: int, image_height: int) -> list[tuple[float, float, float, str]]:
    """Extract bounded line text and its box from the local RapidOCR result."""
    lines: list[tuple[float, float, float, str]] = []
    for block in result.get("blocks", [])[:250]:
        if not isinstance(block, dict):
            continue
        text = " ".join(str(block.get("text") or "").split())[:1000]
        box = block.get("box")
        if not text or not isinstance(box, list) or len(box) < 4:
            continue
        try:
            points = [(float(point[0]), float(point[1])) for point in box if len(point) >= 2]
        except (TypeError, ValueError, IndexError):
            continue
        if len(points) < 4 or any(not math.isfinite(value) for point in points for value in point):
            continue
        left = max(0.0, min(point[0] for point in points))
        right = min(float(image_width), max(point[0] for point in points))
        top = max(0.0, min(point[1] for point in points))
        bottom = min(float(image_height), max(point[1] for point in points))
        if right <= left or bottom <= top:
            continue
        try:
            confidence = float(block.get("confidence"))
        except (TypeError, ValueError):
            confidence = 1.0
        if not math.isfinite(confidence) or confidence < 0.25:
            continue
        lines.append((left, top, bottom - top, text))
    return lines


def _invisible_text_overlay(
    lines: list[tuple[float, float, float, str]], width: float, height: float,
    image_width: int, image_height: int, existing_text: str = "",
) -> bytes:
    """Build a PDF text page whose glyphs are searchable but not painted."""
    if width <= 0 or height <= 0 or image_width <= 0 or image_height <= 0:
        raise ValueError("Ungültige PDF- oder OCR-Seitengröße")
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(width, height), pageCompression=1)
    known_lines = {" ".join(line.split()).casefold() for line in existing_text.splitlines() if line.strip()}
    # PDF text rendering mode 3 keeps OCR available to search/copy tools while
    # ensuring that no recognized glyph is painted over the scanned page.
    invisible_text = pdf.beginText()
    invisible_text.setTextRenderMode(3)
    written_lines = set(known_lines)
    for left, top, box_height, line in sorted(lines, key=lambda item: (item[1], item[0])):
        line = " ".join(line.split())[:2000]
        normalized = " ".join(line.split()).casefold()
        if not normalized or normalized in written_lines:
            continue
        written_lines.add(normalized)
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
    """Return a grayscale PDF with local OCR text behind each visible page.

    The source is only read. Any parse, rendering, or OCR failure rejects the
    whole conversion, so callers never download a silently partial result.
    """
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_PDF_BYTES:
        raise ValueError("Das PDF muss zwischen 1 Byte und 1 GiB groß sein.")
    source_reader = _checked_pdf(payload)
    gs = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
    renderer = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not gs:
        raise RuntimeError("Ghostscript für die Schwarz-Weiß-Konvertierung ist nicht installiert.")
    if not renderer:
        raise RuntimeError("Poppler zum Rendern der PDF-Seiten für die OCR ist nicht installiert.")
    if not tesseract:
        from importlib.util import find_spec

        if not find_spec("rapidocr") or not find_spec("onnxruntime"):
            raise RuntimeError("Für die lokale PDF-OCR wird Tesseract oder das RapidOCR/ONNX-Extra benötigt.")

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
            raise RuntimeError("Das erzeugte PDF ist leer oder überschreitet 1 GiB.")
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
            ocr_image_path = root / f"ocr-{page_number:06d}.png"
            try:
                page_width = float(page.mediabox.width)
                page_height = float(page.mediabox.height)
                image_width, image_height = prepare_ocr_image(image_path, ocr_image_path)
                from .object_vision import analyze_ocr

                ml_result = analyze_ocr(ocr_image_path, fallback_tesseract=False)
                positioned = _rapidocr_lines(ml_result, image_width, image_height)
                ml_text = str(ml_result.get("text") or "").strip()
                ml_has_positioned_text = ml_result.get("status") == "completed" and bool(positioned)
                should_supplement = needs_ocr_supplement(ml_text, ml_result.get("confidence"))
                if not ml_has_positioned_text or (tesseract and should_supplement):
                    if not tesseract and not ml_has_positioned_text:
                        raise RuntimeError(
                            f"Lokale ML-OCR für PDF-Seite {page_number} nicht verfügbar; "
                            "Tesseract ist nicht installiert."
                        )
                    if tesseract:
                        try:
                            try:
                                result = _run(
                                    [tesseract, str(ocr_image_path), "stdout", "-l", "deu+eng", "tsv"],
                                    timeout=30, env=ocr_subprocess_environment(),
                                )
                            except RuntimeError:
                                result = _run(
                                    [tesseract, str(ocr_image_path), "stdout", "-l", "eng", "tsv"],
                                    timeout=30, env=ocr_subprocess_environment(),
                                )
                            positioned.extend(_tesseract_lines(result.stdout))
                        except RuntimeError:
                            if not ml_has_positioned_text:
                                raise
                if not positioned:
                    raise RuntimeError(f"Für PDF-Seite {page_number} wurde kein OCR-Text erkannt.")
                overlay_bytes = _invisible_text_overlay(
                    positioned, page_width, page_height, image_width, image_height,
                    existing_text=native_text,
                )
                writer.add_page(page)
                overlay = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
                writer.pages[-1].merge_page(overlay, over=False)
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"OCR für PDF-Seite {page_number} ist fehlgeschlagen.") from exc
            finally:
                image_path.unlink(missing_ok=True)
                ocr_image_path.unlink(missing_ok=True)

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
            raise RuntimeError("Das erzeugte PDF überschreitet 1 GiB.")
        verified = _checked_pdf(result)
        if len(verified.pages) != len(source_reader.pages):
            raise RuntimeError("Das fertige PDF konnte nicht vollständig validiert werden.")
        return result
