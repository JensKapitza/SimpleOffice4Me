"""Public SBOM CLI contracts with an independently declared package inventory."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / 'tools/generate_sbom.py'


class CargoSbomCliTests(unittest.TestCase):
    def test_declared_locked_packages_and_licenses_are_exported_without_host_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata, output = Path(directory) / 'cargo.json', Path(directory) / 'sbom.json'
            metadata.write_text(json.dumps({'resolve': {'root': 'app-id'}, 'packages': [
                {'id': 'app-id', 'name': 'fixture-app', 'version': '2.4.6', 'license': 'GPL-3.0-only'},
                {'id': 'lib-id', 'name': 'fixture-lib', 'version': '1.2.3', 'license': 'MIT OR Apache-2.0'}]}))
            result = subprocess.run([sys.executable, str(TOOL), '--cargo-metadata', str(metadata),
                                     '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            sbom = json.loads(output.read_text())
            self.assertEqual('CycloneDX', sbom['bomFormat'])
            self.assertEqual({'type': 'application', 'name': 'fixture-app', 'version': '2.4.6'},
                             sbom['metadata']['component'])
            self.assertEqual([
                {'type': 'application', 'name': 'fixture-app', 'version': '2.4.6',
                 'purl': 'pkg:cargo/fixture-app@2.4.6', 'licenses': [{'expression': 'GPL-3.0-only'}]},
                {'type': 'library', 'name': 'fixture-lib', 'version': '1.2.3',
                 'purl': 'pkg:cargo/fixture-lib@1.2.3', 'licenses': [{'expression': 'MIT OR Apache-2.0'}]},
            ], sbom['components'])

    def test_missing_resolved_root_is_rejected_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata, output = Path(directory) / 'cargo.json', Path(directory) / 'sbom.json'
            metadata.write_text('{"packages":[],"resolve":{"root":"missing"}}')
            result = subprocess.run([sys.executable, str(TOOL), '--cargo-metadata', str(metadata),
                                     '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(2, result.returncode)
            self.assertIn('resolved root package', result.stderr)
            self.assertFalse(output.exists())
