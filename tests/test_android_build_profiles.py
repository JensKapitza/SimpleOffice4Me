"""Regression tests for default ARM64 and opt-in legacy ARM32 build policy."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/android-apk-build.yml"
GRADLE = ROOT / "android/apk/app/build.gradle"


class AndroidBuildProfilesTests(unittest.TestCase):
    def test_legacy_arm32_requires_manual_opt_in(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("build_arm32:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.build_arm32", workflow)
        self.assertIn('"abi":"armeabi-v7a","python":"3.11"', workflow)
        self.assertIn('"abi":"arm64-v8a","python":"3.15"', workflow)
        # The fallback matrix for PR/push/manual-without-opt-in contains only ARM64.
        fallback = workflow.split(" || '", 1)[1].split("') }}", 1)[0]
        self.assertNotIn("armeabi-v7a", fallback)
        self.assertIn("arm64-v8a", fallback)

    def test_gradle_rejects_wrong_python_abi_combinations(self):
        gradle = GRADLE.read_text(encoding="utf-8")
        self.assertIn("'arm64-v8a': '3.15'", gradle)
        self.assertIn("'armeabi-v7a': '3.11'", gradle)
        self.assertIn("pythonRuntimeVersion != expectedPythonVersion", gradle)

    def test_security_gate_precedes_artifact_upload(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        sbom_upload = workflow.index("- name: Upload APK-bundled Python SBOM")
        gate = workflow.index("- name: Enforce Android Pillow security baseline")
        apk_upload = workflow.index("- name: Upload installable APK")
        self.assertLess(sbom_upload, gate)
        self.assertLess(gate, apk_upload)


if __name__ == "__main__":
    unittest.main()
