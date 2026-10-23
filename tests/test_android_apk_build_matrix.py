import unittest
from pathlib import Path


class AndroidApkBuildMatrixTest(unittest.TestCase):
    def test_gradle_exposes_only_the_verified_arm64_runtime_profile(self):
        text = Path("android/apk/app/build.gradle").read_text(encoding="utf-8")
        self.assertIn("'arm64-v8a': '3.15'", text)
        self.assertNotIn("'armeabi-v7a': '3.11'", text)
        self.assertIn("SIMPLEOFFICE_ANDROID_ABI", text)
        self.assertIn("SIMPLEOFFICE_ANDROID_PYTHON", text)
        self.assertIn("abiFilters androidAbi", text)
        self.assertIn("version = pythonRuntimeVersion", text)

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
        self.assertIn("python: '3.15'", text)
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

    def test_apk_job_downloads_and_hash_verifies_all_native_python_wheels(self):
        for workflow_name in ("android-apk-build.yml", "ubuntu-26-compat.yml"):
            text = (Path(".github/workflows") / workflow_name).read_text(encoding="utf-8")
            for package in ("Pillow", "cryptography", "cffi", "argon2-cffi-bindings"):
                self.assertIn(f"android-cp315-arm64-{package}-wheel", text)
                self.assertIn(f"{package}-SHA256SUMS", text)


if __name__ == "__main__":
    unittest.main()
