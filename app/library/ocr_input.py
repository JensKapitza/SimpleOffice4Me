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
        import shutil
        import subprocess
        import tempfile
        from pathlib import Path
        executable = shutil.which("pdftoppm")
        if not executable:
            raise ValueError("PDF-Bildumwandlung ist auf diesem Server nicht verfügbar")
        if not 0 <= page < 2000:
            raise ValueError("PDF-Seite ungültig")
        try:
            with tempfile.TemporaryDirectory(prefix="library-pdf-") as directory:
                source = Path(directory) / "input.pdf"
                prefix = Path(directory) / "page"
                source.write_bytes(data)
                source.chmod(0o600)
                result = subprocess.run([executable, "-f", str(page + 1), "-l", str(page + 1),
                                         "-singlefile", "-scale-to", "2000", "-png", "--",
                                         str(source), str(prefix)], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        timeout=15, check=False)
                target = prefix.with_suffix(".png")
                if result.returncode or not target.is_file() or target.stat().st_size > MAX_UPLOAD:
                    raise ValueError("PDF konnte nicht gerendert werden")
                raster = target.read_bytes()
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("PDF konnte nicht gerendert werden") from exc
        return optimize_page(raster, brighten=brighten, straighten=straighten), "pdf"
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP", "TIFF", "BMP"}:
                raise ValueError("Bildformat wird nicht unterstützt")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Dateiformat wird nicht unterstützt") from exc
    return optimize_page(data, brighten=brighten, straighten=straighten), "image"
