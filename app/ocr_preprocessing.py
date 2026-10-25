"""Bounded grayscale enhancement shared by local OCR workflows."""
from __future__ import annotations

import math
from pathlib import Path


MAX_OCR_PIXELS = 24_000_000
# A4 at 300 dpi is about 3,508 px on its long edge.
OCR_RENDER_EDGE = 3508
OCR_RETRY_CONFIDENCE = 0.72
OCR_RETRY_MIN_CHARACTERS = 80


def needs_ocr_supplement(text: str, confidence: object) -> bool:
    """Flag sparse or low-confidence ML results for a second local OCR pass."""
    if len(text.strip()) < OCR_RETRY_MIN_CHARACTERS:
        return True
    try:
        score = float(confidence)
    except (TypeError, ValueError):
        return True
    return not math.isfinite(score) or score < OCR_RETRY_CONFIDENCE


def prepare_ocr_image(source: Path, target: Path) -> tuple[int, int]:
    """Write a contrast-corrected, lightly sharpened grayscale OCR raster."""
    from PIL import Image, ImageFilter, ImageOps

    with Image.open(source) as image:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > MAX_OCR_PIXELS:
            raise RuntimeError("Gerenderte PDF-Seite überschreitet das OCR-Pixellimit.")
        gray = ImageOps.grayscale(image)
        enhanced = ImageOps.autocontrast(gray, cutoff=0.5)
        enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=1.1, percent=125, threshold=3))
        enhanced.save(target, format="PNG", optimize=True)
    return width, height
