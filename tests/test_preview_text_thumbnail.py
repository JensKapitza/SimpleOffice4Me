"""Regression coverage for dependency-light text preview rendering."""

from pathlib import Path

import pytest
from PIL import Image

from app.preview_service import PreviewService, SUPPORTED_SUFFIXES


@pytest.mark.parametrize("suffix", [".txt", ".md", ".csv", ".json", ".xml", ".py", ".log"])
def test_text_preview_uses_stdlib_and_pillow(tmp_path: Path, suffix: str):
    source = tmp_path / ("example" + suffix)
    source.write_text("<script>alert('never execute')</script>\\n" + "hello world\\n" * 80, encoding="utf-8")
    output = tmp_path / "thumbnail.webp"
    service = PreviewService.__new__(PreviewService)
    service._text_thumbnail(source, output)
    assert suffix in SUPPORTED_SUFFIXES
    with Image.open(output) as image:
        assert image.format == "WEBP"
        assert image.size == (1100, 800)


def test_binary_input_rejected(tmp_path: Path):
    source = tmp_path / "unsafe.txt"
    source.write_bytes(b"hello\\x00binary")
    service = PreviewService.__new__(PreviewService)
    with pytest.raises(ValueError, match="binary"):
        service._text_thumbnail(source, tmp_path / "thumbnail.webp")
