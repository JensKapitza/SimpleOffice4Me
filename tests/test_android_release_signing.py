"""Persistent Android release signing path (setup only, no credentials).

The release build loads four signing settings from user-level Gradle
properties or environment variables, assigns ``signingConfigs.release``
only when all are present, and fails closed on ``assembleRelease``
when signing is missing, partial, or unreadable. Local builds without
an explicit sequence use a deterministic UTC time slot so same-day
``versionCode`` values increase. Static source assertions (same
convention as the other Android build tests); no real secret values.
"""

import unittest
from pathlib import Path

GRADLE = Path("android/apk/app/build.gradle").read_text(encoding="utf-8")
README = Path("android/apk/README.md").read_text(encoding="utf-8")
REPO_PROPERTIES = Path("android/apk/gradle.properties").read_text(encoding="utf-8")
WORKFLOW = Path(".github/workflows/android-apk-build.yml").read_text(encoding="utf-8")
RELEASE_WORKFLOW = Path(".github/workflows/android-apk-release.yml").read_text(encoding="utf-8")
PROJECT_RELEASE_WORKFLOW = Path(".github/workflows/project-release.yml").read_text(encoding="utf-8")

PROPERTY_KEYS = (
    "simpleofficeReleaseKeystore",
    "simpleofficeReleaseKeystorePassword",
    "simpleofficeReleaseKeyAlias",
    "simpleofficeReleaseKeyPassword",
)
ENV_KEYS = (
    "SIMPLEOFFICE_RELEASE_KEYSTORE",
    "SIMPLEOFFICE_RELEASE_KEYSTORE_PASSWORD",
    "SIMPLEOFFICE_RELEASE_KEY_ALIAS",
    "SIMPLEOFFICE_RELEASE_KEY_PASSWORD",
)
VALUE_VARIABLES = (
    "releaseKeystorePath",
    "releaseKeystorePassword",
    "releaseKeyAlias",
    "releaseKeyPassword",
)


