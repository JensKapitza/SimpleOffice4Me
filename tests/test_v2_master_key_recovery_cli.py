import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.v2.contracts import OperationResult
from app.v2.master_keys import MasterKeyProfileStore, encode_recovery_key
from app.v2.recovery_cli import main


class _Audit:
    def append(self, event):
        return OperationResult.success("audit-test")


class MasterKeyRecoveryCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        store = MasterKeyProfileStore(self.root, "synthetic-admin", audit_port=_Audit())
        self.material = store.create("synthetic-profile", "correct synthetic password")
        self.bundle = self.root / "recovery.json"
        self.bundle.write_bytes(self.material.recovery_bundle)
        self.key_file = self.root / "recovery.key"
        self.key_file.write_text(encode_recovery_key(self.material.recovery_key) + "\n", encoding="ascii")
        if os.name == "posix":
            os.chmod(self.key_file, 0o600)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, key_file=None):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([
                "master-key-recovery-check",
                "--bundle", str(self.bundle),
                "--recovery-key-file", str(key_file or self.key_file),
            ])
        return code, output.getvalue()

    def test_check_requires_no_simpleoffice_root_and_never_exports_master_key(self):
        code, output = self._run()
        result = json.loads(output)

        self.assertEqual(0, code)
        self.assertTrue(result["valid"])
        self.assertFalse(result["master_key_exported"])
        self.assertFalse(result["contains_raw_master_key"])
        self.assertFalse(result["contains_raw_recovery_key"])
        self.assertNotIn(encode_recovery_key(self.material.recovery_key), output)

    def test_wrong_recovery_key_returns_generic_failure_without_secret_echo(self):
        wrong = self.root / "wrong.key"
        wrong_value = encode_recovery_key(b"x" * 32)
        wrong.write_text(wrong_value, encoding="ascii")

        code, output = self._run(wrong)
        result = json.loads(output)

        self.assertEqual(2, code)
        self.assertFalse(result["valid"])
        self.assertNotIn(wrong_value, output)
        self.assertNotIn("ciphertext", output)
        self.assertNotIn("nonce", output)

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "O_NOFOLLOW"),
        "no-follow symlink test requires POSIX O_NOFOLLOW",
    )
    def test_recovery_key_symlink_is_rejected(self):
        link = self.root / "linked.key"
        link.symlink_to(self.key_file)

        code, output = self._run(link)

        self.assertEqual(2, code)
        self.assertFalse(json.loads(output)["valid"])


if __name__ == "__main__":
    unittest.main()
