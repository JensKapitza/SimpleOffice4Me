from __future__ import annotations

from pathlib import Path
import unittest


class ObjectVisionPackagingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).parents[1]

    def test_desktop_dependencies_include_local_ml_ocr(self):
        pyproject = (self.root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"rapidocr==3.9.2"', pyproject)
        self.assertIn('"onnxruntime==1.23.2; python_version == \'3.10\'"', pyproject)
        self.assertIn('"onnxruntime==1.24.2; python_version >= \'3.11\'"', pyproject)

    def test_pyinstaller_explicitly_bundles_lazy_ml_runtime(self):
        builder = (
            self.root / "desktop" / "python-setup" / "build_backend.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"--collect-all", "rapidocr"', builder)
        self.assertIn('"--collect-all", "onnxruntime"', builder)

    def test_android_does_not_accidentally_bundle_desktop_onnx_runtime(self):
        gradle = (
            self.root / "android" / "apk" / "app" / "build.gradle"
        ).read_text(encoding="utf-8")
        self.assertNotIn("rapidocr", gradle.casefold())
        self.assertNotIn("onnxruntime", gradle.casefold())


if __name__ == "__main__":
    unittest.main()
