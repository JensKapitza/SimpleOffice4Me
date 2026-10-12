"""Black-box APK verification CLI: packaging contracts, independently sized fixtures."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

TOOL = Path(__file__).resolve().parents[1] / 'tools/check_android_launcher_assets.py'


class AndroidLauncherArtifactTests(unittest.TestCase):
    def invoke(self, *, omit='', wrong_size=False, corrupt=False):
        with tempfile.TemporaryDirectory() as temp:
            apk = Path(temp) / 'fixture.apk'
            with zipfile.ZipFile(apk, 'w') as archive:
                for density, size in [('mdpi', 48), ('hdpi', 72), ('xhdpi', 96), ('xxhdpi', 144), ('xxxhdpi', 192)]:
                    for kind in ('ic_launcher', 'ic_launcher_round'):
                        name = f'res/mipmap-{density}-v4/{kind}.png'
                        if name == omit:
                            continue
                        image = io.BytesIO()
                        dimensions = (10, 10) if wrong_size and density == 'mdpi' else (size, size)
                        Image.new('RGBA', dimensions, (200, 20, 20, 255)).save(image, 'PNG')
                        archive.writestr(name, b'not a PNG' if corrupt else image.getvalue())
                image = io.BytesIO()
                Image.new('RGBA', (216, 216), (200, 20, 20, 255)).save(image, 'PNG')
                archive.writestr('res/drawable-nodpi-v4/simpleoffice_logo_foreground.png', image.getvalue())
            return subprocess.run([sys.executable, str(TOOL), str(apk)], capture_output=True, text=True, check=False)

    def test_valid_apk_reports_standard_android_launcher_sizes(self):
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({'ok': True, 'launcher_sizes': {
            'mdpi': [48, 48], 'hdpi': [72, 72], 'xhdpi': [96, 96],
            'xxhdpi': [144, 144], 'xxxhdpi': [192, 192]},
            'adaptive_foreground': [216, 216]}, json.loads(result.stdout))

    def test_missing_density_is_a_build_failure(self):
        result = self.invoke(omit='res/mipmap-hdpi-v4/ic_launcher.png')
        self.assertEqual(1, result.returncode)
        self.assertFalse(json.loads(result.stdout)['ok'])

    def test_wrong_dimensions_are_a_build_failure(self):
        result = self.invoke(wrong_size=True)
        self.assertEqual(1, result.returncode)
        self.assertFalse(json.loads(result.stdout)['ok'])

    def test_corrupt_image_is_a_build_failure(self):
        result = self.invoke(corrupt=True)
        self.assertEqual(1, result.returncode)
        self.assertFalse(json.loads(result.stdout)['ok'])
