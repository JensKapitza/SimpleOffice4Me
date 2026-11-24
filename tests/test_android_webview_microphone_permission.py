"""Check Android WebView permission wiring; JUnit covers the permission flow itself."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android/apk/app/src/main/java/de/simpleoffice4me/android/MainActivity.java"
MANIFEST = ROOT / "android/apk/app/src/main/AndroidManifest.xml"


class AndroidWebViewMicrophonePermissionTests(unittest.TestCase):
    def test_manifest_declares_microphone(self):
        manifest = MANIFEST.read_text(encoding="utf-8")
        self.assertIn('<uses-permission android:name="android.permission.RECORD_AUDIO"', manifest)
        self.assertIn('<uses-feature android:name="android.hardware.microphone" android:required="false"', manifest)

    def test_webview_routes_only_trusted_media_requests_to_android(self):
        source = JAVA.read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"RESOURCE_AUDIO_CAPTURE\.equals\(resource\)\)\s*\{\s*return Manifest\.permission\.RECORD_AUDIO;",
        )
        self.assertIn("isTrustedLocalOrigin(request.getOrigin())", source)
        self.assertIn("webPermissionFlow.request(request, request.getResources())", source)
        self.assertIn("webPermissionFlow.onPermissionResult(permissions, granted)", source)
        self.assertIn("webPermissionFlow.cancel(request)", source)

    def test_activity_distinguishes_permission_dialog_pause_from_stop_or_navigation(self):
        source = JAVA.read_text(encoding="utf-8")
        pause = source.split("protected void onPause()", 1)[1].split("protected void onResume()", 1)[0]
        resume = source.split("protected void onResume()", 1)[1].split("protected void onStop()", 1)[0]
        stop = source.split("protected void onStop()", 1)[1].split("protected void onSaveInstanceState", 1)[0]
        navigation = source.split("public void onPageStarted(", 1)[1].split("public void onPageFinished(", 1)[0]
        self.assertIn("webPermissionFlow.setResumed(false)", pause)
        self.assertNotIn("webPermissionFlow.denyPending()", pause)
        self.assertLess(resume.index("navigateToPendingIntent"), resume.index("webPermissionFlow.setResumed(true)"))
        self.assertIn("webPermissionFlow.denyPending()", stop)
        self.assertIn("webPermissionFlow.denyPending()", navigation)


if __name__ == "__main__":
    unittest.main()
