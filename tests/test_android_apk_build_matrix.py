import unittest
from pathlib import Path


class AndroidApkBuildMatrixTest(unittest.TestCase):
    def test_gradle_exposes_arm64_and_arm32_runtime_profiles(self):
        text = Path("android/apk/app/build.gradle").read_text(encoding="utf-8")
        self.assertIn("'arm64-v8a': '3.13'", text)
        self.assertIn("'armeabi-v7a': '3.11'", text)
        self.assertIn("SIMPLEOFFICE_ANDROID_ABI", text)
        self.assertIn("SIMPLEOFFICE_ANDROID_PYTHON", text)
        self.assertIn("abiFilters androidAbi", text)
        self.assertIn("version = pythonRuntimeVersion", text)

    def test_apk_version_comes_from_project_metadata(self):
        gradle = Path("android/apk/app/build.gradle").read_text(encoding="utf-8")
        project = Path("pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("version = \"2.0.0\"", project)
        self.assertIn("file('../../../pyproject.toml')", gradle)
        self.assertIn("versionCode appVersionCode", gradle)
        self.assertIn("versionName appVersionName", gradle)
        self.assertIn("include 'pyproject.toml'", gradle)
        self.assertNotIn("versionName '1.0.7'", gradle)
        self.assertNotIn("versionCode 8", gradle)

    def test_ci_publishes_a_separate_arm32_apk(self):
        text = Path(".github/workflows/android-apk-build.yml").read_text(encoding="utf-8")
        self.assertIn("abi: arm64-v8a", text)
        self.assertIn("python: '3.13'", text)
        self.assertIn("abi: armeabi-v7a", text)
        self.assertIn("python: '3.11'", text)
        self.assertIn("SimpleOffice4Me-Android-arm32.apk", text)
        self.assertIn("simpleoffice4me-android-arm32-installable", text)
        self.assertIn("page-size: '4'", text)
        self.assertIn("- 'pyproject.toml'", text)
        self.assertIn("assets/simpleoffice/pyproject.toml", text)
        self.assertIn("Verify APK package version matches project version", text)


if __name__ == "__main__":
    unittest.main()
