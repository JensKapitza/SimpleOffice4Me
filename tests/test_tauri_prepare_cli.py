"""Public preparation CLI: central release version and existing shared assets."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / 'tools/prepare_tauri_client.py'


class TauriPreparationContractTests(unittest.TestCase):
    def fixture(self, root, version='2.4.6'):
        files = {
            'pyproject.toml': '[project]\nversion="' + version + '"\n',
            'desktop/tauri/package.json': '{"name":"fixture-client","version":"0.1.0","private":true}',
            'desktop/tauri/src-tauri/tauri.conf.json': '{"version":"0.1.0","identifier":"fixture.identity"}',
            'desktop/tauri/src-tauri/Cargo.toml': '[package]\nname="fixture-client"\nversion="0.1.0"\n',
            'static/vendor/bootstrap-5.3.8/css/bootstrap.min.css': 'synthetic-bootstrap-asset',
            'static/css/app.css': 'synthetic-shared-app-asset',
            'docs/assets/simpleoffice-documentation-logo.png': 'synthetic-source-logo-asset',
        }
        for name, value in files.items():
            path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value)

    def invoke(self, root):
        return subprocess.run([sys.executable, str(TOOL), '--root', str(root)],
                              capture_output=True, text=True, check=False)

    def test_declared_release_version_and_assets_are_integrated_without_losing_package_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.fixture(root)
            result = self.invoke(root)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual({'ok': True, 'version': '2.4.6',
                'assets': ['bootstrap.min.css', 'app.css', 'logo.png']}, json.loads(result.stdout))
            client = root / 'desktop/tauri'
            self.assertEqual({'name': 'fixture-client', 'private': True, 'version': '2.4.6'},
                             json.loads((client / 'package.json').read_text()))
            self.assertEqual({'identifier': 'fixture.identity', 'version': '2.4.6'},
                             json.loads((client / 'src-tauri/tauri.conf.json').read_text()))
            self.assertIn('version="2.4.6"', (client / 'src-tauri/Cargo.toml').read_text())
            self.assertEqual('synthetic-shared-app-asset', (client / 'web/assets/app.css').read_text())
            self.assertEqual('synthetic-source-logo-asset', (client / 'web/assets/logo.png').read_text())

    def test_invalid_project_version_fails_before_changing_client_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self.fixture(root, version='invalid-version')
            result = self.invoke(root)
            self.assertEqual(1, result.returncode)
            self.assertFalse(json.loads(result.stdout)['ok'])
            self.assertEqual('0.1.0', json.loads((root / 'desktop/tauri/package.json').read_text())['version'])
            self.assertFalse((root / 'desktop/tauri/web/assets').exists())

    def test_missing_project_metadata_returns_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.invoke(Path(temp))
            self.assertEqual(1, result.returncode)
            self.assertEqual({'ok': False, 'error': 'Client metadata or shared assets are invalid'},
                             json.loads(result.stdout))
