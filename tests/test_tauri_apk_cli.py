"""APK compatibility checks via the public CLI and independent ELF fixtures."""
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / 'tools/check_tauri_apk.py'
# Independently specified ELF64 ARM64 header and one PT_LOAD program header.
ELF = bytes.fromhex(
    '7f454c46020101000000000000000000'
    '0300b70001000000' '0000000000000000' '4000000000000000'
    '0000000000000000' '00000000' '400038000100000000000000'
    '0100000005000000' '0000000000000000' '0000000000000000'
    '0000000000000000' '7800000000000000' '7800000000000000'
    '0040000000000000'
)


class TauriApkCliTests(unittest.TestCase):
    def invoke(self, payload=None):
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / 'client.apk'
            with zipfile.ZipFile(apk, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                if payload is not None:
                    archive.writestr('lib/arm64-v8a/libclient.so', payload)
            result = subprocess.run([sys.executable, str(TOOL), str(apk)], capture_output=True, text=True)
            return result.returncode, json.loads(result.stdout)

    def test_arm64_16k_library_is_accepted(self):
        self.assertEqual((0, {'ok': True, 'abi': 'arm64-v8a', 'page_size': 16384, 'libraries': 1}), self.invoke(ELF))

    def test_4k_library_is_rejected_for_16k_android_devices(self):
        status, output = self.invoke(ELF[:-8] + bytes.fromhex('0010000000000000'))
        self.assertEqual(1, status)
        self.assertFalse(output['ok'])
        self.assertIn('16 KiB', output['error'])

    def test_missing_or_truncated_native_library_is_rejected(self):
        for payload in (None, ELF[:60]):
            with self.subTest(payload=payload):
                status, output = self.invoke(payload)
                self.assertEqual(1, status)
                self.assertFalse(output['ok'])
