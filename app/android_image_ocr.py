"""Private, bounded image OCR adapter configured only by the bundled APK."""
from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import BinaryIO

from .ocr_preprocessing import MAX_OCR_PIXELS


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_EDGE = 2800
MAX_TEXT = 100_000
_FORMATS = ("JPEG", "PNG", "WEBP", "TIFF", "BMP", "GIF", "AVIF")
_slots = threading.BoundedSemaphore(1)
_renderer: Callable[[bytes, int, int], str] | None = None


def register_renderer(renderer: Callable[[bytes, int, int], str]) -> None:
    """This setup hook is called from the trusted Chaquopy startup only."""
    global _renderer
    _renderer = renderer


def configured() -> bool:
    return _renderer is not None


def _raster(source: BinaryIO) -> tuple[bytes, int, int]:
    from PIL import Image, ImageOps

    try:
        if os.fstat(source.fileno()).st_size > MAX_INPUT_BYTES:
            raise RuntimeError("APK OCR input exceeds its size limit")
        Image.init()
        formats = tuple(name for name in _FORMATS if name in Image.OPEN)
        with Image.open(source, formats=formats) as image:
            if image.width < 1 or image.height < 1 or image.width * image.height > MAX_OCR_PIXELS:
                raise RuntimeError("APK OCR input exceeds its pixel limit")
            if image.format == "JPEG":
                # Shrink during JPEG decode when possible, before EXIF rotation.
                image.draft("L", (MAX_EDGE, MAX_EDGE))
            # Preserve original files and ignore any later animation frames.
            ImageOps.exif_transpose(image, in_place=True)
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            if not image.has_transparency_data:
                with image.convert("L") as gray:
                    return gray.tobytes(), gray.width, gray.height
            with image.convert("RGBA") as rgba, rgba.convert("L") as gray:
                with Image.new("L", gray.size, 255) as paper, rgba.getchannel("A") as alpha:
                    paper.paste(gray, mask=alpha)
                    return paper.tobytes(), paper.width, paper.height
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise RuntimeError("APK OCR could not prepare the image") from exc


def recognize(source: BinaryIO) -> str:
    renderer = _renderer
    if renderer is None:
        raise RuntimeError("APK image OCR is unavailable")
    if not _slots.acquire(blocking=False):
        raise RuntimeError("APK image OCR is busy")
    try:
        text = renderer(*_raster(source))
        if not isinstance(text, str) or len(text) > MAX_TEXT:
            raise RuntimeError("APK OCR returned an invalid result")
        return "\n".join(line.rstrip() for line in text.splitlines()).strip()
    finally:
        _slots.release()