class AndroidReleaseSigningTest(unittest.TestCase):
    def test_four_settings_load_from_properties_or_env(self) -> None:
        for key in PROPERTY_KEYS:
            self.assertIn(f"findProperty('{key}')", GRADLE)
        for key in ENV_KEYS:
            self.assertIn(f"System.getenv('{key}')", GRADLE)
        self.assertIn("user-level Gradle properties", GRADLE)

    def test_release_signing_assigned_only_when_fully_configured(self) -> None:
        self.assertIn(
            "def releaseSigningConfigured = missingReleaseSigning.isEmpty()",
            GRADLE,
        )
        self.assertIn("signingConfigs {", GRADLE)
        self.assertIn("signingConfig signingConfigs.release", GRADLE)
        self.assertEqual(2, GRADLE.count("if (releaseSigningConfigured) {"))
        self.assertIn("storeFile file(releaseKeystorePath)", GRADLE)
        self.assertIn("storePassword releaseKeystorePassword", GRADLE)
        self.assertIn("keyAlias releaseKeyAlias", GRADLE)
        self.assertIn("keyPassword releaseKeyPassword", GRADLE)
        for key in PROPERTY_KEYS:
            self.assertIn(key, GRADLE.split("def missingReleaseSigning")[1])

    def test_assemble_release_fails_closed_when_unsigned(self) -> None:
        self.assertIn("gradle.taskGraph.whenReady", GRADLE)
        self.assertIn('hasTask("${project.path}:assembleRelease")', GRADLE)
        self.assertIn("Release signing is not configured.", GRADLE)
        self.assertIn("Release signing is only partially configured", GRADLE)
        self.assertIn("Release keystore is not a readable file", GRADLE)
        self.assertIn("refusing to build an unsigned release APK", GRADLE)
        guard = GRADLE.split("gradle.taskGraph.whenReady", 1)[1]
        self.assertNotIn("assembleDebug", guard)

    def test_no_credential_values_logged_or_interpolated(self) -> None:
        for variable in VALUE_VARIABLES:
            self.assertNotIn("${" + variable + "}", GRADLE)
            self.assertNotIn("'" + variable + "'", GRADLE)
        for line in GRADLE.splitlines():
            if "println" in line or "logger." in line:
                for variable in VALUE_VARIABLES:
                    self.assertNotIn(variable, line)

    def test_repo_gradle_properties_holds_no_signing_material(self) -> None:
        for key in PROPERTY_KEYS + ENV_KEYS:
            self.assertNotIn(key, REPO_PROPERTIES)

    def test_debug_and_ci_behavior_unchanged(self) -> None:
        self.assertIn("assembleDebug", WORKFLOW)
        self.assertNotIn("assembleRelease", WORKFLOW)
        self.assertNotIn("keystore", WORKFLOW.lower())
        self.assertEqual(1, GRADLE.count("signingConfig signingConfigs.release"))

    def test_local_sequence_defaults_to_utc_time_slot(self) -> None:
        self.assertIn("rawBuildSequenceEnv", GRADLE)
        self.assertIn("java.time.Instant.now().epochSecond % 86400", GRADLE)
        self.assertIn("intdiv(10) + 1", GRADLE)
        self.assertIn(
            "'SIMPLEOFFICE_BUILD_SEQUENCE must be 1..9999'", GRADLE
        )
        self.assertIn("daysSinceEpoch * 10000L + buildSequence", GRADLE)

    def test_time_slot_range_and_monotonicity(self) -> None:
        def slot(seconds_of_day: int) -> int:
            return seconds_of_day // 10 + 1

        self.assertEqual(1, slot(0))
        self.assertEqual(8640, slot(86399))
        for seconds in (0, 9, 10, 43200, 86390, 86399):
            self.assertGreaterEqual(slot(seconds), 1)
            self.assertLessEqual(slot(seconds), 9999)
        self.assertLess(slot(100), slot(200))
        self.assertEqual(slot(100), slot(109))
        epoch_days = 2123
        self.assertLess(
            epoch_days * 10000 + slot(100), epoch_days * 10000 + slot(200)
        )
        self.assertLess(
            epoch_days * 10000 + 8640, (epoch_days + 1) * 10000 + 1
        )

    def test_readme_documents_release_signing_path(self) -> None:
        self.assertIn("## Release signing", README)
        self.assertIn("outside the repository", README)
        self.assertIn("Back it up securely", README)
        self.assertIn("~/.gradle/gradle.properties", README)
        for key in PROPERTY_KEYS + ENV_KEYS:
            self.assertIn(key, README)
        self.assertIn("gradle assembleRelease", README)
        self.assertIn("apksigner verify --print-certs", README)
        self.assertIn("SHA-256", README)
        self.assertIn("uninstalling it once", README)
        self.assertIn("same release certificate plus a higher `versionCode`", README)
        self.assertIn("development-only", README)
        self.assertIn("Working copies of the signing material live in GitHub Secrets", README)
        self.assertIn("feature-branch dispatches are refused", README)
        self.assertIn("explicit sequence always wins", README)
        self.assertIn("fails with a clear error instead of producing an unsigned APK", README)

    def test_signed_release_is_restricted_to_main_or_main_ancestor_version_tags(self) -> None:
        self.assertIn(
            "if: github.ref == 'refs/heads/main' || (github.ref_type == 'tag'",
            RELEASE_WORKFLOW,
        )
        self.assertIn("git merge-base --is-ancestor", RELEASE_WORKFLOW)
        self.assertIn("refs/remotes/origin/main", RELEASE_WORKFLOW)
        self.assertIn('"$GITHUB_REF_NAME" != "v$version"', RELEASE_WORKFLOW)
        self.assertIn("fetch-depth: 0", RELEASE_WORKFLOW)

    def test_release_secrets_are_scoped_and_not_written_to_github_env(self) -> None:
        self.assertIn(
            "ANDROID_RELEASE_KEYSTORE_BASE64: ${{ secrets.ANDROID_RELEASE_KEYSTORE_BASE64 }}",
            RELEASE_WORKFLOW,
        )
        self.assertIn(
            "SIMPLEOFFICE_RELEASE_KEYSTORE_PASSWORD: ${{ secrets.ANDROID_RELEASE_KEYSTORE_PASSWORD }}",
            RELEASE_WORKFLOW,
        )
        self.assertIn(
            "SIMPLEOFFICE_RELEASE_KEY_PASSWORD: ${{ secrets.ANDROID_RELEASE_KEY_PASSWORD }}",
            RELEASE_WORKFLOW,
        )
        self.assertNotIn("SIMPLEOFFICE_RELEASE_KEYSTORE_PASSWORD=${{ secrets.", RELEASE_WORKFLOW)
        self.assertNotIn("SIMPLEOFFICE_RELEASE_KEY_PASSWORD=${{ secrets.", RELEASE_WORKFLOW)
        self.assertNotIn("ANDROID_RELEASE_KEYSTORE_BASE64 }}\" | base64", RELEASE_WORKFLOW)

    def test_project_release_collects_signed_android_artifact(self) -> None:
        self.assertIn("android-apk-release.yml", PROJECT_RELEASE_WORKFLOW)
        self.assertIn("steps.runs.outputs.android_apk_release", PROJECT_RELEASE_WORKFLOW)


if __name__ == "__main__":
    unittest.main()
