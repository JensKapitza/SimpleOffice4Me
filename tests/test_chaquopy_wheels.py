"""Offline tests for Android native wheel compatibility preflight."""
import unittest
from tools.check_chaquopy_wheels import compatible_wheels, pinned_versions


class ChaquopyWheelsTests(unittest.TestCase):
    def test_exact_python_abi_and_version(self):
        index = '<a href="cffi-1.17.1-1-cp313-cp313-android_24_arm64_v8a.whl">wheel</a>'
        self.assertTrue(compatible_wheels(index, "cffi", "1.17.1", "3.13", "arm64-v8a"))
        self.assertFalse(compatible_wheels(index, "cffi", "1.17.1", "3.15", "arm64-v8a"))
        self.assertFalse(compatible_wheels(index, "cffi", "1.17.1", "3.13", "armeabi-v7a"))

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
