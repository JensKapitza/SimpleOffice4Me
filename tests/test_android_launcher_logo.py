"""Regression checks for branded Android launcher resources."""
from pathlib import Path
import struct
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/apk/app/src/main"
LOGO = ROOT / "docs/assets/simpleoffice-documentation-logo.png"
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


class AndroidLauncherLogoTests(unittest.TestCase):
    def test_source_is_a_valid_sized_png(self):
        data = LOGO.read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(data[12:16], b"IHDR")
        width, height = struct.unpack(">II", data[16:24])
        self.assertGreaterEqual(width, 192)
        self.assertGreaterEqual(height, 192)
        self.assertEqual(width, height)

    def test_manifest_references_generated_launcher_assets(self):
        app = ET.parse(ANDROID / "AndroidManifest.xml").getroot().find("application")
        self.assertIsNotNone(app)
        self.assertEqual(app.attrib[ANDROID_NS + "icon"], "@mipmap/ic_launcher")
        self.assertEqual(app.attrib[ANDROID_NS + "roundIcon"], "@mipmap/ic_launcher")

    def test_adaptive_icons_reference_generated_foreground(self):
        for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
            with self.subTest(name=name):
                root = ET.parse(ANDROID / "res/mipmap-anydpi-v26" / name).getroot()
                self.assertTrue(root.tag.endswith("adaptive-icon"))
                foreground = root.find("foreground")
                self.assertEqual(
                    foreground.attrib[ANDROID_NS + "drawable"],
                    "@drawable/simpleoffice_logo_foreground",
                )

    def test_gradle_regenerates_icons_from_canonical_logo(self):
        build = (ROOT / "android/apk/app/build.gradle").read_text(encoding="utf-8")
        self.assertIn("docs/assets/simpleoffice-documentation-logo.png", build)
        self.assertIn("preBuild.dependsOn generateSimpleOfficeLauncherIcons", build)
        self.assertIn("res.srcDir", build)
        for density in ("mdpi", "hdpi", "xhdpi", "xxhdpi", "xxxhdpi"):
            self.assertIn("'"+density+"':", build)
        self.assertIn("simpleoffice_logo_foreground.png", build)


if __name__ == "__main__":
    unittest.main()
