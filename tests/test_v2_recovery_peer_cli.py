import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app.v2.recovery_cli import main


class EncryptedRecoveryPeerCliTests(unittest.TestCase):
    def setUp(self):
        self.descriptor = {
            "descriptor_id": "descriptor-test",
            "object_id": "object-test",
            "version_id": "version-test",
            "ciphertext_chunks": [{}],
        }
        self.availability = {
            "format": "simpleoffice-v2-encrypted-recovery-availability/v1",
            "descriptor_id": "descriptor-test",
            "object_id": "object-test",
            "version_id": "version-test",
            "total_chunks": 1,
            "requested_indexes": [0],
            "available_indexes": [0],
            "missing_indexes": [],
        }

    def test_peer_availability_is_read_only_and_descriptor_scoped(self):
        with tempfile.TemporaryDirectory() as root, \
                patch(
                    "app.v2.recovery_cli.load_encrypted_recovery_descriptor",
                    return_value=self.descriptor,
                ), \
                patch(
                    "app.federation_worker.remote_encrypted_recovery_availability",
                    return_value=self.availability,
                ) as query:
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "--root",
                    root,
                    "encrypted-peer-availability",
                    "--descriptor",
                    "descriptor.json",
                    "--peer",
                    "peer-a",
                    "--authorization-ref",
                    "grant-a",
                    "--chunk-index",
                    "0",
                ])

        self.assertEqual(0, code)
        self.assertEqual(self.availability, json.loads(output.getvalue()))
        query.assert_called_once_with(
            root,
            "peer-a",
            self.descriptor,
            "grant-a",
            chunk_indexes=[0],
        )

    def test_peer_fetch_requires_apply_before_caching(self):
        with tempfile.TemporaryDirectory() as root, \
                patch(
                    "app.v2.recovery_cli.load_encrypted_recovery_descriptor",
                    return_value=self.descriptor,
                ), \
                patch(
                    "app.federation_worker.remote_encrypted_recovery_availability",
                    return_value=self.availability,
                ), \
                patch(
                    "app.federation_worker.remote_encrypted_recovery_chunk",
                ) as fetch, \
                patch(
                    "app.v2.recovery_cli.EncryptedRecoveryChunkSearch",
                ) as local_cache:
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "--root",
                    root,
                    "encrypted-peer-fetch",
                    "--descriptor",
                    "descriptor.json",
                    "--peer",
                    "peer-a",
                    "--authorization-ref",
                    "grant-a",
                    "--chunk-index",
                    "0",
                ])

        self.assertEqual(3, code)
        self.assertIn("read-only mode", output.getvalue())
        fetch.assert_not_called()
        local_cache.assert_not_called()

    def test_peer_fetch_verifies_remotely_then_caches_only_with_apply(self):
        payload = b"verified-ciphertext"
        with tempfile.TemporaryDirectory() as root, \
                patch(
                    "app.v2.recovery_cli.load_encrypted_recovery_descriptor",
                    return_value=self.descriptor,
                ), \
                patch(
                    "app.federation_worker.remote_encrypted_recovery_availability",
                    return_value=self.availability,
                ), \
                patch(
                    "app.federation_worker.remote_encrypted_recovery_chunk",
                    return_value=payload,
                ) as fetch, \
                patch(
                    "app.v2.recovery_cli.EncryptedRecoveryChunkSearch",
                ) as local_cache:
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "--root",
                    root,
                    "encrypted-peer-fetch",
                    "--descriptor",
                    "descriptor.json",
                    "--peer",
                    "peer-a",
                    "--authorization-ref",
                    "grant-a",
                    "--chunk-index",
                    "0",
                    "--apply",
                ])

        self.assertEqual(0, code)
        self.assertTrue(json.loads(output.getvalue())["cached"])
        fetch.assert_called_once_with(
            root,
            "peer-a",
            self.descriptor,
            "grant-a",
            0,
        )
        local_cache.return_value.store_chunk.assert_called_once_with(
            self.descriptor,
            0,
            payload,
        )

    def test_peer_fetch_reports_missing_chunk_without_transfer(self):
        missing = {
            **self.availability,
            "available_indexes": [],
            "missing_indexes": [0],
        }
        with tempfile.TemporaryDirectory() as root, \
                patch(
                    "app.v2.recovery_cli.load_encrypted_recovery_descriptor",
                    return_value=self.descriptor,
                ), \
                patch(
                    "app.federation_worker.remote_encrypted_recovery_availability",
                    return_value=missing,
                ), \
                patch(
                    "app.federation_worker.remote_encrypted_recovery_chunk",
                ) as fetch:
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "--root",
                    root,
                    "encrypted-peer-fetch",
                    "--descriptor",
                    "descriptor.json",
                    "--peer",
                    "peer-a",
                    "--authorization-ref",
                    "grant-a",
                    "--chunk-index",
                    "0",
                    "--apply",
                ])

        self.assertEqual(2, code)
        self.assertFalse(json.loads(output.getvalue())["available"])
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
