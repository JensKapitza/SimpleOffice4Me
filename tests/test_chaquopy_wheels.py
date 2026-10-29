"""Offline tests for Android native wheel compatibility preflight."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tools.check_chaquopy_wheels import (
    compatible_pypi_pure_wheel,
    compatible_wheels,
    download_pypi_pure_wheel,
    pinned_versions,
)
from tools.prepare_android_libffi import patch_android_libffi


class ChaquopyWheelsTests(unittest.TestCase):
    def test_experimental_builder_pins_cpython_315_android_arm64_source_revisions(self):
        workflow = Path(".github/workflows/android-native-wheels.yml").read_text(encoding="utf-8")
        wheel_build_tools = Path("requirements-ci-android-wheels.txt").read_text(
            encoding="utf-8",
        )
        self.assertIn("cibuildwheel==4.3.3", wheel_build_tools)
        self.assertIn("--hash=sha256:", wheel_build_tools)
        self.assertIn("requirements-ci-android-wheels.txt", workflow)
        self.assertIn("_rust.abi3*.so", workflow)
        self.assertIn('"${#extensions[@]}" -ne 1', workflow)
        self.assertIn("CIBW_BUILD: cp315-android_arm64_v8a", workflow)
        self.assertIn("CIBW_ARCHS_ANDROID: arm64_v8a", workflow)
        self.assertIn("package: cffi", workflow)
        self.assertIn("version: '2.0.0'", workflow)
        self.assertIn("44d1b5909021139fe36001ae048dbdde8214afa20200eda0f64c068cac5d5529", workflow)
        self.assertIn("PILLOW_SOURCE_COMMIT: 293218ad059d994baf1777d882911bed6a49704a", workflow)
        self.assertIn("PILLOW_DEPENDENCIES_COMMIT: 93950a8f9ada1165a4ab8c719326d5017e3f6c72", workflow)
        self.assertIn('! -name requirements.txt', workflow)
        self.assertIn('cd "$source_dir"', workflow)
        self.assertNotIn('CIBW_BEFORE_ALL=\'BUILD_PREFIX', workflow)
        self.assertIn('"$GITHUB_WORKSPACE/wheelhouse"', workflow)
        self.assertIn("package: cryptography", workflow)
        self.assertIn("version: '48.0.1'", workflow)
        self.assertIn("266f4ee051abb2f725b74ef8072b521ce1feacf685a3364fa6a6b45548db791a", workflow)
        self.assertIn("cryptography", workflow)
        self.assertIn("48.0.1", workflow)
        self.assertIn("LIBFFI_SOURCE_COMMIT", workflow)
        self.assertIn("3d0ce1e6fcf19f853894862abcbac0ae78a7be60", workflow)
        self.assertIn("prepare_android_libffi.py", workflow)
        self.assertIn("package: argon2-cffi-bindings", workflow)
        self.assertIn("bb89ceffa6c791807d1305ceb77dbfacc5aa499891d2c55661c6459651fc39e3", workflow)
        self.assertIn("ARGON2_CFFI_USE_SSE2=0", workflow)

    def test_android_libffi_compat_patch_is_versioned_narrow_and_idempotent(self):
        with TemporaryDirectory() as temporary_directory:
            configure = Path(temporary_directory) / "configure.ac"
            configure.write_text(
                "AC_INIT([libffi],[3.4.6],[https://example.invalid])\n"
                "LT_SYS_SYMBOL_USCORE\n",
                encoding="utf-8",
            )

            self.assertTrue(patch_android_libffi(Path(temporary_directory)))
            patched = configure.read_text(encoding="utf-8")
            self.assertNotIn("LT_SYS_SYMBOL_USCORE", patched)
            self.assertIn("SO4ME_ANDROID_SYMBOL_PREFIX", patched)
            self.assertIn("sys_symbol_underscore=no", patched)
            self.assertFalse(patch_android_libffi(Path(temporary_directory)))

    def test_android_libffi_compat_patch_rejects_unexpected_source(self):
        with TemporaryDirectory() as temporary_directory:
            configure = Path(temporary_directory) / "configure.ac"
            configure.write_text("AC_INIT([libffi],[3.4.7])\nLT_SYS_SYMBOL_USCORE\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected libffi 3.4.6"):
                patch_android_libffi(Path(temporary_directory))

            configure.write_text("AC_INIT([libffi],[3.4.6])\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "found 0"):
                patch_android_libffi(Path(temporary_directory))

    def test_exact_python_abi_and_version(self):
        index = '<a href="cffi-2.0.0-1-cp313-cp313-android_24_arm64_v8a.whl">wheel</a>'
        self.assertTrue(compatible_wheels(index, "cffi", "2.0.0", "3.13", "arm64-v8a"))
        self.assertFalse(compatible_wheels(index, "cffi", "2.0.0", "3.15", "arm64-v8a"))
        self.assertFalse(compatible_wheels(index, "cffi", "2.0.0", "3.13", "armeabi-v7a"))

    def test_abi3_from_older_python_is_compatible(self):
        index = '<a href="cryptography-48.0.1-cp313-abi3-android_24_arm64_v8a.whl#sha256=123">wheel</a>'
        self.assertTrue(compatible_wheels(index, "cryptography", "48.0.1", "3.15", "arm64-v8a"))
        self.assertFalse(compatible_wheels(index, "cryptography", "48.0.1", "3.11", "arm64-v8a"))

    def test_abi3_tags_cover_old_and_new_cpython_minor_formats(self):
        for wheel_tag, target, expected in (
            ("cp38", "3.15", True),
            ("cp38", "3.8", True),
            ("cp39", "3.8", False),
            ("cp310", "3.15", True),
            ("cp313", "3.15", True),
            ("cp315", "3.14", False),
        ):
            with self.subTest(wheel_tag=wheel_tag, target=target):
                index = f'<a href="cryptography-48.0.1-{wheel_tag}-abi3-android_24_arm64_v8a.whl">wheel</a>'
                self.assertEqual(expected, compatible_wheels(index, "cryptography", "48.0.1", target, "arm64-v8a"))

    def test_abi3_from_newer_python_is_not_compatible(self):
        index = '<a href="cryptography-48.0.1-cp315-abi3-android_24_arm64_v8a.whl">wheel</a>'
        self.assertFalse(compatible_wheels(index, "cryptography", "48.0.1", "3.14", "arm64-v8a"))

    def test_linux_wheel_cannot_satisfy_android(self):
        index = '<a href="cffi-2.0.0-cp315-cp315-manylinux_2_17_aarch64.whl">wheel</a>'
        self.assertFalse(compatible_wheels(index, "cffi", "2.0.0", "3.15", "arm64-v8a"))

    def test_hash_verified_pypi_universal_wheel_satisfies_pure_package(self):
        payload = {
            "info": {"name": "reportlab", "version": "4.2.5"},
            "urls": [{
                "filename": "reportlab-4.2.5-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/packages/reportlab.whl",
                "digests": {"sha256": "a" * 64},
            }],
        }
        self.assertTrue(compatible_pypi_pure_wheel(payload, "reportlab", "4.2.5"))

    def test_pypi_pure_wheel_download_is_hash_verified(self):
        import hashlib
        import io
        from tempfile import TemporaryDirectory

        wheel_bytes = b"verified reportlab wheel fixture"
        payload = {
            "info": {"name": "reportlab", "version": "4.2.5"},
            "urls": [{
                "filename": "reportlab-4.2.5-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/packages/reportlab.whl",
                "digests": {"sha256": hashlib.sha256(wheel_bytes).hexdigest()},
            }],
        }

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with TemporaryDirectory() as temporary_directory:
            with patch("tools.check_chaquopy_wheels.build_opener") as build_opener:
                build_opener.return_value.open.return_value = Response(wheel_bytes)
                wheel = download_pypi_pure_wheel(payload, "reportlab", "4.2.5", Path(temporary_directory))
            self.assertEqual(wheel_bytes, wheel.read_bytes())
            self.assertEqual("reportlab-4.2.5-py3-none-any.whl", wheel.name)

    def test_pypi_pure_wheel_hash_mismatch_does_not_leave_partial_artifact(self):
        import io
        from tempfile import TemporaryDirectory

        payload = {
            "info": {"name": "reportlab", "version": "4.2.5"},
            "urls": [{
                "filename": "reportlab-4.2.5-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/packages/reportlab.whl",
                "digests": {"sha256": "a" * 64},
            }],
        }

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            with patch("tools.check_chaquopy_wheels.build_opener") as build_opener:
                build_opener.return_value.open.return_value = Response(b"tampered wheel")
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    download_pypi_pure_wheel(payload, "reportlab", "4.2.5", directory)
            self.assertEqual([], list(directory.glob("*.whl")))

    def test_universal_pypi_wheel_requires_the_exact_package_version_and_hash(self):
        payload = {
            "info": {"name": "reportlab", "version": "4.2.5"},
            "urls": [{
                "filename": "reportlab-4.2.5-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/packages/reportlab.whl",
                "digests": {},
            }],
        }
        self.assertFalse(compatible_pypi_pure_wheel(payload, "reportlab", "4.2.5"))
        with self.assertRaisesRegex(ValueError, "version does not match"):
            compatible_pypi_pure_wheel(payload, "reportlab", "4.2.4")

    def test_universal_pypi_wheel_rejects_untrusted_hosts_and_paths(self):
        payload = {
            "info": {"name": "reportlab", "version": "4.2.5"},
            "urls": [{
                "filename": "../reportlab-4.2.5-py3-none-any.whl",
                "url": "https://example.invalid/reportlab.whl",
                "digests": {"sha256": "a" * 64},
            }],
        }
        self.assertFalse(compatible_pypi_pure_wheel(payload, "reportlab", "4.2.5"))

    def test_pure_python_is_not_assumed_native(self):
        index = '<a href="Pillow-13.0.0-py3-none-any.whl">wheel</a>'
        self.assertFalse(compatible_wheels(index, "pillow", "13.0.0", "3.15", "arm64-v8a"))

    def test_pins_read_from_gradle(self):
        self.assertEqual(
            {
                "pillow": "13.0.0",
                "argon2-cffi-bindings": "21.2.0",
                "cryptography": "48.0.1",
                "cffi": "2.0.0",
            },
            pinned_versions(
                "install 'Pillow==13.0.0'\n"
                "install 'argon2-cffi-bindings==21.2.0'\n"
                "install 'cryptography==48.0.1'\n"
                "install 'cffi==2.0.0'"
            ),
        )


if __name__ == "__main__":
    unittest.main()
