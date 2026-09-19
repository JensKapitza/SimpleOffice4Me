import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from app.v2.blob_store import BlobStore
from app.v2.contracts import LogicalObjectId
from app.v2.recovery import RecoveryService
from app.v2.recovery_cli import main


class V2RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.object_id = LogicalObjectId("recovery-document")
        self.version = BlobStore(self.root, chunk_size=64 * 1024).write(self.object_id, b"recovery payload")

    def tearDown(self):
        self.temp.cleanup()

    def test_recovery_service_verifies_and_exports_without_flask(self):
        service = RecoveryService(self.root)
        result = service.verify(self.object_id.value)
        self.assertTrue(result.valid)
        target = self.root / "export.bin"
        service.export(self.object_id.value, target)
        self.assertEqual(b"recovery payload", target.read_bytes())

    def test_descriptor_contains_no_key_material_and_detects_manifest_change(self):
        service = RecoveryService(self.root)
        descriptor = service.descriptor(self.object_id.value)
        serialized = json.dumps(descriptor).casefold()
        self.assertNotIn("password", serialized)
        self.assertNotIn("master_key", serialized)
        self.assertNotIn("recovery_key", serialized)
        self.assertTrue(service.verify_descriptor(descriptor).valid)

        descriptor["size"] += 1
        self.assertFalse(service.verify_descriptor(descriptor).valid)

    def test_cli_is_read_only_without_apply(self):
        target = self.root / "cli-export.bin"
        output = StringIO()
        with redirect_stdout(output):
            code = main([
                "--root", str(self.root),
                "export",
                "--object-id", self.object_id.value,
                "--output", str(target),
            ])
        self.assertEqual(3, code)
        self.assertFalse(target.exists())
        self.assertIn("read-only mode", output.getvalue())

    def test_cli_export_requires_explicit_apply(self):
        target = self.root / "cli-export.bin"
        with redirect_stdout(StringIO()):
            code = main([
                "--root", str(self.root),
                "export",
                "--object-id", self.object_id.value,
                "--output", str(target),
                "--apply",
            ])
        self.assertEqual(0, code)
        self.assertEqual(b"recovery payload", target.read_bytes())


if __name__ == "__main__":
    unittest.main()
