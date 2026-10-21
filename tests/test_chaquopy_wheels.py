"""Offline tests for Android native wheel compatibility preflight."""
import unittest
from pathlib import Path
from tools.check_chaquopy_wheels import compatible_wheels, pinned_versions


class ChaquopyWheelsTests(unittest.TestCase):
    def test_experimental_builder_pins_cpython_315_android_arm64_selector(self):
        workflow = Path(".github/workflows/android-native-wheels.yml").read_text(encoding="utf-8")
        self.assertIn("cibuildwheel==4.3.3", workflow)
        self.assertIn("CIBW_BUILD: cp315-android_arm64_v8a", workflow)
        self.assertIn("CIBW_ARCHS_ANDROID: arm64_v8a", workflow)

    def test_exact_python_abi_and_version(self):
        index = '<a href="cffi-1.17.1-1-cp313-cp313-android_24_arm64_v8a.whl">wheel</a>'
        self.assertTrue(compatible_wheels(index, "cffi", "1.17.1", "3.13", "arm64-v8a"))
        self.assertFalse(compatible_wheels(index, "cffi", "1.17.1", "3.15", "arm64-v8a"))
        self.assertFalse(compatible_wheels(index, "cffi", "1.17.1", "3.13", "armeabi-v7a"))

    def test_abi3_from_older_python_is_compatible(self):
        index = '<a href="cryptography-48.0.1-cp313-abi3-android_24_arm64_v8a.whl#sha256=123">wheel</a>'
        self.assertTrue(compatible_wheels(index, "cryptography", "48.0.1", "3.15", "arm64-v8a"))
        self.assertFalse(compatible_wheels(index, "cryptography", "48.0.1", "3.11", "arm64-v8a"))

    def test_abi3_from_newer_python_is_not_compatible(self):
        index = '<a href="cryptography-48.0.1-cp315-abi3-android_24_arm64_v8a.whl">wheel</a>'
        self.assertFalse(compatible_wheels(index, "cryptography", "48.0.1", "3.14", "arm64-v8a"))

    def test_linux_wheel_cannot_satisfy_android(self):
        index = '<a href="cffi-1.17.1-cp315-cp315-manylinux_2_17_aarch64.whl">wheel</a>'
        self.assertFalse(compatible_wheels(index, "cffi", "1.17.1", "3.15", "arm64-v8a"))

    def test_pure_python_is_not_assumed_native(self):
        index = '<a href="Pillow-13.0.0-py3-none-any.whl">wheel</a>'
        self.assertFalse(compatible_wheels(index, "pillow", "13.0.0", "3.15", "arm64-v8a"))

    def test_pins_read_from_gradle(self):
        self.assertEqual(
            {"pillow": "13.0.0", "argon2-cffi-bindings": "21.2.0"},
            pinned_versions("install 'Pillow==13.0.0'\ninstall 'argon2-cffi-bindings==21.2.0'"),
        )


if __name__ == "__main__":
    unittest.main()
