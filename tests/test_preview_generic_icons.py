"""Regression tests for Pillow-only generic document thumbnails."""
from pathlib import Path

from PIL import Image

from app.preview_service import PreviewService


def test_generic_filetype_thumbnails(tmp_path: Path):
    for name in (
        "sound.mp3", "archive.zip", "comic.cbz", "book.epub", "notes.txt", "unknown.blob",
    ):
        source = tmp_path / name
        source.write_bytes(b"sample")
        output = tmp_path / (name + ".webp")
        PreviewService._generic_thumbnail(source, output)
        with Image.open(output) as preview:
            assert preview.format == "WEBP"
            assert preview.size == (640, 420)


def test_generic_supports_files_without_known_suffix(tmp_path: Path):
    unknown = tmp_path / "file.unknown"
    unknown.write_bytes(b"sample")
    service = PreviewService(tmp_path)
    assert service.supports(unknown)
    assert not service.supports(tmp_path / "missing.unknown")
