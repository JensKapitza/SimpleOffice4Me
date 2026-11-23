"""Regression checks for Android WebView microphone permission routing."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android/apk/app/src/main/java/de/simpleoffice4me/android/MainActivity.java"
MANIFEST = ROOT / "android/apk/app/src/main/AndroidManifest.xml"


class AndroidWebViewMicrophonePermissionTests(unittest.TestCase):
    def test_manifest_declares_microphone(self):
        manifest = MANIFEST.read_text(encoding="utf-8")
        self.assertIn('android.permission.RECORD_AUDIO', manifest)
        self.assertIn('android.hardware.microphone', manifest)

    def test_webview_routes_audio_and_video(self):
        source = JAVA.read_text(encoding="utf-8")
        handler = source.split('private void handleWebPermissionRequest(PermissionRequest request)', 1)[1].split('private static boolean isTrustedLocalOrigin', 1)[0]
        self.assertIn('PermissionRequest.RESOURCE_AUDIO_CAPTURE', handler)
        self.assertIn('Manifest.permission.RECORD_AUDIO', handler)
        self.assertIn('PermissionRequest.RESOURCE_VIDEO_CAPTURE', handler)
        self.assertIn('Manifest.permission.CAMERA', handler)
        self.assertIn('isTrustedLocalOrigin(request.getOrigin())', handler)
        self.assertIn('request.deny()', handler)
        self.assertIn('requestPermissions(', handler)

    def test_permission_result_grants_requested_resources(self):
        source = JAVA.read_text(encoding="utf-8")
        self.assertIn('request.grant(resources)', source)
        self.assertIn('pendingWebResources = null;', source)


if __name__ == "__main__":
    unittest.main()
