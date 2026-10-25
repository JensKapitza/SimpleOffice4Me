"""Regression coverage for platform-specific desktop OCR runtime dependencies."""
from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

from packaging.requirements import Requirement


class DesktopOcrDependenciesTests(unittest.TestCase):
    def test_onnxruntime_wheel_selection_per_platform(self):
        project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        requirements = [Requirement(value) for value in project["project"]["optional-dependencies"]["ocr"]]
        onnx = [req for req in requirements if req.name == "onnxruntime"]
        self.assertEqual(2, len(onnx))
        for platform, machine, expected in (
            ("darwin", "x86_64", "1.23.2"),
            ("darwin", "arm64", "1.30.0"),
            ("linux", "x86_64", "1.30.0"),
            ("win32", "AMD64", "1.30.0"),
        ):
            with self.subTest(platform=platform, machine=machine):
                selected = [str(req.specifier) for req in onnx if req.marker.evaluate(
                    {"sys_platform": platform, "platform_machine": machine, "extra": "ocr"}
                )]
                self.assertEqual(["==" + expected], selected)

    def test_cryptography_platform_pins(self):
        project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        requirements = [Requirement(value) for value in project["project"]["dependencies"]]
        crypto = [req for req in requirements if req.name == "cryptography"]
        self.assertEqual(2, len(crypto))
        for platform, machine, expected in (
            ("darwin", "x86_64", "==48.0.1"),
            ("darwin", "arm64", "<51,>=48.0.1"),
            ("linux", "x86_64", "<51,>=48.0.1"),
        ):
            with self.subTest(platform=platform, machine=machine):
                selected = [str(req.specifier) for req in crypto if req.marker.evaluate(
                    {"sys_platform": platform, "platform_machine": machine}
                )]
                self.assertEqual([expected], selected)

    def test_rapidocr_remains_enabled(self):
        project = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("rapidocr==3.10.0", project["project"]["optional-dependencies"]["ocr"])


if __name__ == "__main__":
    unittest.main()
