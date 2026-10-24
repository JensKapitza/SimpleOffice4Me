"""Regression coverage for dependency-light text preview rendering."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.preview_service import PreviewService, SUPPORTED_SUFFIXES


class PreviewTextThumbnailTests(unittest.TestCase):
    def test_text_preview_uses_stdlib_and_pillow(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for suffix in (".txt", ".md", ".csv", ".json", ".xml", ".py", ".log"):
                with self.subTest(suffix=suffix):
                    source = root / ("example" + suffix)
                    source.write_text(
                        "<script>alert('never execute')</script>\n" + "hello world\n" * 80,
                        encoding="utf-8",
                    )
                    output = root / (suffix.removeprefix(".") + ".webp")
                    service = PreviewService.__new__(PreviewService)
                    service._text_thumbnail(source, output)
                    self.assertIn(suffix, SUPPORTED_SUFFIXES)
                    with Image.open(output) as image:
                        self.assertEqual("WEBP", image.format)
                        self.assertEqual((1100, 800), image.size)

    def test_binary_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "unsafe.txt"
            source.write_bytes(b"hello\x00binary")
            service = PreviewService.__new__(PreviewService)
            with self.assertRaisesRegex(ValueError, "binary"):
                service._text_thumbnail(source, root / "thumbnail.webp")


if __name__ == "__main__":
    unittest.main()
