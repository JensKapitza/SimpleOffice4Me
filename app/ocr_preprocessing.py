"""Bounded grayscale enhancement shared by local OCR workflows."""
from __future__ import annotations

import math
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path


MAX_OCR_PIXELS = 24_000_000
_MAX_ORIENTED_JPEG_BYTES = 64 * 1024 * 1024
# A4 at 300 dpi is about 3,508 px on its long edge.
OCR_RENDER_EDGE = 3508
OCR_RETRY_CONFIDENCE = 0.72
OCR_RETRY_MIN_CHARACTERS = 80
_MAX_PNG_OCR_BYTES = 64 * 1024 * 1024


def _normalized_png(source: Path, resources: ExitStack) -> Path:
    try:
        from PIL import Image
    except ImportError:
        return source
    try:
        if source.stat().st_size > _MAX_PNG_OCR_BYTES:
            return source
        # The PNG IHDR records bit depth and color type. Restrict the lookup
        # table to 8-bit grayscale: Pillow scales 2-/4-bit samples as it decodes.
        reader = resources.enter_context(source.open("rb"))
        header = reader.read(26)
        if not header.startswith(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR") or header[24:26] != b"\x08\x00":
            return source
        reader.seek(0)
        image = Image.open(reader, formats=["PNG"])
    except (OSError, ValueError, Image.DecompressionBombError):
        return source
    with image:
        transparent = image.info.get("transparency")
        if (
            image.mode != "L"
            or type(transparent) is not int
            or not 0 <= transparent <= 255
            or image.width * image.height > MAX_OCR_PIXELS
            or image.is_animated
        ):
            return source
        table = list(range(256))
        table[transparent] = 255  # Treat transparent pixels as white paper.
        try:
            directory = resources.enter_context(tempfile.TemporaryDirectory(prefix="simpleoffice-gray-png-ocr-"))
            prepared = Path(directory) / "paper.png"
            with image.point(table) as paper, prepared.open("xb") as output:
                paper.info.clear()
                paper.save(output, format="PNG")
        except (OSError, ValueError) as exc:
            raise RuntimeError("Transparent grayscale PNG could not be prepared for local OCR.") from exc
        return prepared


@contextmanager
def photo_ocr_input(source: Path):
    """Use a bounded, private OCR raster for grayscale PNG color-key transparency."""
    with ExitStack() as resources:
        yield _normalized_png(source, resources) if source.suffix.casefold() == ".png" else source


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


@contextmanager
def jpeg_ocr_input(source: Path):
    """Use a private EXIF-corrected raster only for explicitly oriented JPEG."""
    if source.suffix.casefold() not in {".jpg", ".jpeg", ".jpe"}:
        yield source
        return
    try:
        from PIL import Image, ImageOps
    except ImportError:
        yield source
        return

    try:
        # Large inputs keep the prior path rather than adding a second raster.
        inspect_jpeg = source.stat().st_size <= _MAX_ORIENTED_JPEG_BYTES
        image = Image.open(source, formats=["JPEG"]) if inspect_jpeg else None
    except Image.DecompressionBombError as exc:
        raise RuntimeError("JPEG exceeds the local OCR pixel limit.") from exc
    except (OSError, ValueError):
        # Leave the existing Tesseract handling of unreadable inputs unchanged.
        yield source
        return

    if image is None:
        yield source
        return
    with ExitStack() as resources:
        ocr_source = source
        with image:
            try:
                orientation = image.getexif().get(274)
            except (OSError, ValueError, SyntaxError):
                orientation = None
            if type(orientation) is int and 2 <= orientation <= 8:
                # JPEG can downsample during decoding; do not decode a large RGB
                # image first just to resize or rotate it afterwards.
                width, height = image.size
                scale = 1
                while width * height > MAX_OCR_PIXELS * scale * scale and scale < 8:
                    scale *= 2
                image.draft("L", (max(1, width // scale), max(1, height // scale)))
                if image.width * image.height > MAX_OCR_PIXELS:
                    raise RuntimeError("JPEG exceeds the local OCR pixel limit.")
                temporary = resources.enter_context(tempfile.TemporaryDirectory(prefix="simpleoffice-jpeg-ocr-"))
                ocr_source = Path(temporary) / "oriented.png"
                try:
                    with ImageOps.exif_transpose(image) as oriented, oriented.convert("L") as pixels:
                        pixels.info.clear()
                        with ocr_source.open("xb") as output:
                            pixels.save(output, format="PNG")
                except (OSError, ValueError) as exc:
                    raise RuntimeError("JPEG orientation could not be prepared for local OCR.") from exc
        yield ocr_source
