"""Bounded conversion of ordered local JPEG/PNG pages into a new PDF."""

from __future__ import annotations

import io
import json
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

MIB = 1024 * 1024
MAX_REQUEST = 32 * MIB
MAX_PAGE = 12 * MIB
MAX_PAGES = 24
MAX_PAGE_PIXELS = 16_000_000
MAX_TOTAL_PIXELS = 64_000_000
MAX_LONG_EDGE = 2400
MAX_JPEG_TOTAL = 24 * MIB
ALLOWED_FORMATS = ("JPEG", "PNG")
ROTATIONS = (None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_90)


@dataclass(frozen=True)
class ScanError(ValueError):
    code: str
    status: int = 400


def ordered_pages(raw: str, count: int) -> list[tuple[int, int]]:
    if not 1 <= count <= MAX_PAGES:
        raise ScanError("count")
    if not raw:
        return [(index, 0) for index in range(count)]
    if len(raw) > 1200:
        raise ScanError("order")
    try:
        order = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise ScanError("order") from exc
    if not isinstance(order, list) or len(order) != count:
        raise ScanError("order")
    result = []
    for page in order:
        if not isinstance(page, list) or len(page) != 2 or any(type(part) is not int for part in page):
            raise ScanError("order")
        if page[0] not in range(count) or page[1] not in range(4):
            raise ScanError("order")
        result.append((page[0], page[1]))
    if len({index for index, _rotation in result}) != count:
        raise ScanError("order")
    return result


def _read_page(upload, turns: int, available_pixels: int) -> tuple[io.BytesIO, tuple[int, int], int]:
    contents = upload.stream.read(MAX_PAGE + 1)
    if len(contents) > MAX_PAGE:
        raise ScanError("size", 413)
    try:
        with Image.open(io.BytesIO(contents), formats=ALLOWED_FORMATS) as source:
            pixels = source.width * source.height
            if pixels > min(MAX_PAGE_PIXELS, available_pixels):
                raise ScanError("resolution", 413)
            if getattr(source, "n_frames", 1) != 1:
                raise ScanError("image")
            normalized = ImageOps.exif_transpose(source)
            normalized.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.Resampling.LANCZOS)
            if turns:
                normalized = normalized.transpose(ROTATIONS[turns])
            rgba = normalized.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
            jpeg = io.BytesIO()
            rgb.save(jpeg, format="JPEG", quality=86)
            return jpeg, rgb.size, pixels
    except ScanError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ScanError("image") from exc


def build_scan_pdf(files: Sequence, order: list[tuple[int, int]]) -> io.BytesIO:
    result = io.BytesIO()
    book = canvas.Canvas(result, pagesize=A4, pageCompression=1, invariant=1)
    book.setTitle("Book scan")
    jpeg_total = used_pixels = 0
    for index, rotation in order:
        jpeg, (width, height), pixels = _read_page(files[index], rotation, MAX_TOTAL_PIXELS - used_pixels)
        used_pixels += pixels
        jpeg_total += jpeg.getbuffer().nbytes
        if jpeg_total > MAX_JPEG_TOTAL:
            raise ScanError("size", 413)
        page_w, page_h = landscape(A4) if width > height else A4
        book.setPageSize((page_w, page_h))
        margin = 12
        scale = min((page_w - 2 * margin) / width, (page_h - 2 * margin) / height)
        draw_w, draw_h = width * scale, height * scale
        book.drawImage(ImageReader(jpeg), (page_w - draw_w) / 2, (page_h - draw_h) / 2, draw_w, draw_h)
        book.showPage()
    book.save()
    if result.tell() > MAX_REQUEST:
        raise ScanError("size", 413)
    result.seek(0)
    return result
