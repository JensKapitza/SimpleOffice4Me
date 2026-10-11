"""Deterministic, non-AI preprocessing for photographed OCR pages."""
from __future__ import annotations

import io
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

MAX_UPLOAD = 12 * 1024 * 1024
MAX_PIXELS = 24_000_000


def optimize_page(data: bytes, *, brighten: bool = True, straighten: bool = True) -> bytes:
    """Normalize orientation and improve OCR contrast without changing the original."""
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("Bild fehlt oder überschreitet 12 MiB")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("Bildauflösung überschreitet das Limit")
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Bild konnte nicht sicher verarbeitet werden") from exc
    # EXIF orientation is authoritative. Avoid guessing 90/180 degree text orientation.
    if straighten:
        # Estimate only small page skew from high-contrast horizontal edges.
        import math
        gray = ImageOps.grayscale(image)
        gray.thumbnail((900, 900))
        edges = gray.filter(ImageFilter.FIND_EDGES)
        best_angle, best_score = 0.0, 0.0
        for tenths in range(-30, 31, 5):
            angle = tenths / 10.0
            rotated = edges.rotate(angle, resample=Image.Resampling.BILINEAR, expand=False)
            pixels = rotated.point(lambda x: 255 if x > 65 else 0)
            hist = [sum(1 for v in range(pixels.width) if pixels.getpixel((v, y)) > 0) for y in range(0, pixels.height, 3)]
            if not hist:
                continue
            mean = sum(hist) / len(hist)
            score = sum((v - mean) ** 2 for v in hist) / len(hist)
            if score > best_score:
                best_score, best_angle = score, angle
        if abs(best_angle) >= 0.5 and best_score > 20:
            image = image.rotate(best_angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
    if brighten:
        image = ImageOps.autocontrast(image, cutoff=1, preserve_tone=True)
        image = ImageEnhance.Contrast(image).enhance(1.12)
        image = ImageEnhance.Sharpness(image).enhance(1.15)
    result = io.BytesIO()
    image.save(result, format="PNG", optimize=True)
    return result.getvalue()
