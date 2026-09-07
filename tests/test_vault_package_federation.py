import tempfile
import unittest
from pathlib import Path

from app.vault_package_federation import (
    load_manifest,
    missing_chunks,
    peer_chunk_names,
    reassemble_encrypted_package,
    split_encrypted_package,
)
from app.vault_packages import create_vault_package, extract_vault_package


class VaultPackageFederationTests(unittest.TestCase):
    def test_split_assign_reassemble_and_extract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "document.bin"
            source.write_bytes((b"sample-data-" * 20000) + b"end")
            package = root / "document.sovp"
            created = create_vault_package([source], package)

            chunks = root / "chunks"
            manifest = split_encrypted_package(
                package, chunks, peers=["peer-a", "peer-b", "peer-c"], replicas=2, chunk_size=64 * 1024
            )
            loaded = load_manifest(chunks / "manifest.json")
            self.assertEqual(manifest["package_sha256"], loaded["package_sha256"])
            self.assertEqual([], missing_chunks(loaded, [chunks]))
            self.assertTrue(peer_chunk_names(loaded, "peer-a"))
            self.assertTrue(all(len(loaded["assignments"][peer]) < loaded["chunk_count"] for peer in loaded["assignments"]))

            rebuilt = root / "rebuilt.sovp"
            reassemble_encrypted_package(loaded, [chunks], rebuilt)
            self.assertEqual(package.read_bytes(), rebuilt.read_bytes())

            restore = root / "restore"
            extract_vault_package(rebuilt, restore, recovery_key=created["recovery_key"])
            self.assertEqual(source.read_bytes(), (restore / source.name).read_bytes())

    def test_missing_or_modified_chunk_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "payload.bin"
            source.write_bytes(b"x" * 200000)
            package = root / "payload.sovp"
            create_vault_package([source], package)
            chunks = root / "chunks"
            manifest = split_encrypted_package(
                package, chunks, peers=["peer-a", "peer-b"], replicas=1, chunk_size=64 * 1024
            )
            first = chunks / manifest["chunks"][0]["name"]
            raw = bytearray(first.read_bytes())
            raw[0] ^= 1
            first.write_bytes(raw)
            self.assertIn(0, missing_chunks(manifest, [chunks]))
            with self.assertRaises(ValueError):
                reassemble_encrypted_package(manifest, [chunks], root / "bad.sovp")


if __name__ == "__main__":
    unittest.main()
