"""Regression checks for branded Android launcher resources."""
from pathlib import Path
from PIL import Image
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android/apk/app/src/main"
LOGO = ROOT / "docs/assets/simpleoffice-documentation-logo.png"
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


class AndroidLauncherLogoTests(unittest.TestCase):
    def test_source_is_a_valid_sized_png(self):
        with Image.open(LOGO) as image:
            image.load()
            self.assertEqual('PNG', image.format)
            self.assertGreaterEqual(image.width, 192)
            self.assertEqual(image.width, image.height)
            self.assertIsNotNone(image.convert('RGBA').getchannel('A').getbbox())

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



if __name__ == "__main__":
    unittest.main()
