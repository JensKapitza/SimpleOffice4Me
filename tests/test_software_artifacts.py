import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.software_artifacts import SoftwareArtifactStore


class SoftwareArtifactStoreTests(unittest.TestCase):
    def test_cache_stream_persists_installer_by_sha256(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SoftwareArtifactStore(Path(temp) / "documents")
            payload = b"fake-windows-installer"
            entry = store.cache_stream(io.BytesIO(payload), "SimpleOffice4Me-Setup.exe", source="test")

            self.assertEqual(hashlib.sha256(payload).hexdigest(), entry["sha256"])
            self.assertEqual("windows", entry["platform"])
            self.assertEqual("exe", entry["kind"])
            self.assertEqual(payload, store.artifact_path(entry["sha256"]).read_bytes())
            self.assertEqual(1, len(store.catalog()))

    def test_cache_rejects_unknown_file_type(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SoftwareArtifactStore(Path(temp) / "documents")
            with self.assertRaisesRegex(ValueError, "Nicht unterstütztes"):
                store.cache_stream(io.BytesIO(b"data"), "payload.sh")

    def test_cache_verifies_expected_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SoftwareArtifactStore(Path(temp) / "documents")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                store.cache_stream(
                    io.BytesIO(b"data"),
                    "SimpleOffice4Me.apk",
                    expected_sha256="a" * 64,
                )
            self.assertEqual([], store.catalog())

    def test_offer_catalog_is_recorded_without_downloading_blob(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SoftwareArtifactStore(Path(temp) / "documents")
            digest = "b" * 64
            result = store.record_offer(
                "peer-a",
                [{
                    "sha256": digest,
                    "name": "SimpleOffice4Me.dmg",
                    "size": 12345,
                    "platform": "macos",
                    "kind": "dmg",
                    "revision": "c" * 40,
                }],
            )
            self.assertEqual(1, len(result))
            self.assertEqual("peer-a", store.offers()[0]["peer_id"])
            self.assertEqual([], store.catalog())

    def test_import_github_actions_archive_keeps_only_supported_root_installers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = SoftwareArtifactStore(root / "documents")
            archive = root / "artifact.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("SimpleOffice4Me-Setup.exe", b"installer")
                package.writestr("SimpleOffice4Me-Setup.exe.sha256", b"ignored")
                package.writestr("win-unpacked/helper.exe", b"ignored unpacked tree")
            imported = store.import_actions_archive(
                archive,
                source="github:simpleoffice4me-desktop-windows",
                platform="windows",
                revision="d" * 40,
            )
            self.assertEqual(1, len(imported))
            self.assertEqual("SimpleOffice4Me-Setup.exe", imported[0]["name"])
            self.assertEqual("windows", imported[0]["platform"])

    def test_delete_removes_blob_and_catalog_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SoftwareArtifactStore(Path(temp) / "documents")
            entry = store.cache_stream(io.BytesIO(b"apk"), "SimpleOffice4Me.apk")
            store.delete(entry["sha256"])
            self.assertEqual([], store.catalog())
            with self.assertRaisesRegex(ValueError, "nicht im Offline-Cache"):
                store.artifact_path(entry["sha256"])


if __name__ == "__main__":
    unittest.main()
