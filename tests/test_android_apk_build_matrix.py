import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class AndroidApkBuildMatrixTest(unittest.TestCase):
    def test_gradle_exposes_only_the_verified_arm64_runtime_profile(self):
        text = Path("android/apk/app/build.gradle").read_text(encoding="utf-8")
        self.assertIn("'arm64-v8a': '3.14'", text)
        self.assertNotIn("'armeabi-v7a': '3.11'", text)
        self.assertIn("SIMPLEOFFICE_ANDROID_ABI", text)
        self.assertIn("SIMPLEOFFICE_ANDROID_PYTHON", text)
        self.assertIn("abiFilters androidAbi", text)
        self.assertIn("version = pythonRuntimeVersion", text)

    def test_android_apk_uses_audited_pdf_and_no_selector_engine(self):
        gradle = Path("android/apk/app/build.gradle").read_text(encoding="utf-8")
        self.assertIn("install 'pypdf==7.0.1'", gradle)
        self.assertNotIn("soupsieve", gradle)
        self.assertNotIn("beautifulsoup4", gradle)

    def test_apk_version_comes_from_project_metadata(self):
        gradle = Path("android/apk/app/build.gradle").read_text(encoding="utf-8")
        project = Path("pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(
            project,
            r'(?ms)^\[project\].*?^version\s*=\s*["\x27]\d+\.\d+\.\d+["\x27]',
        )
        self.assertIn("file('../../../pyproject.toml')", gradle)
        self.assertIn("versionCode appVersionCode", gradle)
        self.assertIn("versionName appVersionName", gradle)
        self.assertIn("SIMPLEOFFICE_BUILD_DATE", gradle)
        self.assertIn("SIMPLEOFFICE_BUILD_SEQUENCE", gradle)
        self.assertIn("daysSinceEpoch * 10000L + buildSequence", gradle)
        self.assertIn("calculatedVersionCode > 2_100_000_000L", gradle)
        self.assertIn("def appVersionName = \"${versionMajor}.${buildDate}.${buildSequence}\"", gradle)
        self.assertIn("include 'pyproject.toml'", gradle)
        self.assertNotIn("versionName '1.0.7'", gradle)
        self.assertNotIn("versionCode 8", gradle)

    def test_date_based_android_codes_increase_across_builds_and_days(self):
        from datetime import date

        epoch = date(2020, 1, 1)

        def code(day, sequence):
            return (date.fromisoformat(day) - epoch).days * 10000 + sequence

        self.assertLess(2_000_000, code("2026-10-21", 1))
        self.assertLess(code("2026-10-21", 1), code("2026-10-21", 2))
        self.assertLess(code("2026-10-21", 9999), code("2026-10-22", 1))
        self.assertLess(code("2026-10-22", 1), 2_100_000_000)

    def test_ci_publishes_only_verified_arm64_apk(self):
        text = Path(".github/workflows/android-apk-build.yml").read_text(encoding="utf-8")
        self.assertIn("abi: arm64-v8a", text)
        self.assertIn("python: '3.14'", text)
        self.assertNotIn("abi: armeabi-v7a", text)
        self.assertNotIn("ARM32", text)
        self.assertIn("simpleoffice4me-android-installable", text)
        self.assertIn("page-size: '16'", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("pull_request:", text)
        self.assertIn("push:", text)
        self.assertIn("- main", text)
        self.assertIn("\'android/apk/**\'", text)
        self.assertIn("\'.github/workflows/android-apk-build.yml\'", text)
        self.assertIn("\'tests/test_android_apk_build_matrix.py\'", text)
        self.assertIn("\'tests/test_android_native_integration.py\'", text)
        self.assertIn("assets/simpleoffice/pyproject.toml", text)
        self.assertIn("Verify APK package date version and Android versionCode", text)
        self.assertIn("versionCode=\'$EXPECTED_CODE\'", text)
        self.assertIn("SIMPLEOFFICE_BUILD_SEQUENCE=${{ github.run_number }}", text)
        self.assertIn("date -u +%Y%m%d", text)
        self.assertIn("VERSIONED_OUTPUT", text)
        self.assertIn("SIMPLEOFFICE_BUILD_DATE", text)
        self.assertIn("SIMPLEOFFICE_BUILD_SEQUENCE", text)

    def test_inventory_only_prs_rebuild_the_android_webview_assets(self):
        workflow = Path(".github/workflows/android-apk-build.yml").read_text(encoding="utf-8")
        pr_filters = workflow.split("  pull_request:\n", 1)[1].split("  push:\n", 1)[0]
        for pattern in ("app/inventory.py", "static/js/inventory*.js", "templates/inventory/**"):
            self.assertIn(f"      - '{pattern}'", pr_filters)
        for path in ("static/js/inventory_nfc.js", "templates/inventory/index.html"):
            self.assertIn(f"grep -F 'assets/simpleoffice/{path}' /tmp/apk-files.txt", workflow)
            self.assertTrue(Path(path).is_file())

    def test_apk_job_downloads_and_hash_verifies_all_native_python_wheels(self):
        for workflow_name in ("android-apk-build.yml", "ubuntu-26-compat.yml"):
            text = (Path(".github/workflows") / workflow_name).read_text(encoding="utf-8")
            for package in ("Pillow", "cryptography", "cffi", "argon2-cffi-bindings"):
                self.assertIn(f"android-cp314-arm64-{package}-wheel", text)
                self.assertIn(f"{package}-SHA256SUMS", text)



@unittest.skipUnless(all(shutil.which(tool) for tool in ("bash", "timeout", "tr", "grep")), "needs Linux shell tools")
class AndroidContactsRunnerTest(unittest.TestCase):
    def test_resolves_sdk_adb_without_path_and_enforces_instrumentation_verdict(self):
        with TemporaryDirectory() as directory:
            top = Path(directory)
            bin_dir = top / "bin"
            bin_dir.mkdir()
            for tool in ("timeout", "tr", "grep"):
                (bin_dir / tool).symlink_to(shutil.which(tool))
            sdk = top / "SDK path with space"
            adb = sdk / "platform-tools" / "adb"
            adb.parent.mkdir(parents=True)
            adb.write_text(
                """#!/bin/bash
[[ "$1 $2" == '-s emulator-5560' ]] || exit 10
shift 2
case "$*" in
  'wait-for-device') ;;
  'shell getprop sys.boot_completed') echo 1 ;;
  'shell getprop ro.hardware') echo ranchu ;;
  'shell getprop ro.product.cpu.abilist') echo "${TEST_ABI:-x86_64,arm64-v8a}" ;;
  'shell pm list packages de.simpleoffice4me.android') ;;
  install*) printf 'install\n' >> "$TEST_CALLS" ;;
  'shell am instrument -w -r -e disposable yes '*)
    printf '%s\n' "INSTRUMENTATION_RESULT: simpleoffice_contacts_provider=$TEST_RESULT" \
      "INSTRUMENTATION_RESULT: simpleoffice_calendar_provider=$TEST_CALENDAR" \
      'INSTRUMENTATION_RESULT: stream=' 'SIMPLEOFFICE_CONTACTS_PROVIDER_PASS'
    if [[ "$TEST_BULK" != missing ]]; then
      printf '%s\n' "INSTRUMENTATION_RESULT: simpleoffice_contacts_bulk_resume=$TEST_BULK"
    fi
    printf '%s\n' "INSTRUMENTATION_CODE: $TEST_CODE" ;;
  'shell am instrument'*) printf 'SIMPLEOFFICE_CONTACTS_PROVIDER_PASS\n' ;;
  *) exit 10 ;;
esac
""",
                encoding="utf-8",
            )
            adb.chmod(0o700)
            apk, test_apk, calls = (top / name for name in ("app.apk", "test.apk", "calls"))
            apk.touch()
            test_apk.touch()
            command = [
                shutil.which("bash"), "tools/run_android_contacts_provider_acceptance.sh",
                str(apk), str(test_apk), "emulator-5560",
            ]
            env = {**os.environ, "PATH": str(bin_dir), "ANDROID_HOME": str(sdk), "TEST_CALLS": str(calls)}
            for code, verdict, calendar, bulk, abi, expected in (
                (0, "pass", "pass", "pass", "x86_64,arm64-v8a", 1),
                (-1, "no-result", "pass", "pass", "x86_64,arm64-v8a", 1),
                (-1, "pass", "no-result", "pass", "x86_64,arm64-v8a", 1),
                (-1, "pass", "pass", "missing", "x86_64,arm64-v8a", 1),
                (-1, "pass", "pass", "fail", "x86_64,arm64-v8a", 1),
                (-1, "pass", "pass", "pass", "x86_64,arm64-v8a", 0),
                (-1, "pass", "pass", "pass", "x86_64", 2),
            ):
                with self.subTest(code=code, verdict=verdict, calendar=calendar, bulk=bulk, abi=abi):
                    calls.unlink(missing_ok=True)
                    result = subprocess.run(
                        command, env={**env, "TEST_CODE": str(code), "TEST_ABI": abi,
                             "TEST_RESULT": verdict, "TEST_CALENDAR": calendar, "TEST_BULK": bulk},
                        capture_output=True, text=True, timeout=15, check=False,
                    )
                    self.assertEqual(expected, result.returncode, result.stderr)
                    if expected == 2:
                        self.assertFalse(calls.exists(), "Unsupported ABI must not install anything")
                    else:
                        self.assertEqual(["install", "install"], calls.read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
