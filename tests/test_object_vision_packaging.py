from __future__ import annotations

from pathlib import Path
import re
import unittest


class ObjectVisionPackagingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]

    def test_ml_ocr_is_optional_project_extra_not_base_dependency(self):
        pyproject = (self.root / "pyproject.toml").read_text(encoding="utf-8")
        base_dependencies = pyproject.split("[project.optional-dependencies]", 1)[0]
        optional_dependencies = pyproject.split("[project.optional-dependencies]", 1)[1]
        self.assertNotIn("rapidocr", base_dependencies.casefold())
        self.assertNotIn("onnxruntime", base_dependencies.casefold())
        self.assertIn("ocr = [", optional_dependencies)
        ocr_extra = optional_dependencies.split("ocr = [", 1)[1].split("]", 1)[0]
        setup = (self.root / "setup.py").read_text(encoding="utf-8")
        # A dependency update must not break an unrelated packaging test.
        # Compare both supported installation formats rather than one historical pin.
        rapidocr_pins = re.findall(r'"(rapidocr==[0-9]+[.][0-9]+[.][0-9]+)"', ocr_extra)
        self.assertEqual(1, len(rapidocr_pins), "OCR extra needs one explicit RapidOCR pin")
        self.assertIn(f'"{rapidocr_pins[0]}"', setup, "setup.py OCR extra must match pyproject.toml")

        web_extra = optional_dependencies.split("web-export = [", 1)[1].split("]", 1)[0]
        playwright_pins = re.findall(r'"(playwright==[0-9]+[.][0-9]+[.][0-9]+)"', web_extra)
        self.assertEqual(1, len(playwright_pins), "Web export needs one explicit Playwright pin")
        self.assertIn(f'"{playwright_pins[0]}"', setup, "setup.py web extra must match pyproject.toml")
        self.assertIn("onnxruntime", ocr_extra.casefold())
        self.assertIn("python_version == '3.10'", ocr_extra)
        self.assertIn("python_version >= '3.11'", ocr_extra)

    def test_desktop_builder_installs_and_bundles_ocr_extra(self):
        builder = (
            self.root / "desktop" / "python-setup" / "build_backend.py"
        ).read_text(encoding="utf-8")
        self.assertIn('f"{REPO}[ocr]"', builder)
        self.assertIn('"--collect-all", "rapidocr"', builder)
        self.assertIn('"--collect-all", "onnxruntime"', builder)

    def test_docker_server_installs_ocr_extra(self):
        dockerfile = (self.root / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("pip install --no-cache-dir '.[ocr]'", dockerfile)

    def test_android_does_not_accidentally_bundle_desktop_onnx_runtime(self):
        gradle = (
            self.root / "android" / "apk" / "app" / "build.gradle"
        ).read_text(encoding="utf-8")
        self.assertNotIn("rapidocr", gradle.casefold())
        self.assertNotIn("onnxruntime", gradle.casefold())

    def test_termux_start_does_not_request_ocr_extra(self):
        start = (self.root / "start.sh").read_text(encoding="utf-8")
        termux_branch = start.split('if [ "$IS_TERMUX" -eq 1 ]; then', 1)[1].split("elif [", 1)[0]
        self.assertNotIn("[ocr]", termux_branch)
        self.assertNotIn("rapidocr", termux_branch.casefold())
        self.assertNotIn("onnxruntime", termux_branch.casefold())


if __name__ == "__main__":
    unittest.main()
