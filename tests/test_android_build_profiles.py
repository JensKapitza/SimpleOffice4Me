"""Regression tests for supported Android ABI profiles and security gates."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/android-apk-build.yml"
GRADLE = ROOT / "android/apk/app/build.gradle"


class AndroidBuildProfilesTests(unittest.TestCase):
    def test_arm32_is_unavailable_without_a_verified_pillow_wheel(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        gradle = GRADLE.read_text(encoding="utf-8")
        self.assertNotIn("abi: armeabi-v7a", workflow)
        self.assertNotIn("ARM32", workflow)
        self.assertNotIn("'armeabi-v7a': '3.11'", gradle)
        self.assertIn("Unsupported Android ABI", gradle)

    def test_gradle_rejects_wrong_python_abi_combinations(self):
        gradle = GRADLE.read_text(encoding="utf-8")
        self.assertIn("'arm64-v8a': '3.15'", gradle)
        self.assertIn("pythonRuntimeVersion != expectedPythonVersion", gradle)
        self.assertIn("cffi-2.0.0-*.whl", gradle)
        self.assertIn("pillow-13.0.0-*.whl", gradle)
        self.assertIn("cryptography-48.0.1-*.whl", gradle)
        self.assertIn("argon2_cffi_bindings-21.2.0-*.whl", gradle)
        self.assertIn("localArgon2BindingsWheel.absolutePath", gradle)
        self.assertIn("Match counts: cffi=", gradle)
        self.assertIn("reportlab-4.2.5-py3-none-any.whl", gradle)
        self.assertIn("localReportlabWheel.absolutePath", gradle)

    def test_apk_job_consumes_hash_verified_native_wheel_artifacts(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("android-cp315-arm64-Pillow-wheel", workflow)
        self.assertIn("android-cp315-arm64-cryptography-wheel", workflow)
        self.assertIn("android-cp315-arm64-argon2-cffi-bindings-wheel", workflow)
        self.assertIn("android-cp315-arm64-cffi-wheel", workflow)
        self.assertIn("sha256sum --check Pillow-SHA256SUMS cryptography-SHA256SUMS cffi-SHA256SUMS argon2-cffi-bindings-SHA256SUMS", workflow)
        self.assertIn("--wheel-dir android/apk/app/wheelhouse", workflow)

    def test_ubuntu_26_android_job_uses_the_same_verified_wheel_artifacts(self):
        workflow = (ROOT / ".github/workflows/ubuntu-26-compat.yml").read_text(encoding="utf-8")
        self.assertIn("android-native-wheels:", workflow)
        self.assertIn("needs: android-native-wheels", workflow)
        self.assertIn("SIMPLEOFFICE_ANDROID_WHEEL_DIR:", workflow)
        self.assertIn("android-cp315-arm64-Pillow-wheel", workflow)
        self.assertIn("android-cp315-arm64-cryptography-wheel", workflow)
        self.assertIn("android-cp315-arm64-argon2-cffi-bindings-wheel", workflow)
        self.assertIn("android-cp315-arm64-cffi-wheel", workflow)
        self.assertIn("sha256sum --check Pillow-SHA256SUMS cryptography-SHA256SUMS cffi-SHA256SUMS argon2-cffi-bindings-SHA256SUMS", workflow)
        self.assertIn("--wheel-dir android/apk/app/wheelhouse", workflow)

    def test_security_gate_precedes_artifact_upload(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        sbom_upload = workflow.index("- name: Upload APK-bundled Python SBOM")
        gate = workflow.index("- name: Enforce Android Pillow security baseline")
        apk_upload = workflow.index("- name: Upload installable APK")
        self.assertLess(sbom_upload, gate)
        self.assertLess(gate, apk_upload)


if __name__ == "__main__":
    unittest.main()
