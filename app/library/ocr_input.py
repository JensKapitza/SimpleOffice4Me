"""File type detection and safe rasterization for OCR preprocessing.

This module deliberately does not perform recognition or persist tags. Existing
OCR and document services remain responsible for those operations.
"""
from __future__ import annotations

import io
from PIL import Image, UnidentifiedImageError
from .image_preprocessing import MAX_UPLOAD, optimize_page

PDF_MAGIC = b"%PDF-"


def optimize_upload(data: bytes, *, brighten: bool = True, straighten: bool = True,
                    page: int = 0) -> tuple[bytes, str]:
    """Return an optimized PNG and the detected input format."""
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("Datei fehlt oder überschreitet 12 MiB")
    if data.startswith(PDF_MAGIC):
        try:
            import fitz  # PyMuPDF; optional and subject to deployment license review
        except ImportError as exc:
            raise ValueError("PDF-Bildumwandlung ist auf diesem Server nicht verfügbar") from exc
        try:
            with fitz.open(stream=data, filetype="pdf") as document:
                if document.needs_pass:
                    raise ValueError("Passwortgeschützte PDF-Datei")
                if document.page_count < 1 or document.page_count > 2000:
                    raise ValueError("Ungültige PDF-Seitenzahl")
                if page < 0 or page >= document.page_count:
                    raise ValueError("PDF-Seite existiert nicht")
                pdf_page = document.load_page(page)
                # Cap raster dimensions before allocating large images.
                bounds = pdf_page.rect
                scale = min(2.0, 2800 / max(1, float(bounds.width), float(bounds.height)))
                if scale <= 0:
                    raise ValueError("Ungültige Seitengröße")
                pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                if pixmap.width * pixmap.height > 24_000_000:
                    raise ValueError("PDF-Seite zu groß")
                raster = pixmap.tobytes("png")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDF konnte nicht gerendert werden") from exc
        return optimize_page(raster, brighten=brighten, straighten=straighten), "pdf"
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP", "TIFF", "BMP"}:
                raise ValueError("Bildformat wird nicht unterstützt")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Dateiformat wird nicht unterstützt") from exc
    return optimize_page(data, brighten=brighten, straighten=straighten), "image"
