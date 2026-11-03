"""Regression tests for the honest Android APK/device smoke helper."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import android_emulator_smoke as smoke


class AndroidSmokeContractTests(unittest.TestCase):
    def _apk(self, content: str | None) -> Path:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        apk = Path(self.temp.name) / "app.apk"
        with zipfile.ZipFile(apk, "w") as archive:
            if content is not None:
                archive.writestr("assets/chaquopy/android_runtime.py", content)
        return apk

    def test_contract_accepts_packaged_runtime_wiring(self) -> None:
        source = (
            "def _verify_android_cryptography_extension():\n"
            " importlib.import_module('cryptography.hazmat.bindings._rust')\n"
            f" print('{smoke.RUST_SMOKE_MARKER}')\n"
        )
        result = smoke.check_packaged_runtime_contract(self._apk(source))
        self.assertTrue(result["ok"])
        self.assertEqual(["assets/chaquopy/android_runtime.py"], result["entries"])

    def test_contract_reads_compiled_runtime_from_nested_chaquopy_imy(self) -> None:
        source = (
            "_verify_android_cryptography_extension "
            f"cryptography.hazmat.bindings._rust {smoke.RUST_SMOKE_MARKER}"
        ).encode("ascii")
        bundled = BytesIO()
        with zipfile.ZipFile(bundled, "w") as inner:
            inner.writestr("android_runtime.pyc", source)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        apk = Path(self.temp.name) / "app.apk"
        with zipfile.ZipFile(apk, "w") as outer:
            outer.writestr("assets/chaquopy/app.imy", bundled.getvalue())
        result = smoke.check_packaged_runtime_contract(apk)
        self.assertTrue(result["ok"])
        self.assertEqual(
            ["assets/chaquopy/app.imy!/android_runtime.pyc"], result["entries"]
        )

    def test_contract_rejects_missing_or_incomplete_runtime(self) -> None:
        missing = smoke.check_packaged_runtime_contract(self._apk(None))
        incomplete = smoke.check_packaged_runtime_contract(
            self._apk("print('not the Android runtime')")
        )
        self.assertFalse(missing["ok"])
        self.assertFalse(incomplete["ok"])
        self.assertGreaterEqual(len(incomplete["failures"]), 3)

    def test_contract_reports_corrupt_apk_without_traceback(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        apk = Path(self.temp.name) / "corrupt.apk"
        apk.write_bytes(b"not a zip archive")
        result = smoke.check_packaged_runtime_contract(apk)
        self.assertFalse(result["ok"])
        self.assertIn("BadZipFile", result["failures"][0])

    def test_logcat_requires_fresh_import_marker_without_import_errors(self) -> None:
        passed = smoke.evaluate_logcat(smoke.RUST_SMOKE_MARKER)
        failed = smoke.evaluate_logcat(
            smoke.RUST_SMOKE_MARKER + "\nImportError: unresolved PyLong_Type"
        )
        self.assertTrue(passed["device_validated"])
        self.assertFalse(failed["device_validated"])
        self.assertIn("ImportError", failed["failure_signals"])

    def test_sanitizer_redacts_identifiers_and_keeps_marker(self) -> None:
        value = (
            "user@example.invalid 12345678-1234-5678-9abc-1234567890ab "
            + smoke.RUST_SMOKE_MARKER
        )
        result = smoke.sanitize_logcat(value)
        self.assertNotIn("user@example.invalid", result)
        self.assertNotIn("12345678-1234-5678-9abc-1234567890ab", result)
        self.assertIn(smoke.RUST_SMOKE_MARKER, result)


class AndroidSmokeDeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "out"
        self.output.mkdir()
        self.apk = Path(self.temp.name) / "app.apk"
        self.apk.write_bytes(b"synthetic APK path")

    def _run_with_devices(self, devices: list[str], abi: str = "arm64-v8a") -> tuple[dict, mock.Mock]:
        adb = mock.Mock(return_value="")
        with (
            mock.patch.object(smoke.shutil, "which", return_value="adb"),
            mock.patch.object(smoke, "_adb", adb),
            mock.patch.object(smoke, "_connected_devices", return_value=devices),
            mock.patch.object(smoke, "_device_abi", return_value=abi),
            mock.patch.object(smoke, "_wait_for_boot", return_value=True),
        ):
            result = smoke.run_device_smoke(self.apk, self.output, timeout_s=60)
        return result, adb

    def test_no_device_fails_closed(self) -> None:
        result, adb = self._run_with_devices([])
        self.assertFalse(result["device_validated"])
        self.assertIn("no Android device", result["reason"])
        self.assertFalse(any(call.args[0] == "install" for call in adb.call_args_list))

    def test_multiple_devices_fail_closed(self) -> None:
        result, adb = self._run_with_devices(["one", "two"])
        self.assertFalse(result["device_validated"])
        self.assertIn("multiple Android devices", result["reason"])
        self.assertFalse(any(call.args[0] == "install" for call in adb.call_args_list))

    def test_non_arm64_device_fails_before_install(self) -> None:
        result, adb = self._run_with_devices(["phone"], abi="armeabi-v7a")
        self.assertFalse(result["device_validated"])
        self.assertEqual("armeabi-v7a", result["device_abi"])
        self.assertFalse(any(call.args[0] == "install" for call in adb.call_args_list))

    def test_launch_clears_logcat_before_install_and_needs_fresh_marker(self) -> None:
        operations: list[tuple[str, ...]] = []
        logcat_cleared = False
        launched = False

        def adb(*args: str, timeout: int = 30, serial: str | None = None) -> str:
            nonlocal logcat_cleared, launched
            operations.append(args)
            if args == ("devices",):
                return "List of devices attached\nphone\tdevice\n"
            if args == ("shell", "getprop", "ro.product.cpu.abi"):
                return "arm64-v8a\n"
            if args == ("logcat", "-c"):
                logcat_cleared = True
                return ""
            if args[0] == "install":
                if not logcat_cleared:
                    raise AssertionError("logcat was not cleared before install")
                return "Success"
            if args == ("shell", "am", "start", "-W", "-n", smoke.LAUNCH_ACTIVITY):
                launched = True
                return "Status: ok"
            if args[:2] == ("logcat", "-d"):
                if not launched:
                    return ""
                return f"fresh launch: {smoke.RUST_SMOKE_MARKER}"
            return ""

        with (
            mock.patch.object(smoke.shutil, "which", return_value="adb"),
            mock.patch.object(smoke, "_adb", side_effect=adb),
            mock.patch.object(smoke, "_wait_for_boot", return_value=True),
        ):
            result = smoke.run_device_smoke(self.apk, self.output, timeout_s=60)

        self.assertTrue(result["device_validated"])
        self.assertEqual("phone", result["device_serial"])
        clear_index = operations.index(("logcat", "-c"))
        install_index = next(
            index for index, call_arg in enumerate(operations)
            if call_arg[0] == "install"
        )
        self.assertLess(clear_index, install_index)

    def test_old_logcat_marker_cannot_validate_current_launch(self) -> None:
        cleared = False

        def adb(*args: str, timeout: int = 30, serial: str | None = None) -> str:
            nonlocal cleared
            if args == ("devices",):
                return "List of devices attached\nphone\tdevice\n"
            if args == ("shell", "getprop", "ro.product.cpu.abi"):
                return "arm64-v8a\n"
            if args == ("logcat", "-c"):
                cleared = True
                return ""
            if args[:2] == ("logcat", "-d"):
                # The marker is stale before clearing and absent afterward.
                return "" if cleared else smoke.RUST_SMOKE_MARKER
            return ""

        with (
            mock.patch.object(smoke.shutil, "which", return_value="adb"),
            mock.patch.object(smoke, "_adb", side_effect=adb),
            mock.patch.object(smoke, "_wait_for_boot", return_value=True),
            mock.patch.object(smoke.time, "sleep"),
            mock.patch.object(smoke.time, "monotonic", side_effect=(0, 0, 0, 2)),
        ):
            result = smoke.run_device_smoke(self.apk, self.output, timeout_s=1)

        self.assertFalse(result["device_validated"])
        self.assertFalse(result["marker_found"])


if __name__ == "__main__":
    unittest.main()
