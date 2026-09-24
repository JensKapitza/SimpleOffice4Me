import json
import os
import sqlite3
from app.sqlite_utils import connect as sqlite_connect
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.password_vault import PasswordVault, _canonical, _entry_aad
from app.v2.contracts import LogicalObjectId
from app.v2.vault_payload_store import VaultPayloadStore


class V2VaultObjectPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.password = "correct horse battery staple"
        self.vault = PasswordVault(self.root)
        self.key = self.vault.create("alice", self.password)

    def tearDown(self):
        self.temp.cleanup()

    def _db_row(self, entry_id):
        db = sqlite_connect(self.vault.path)
        db.row_factory = sqlite3.Row
        try:
            return db.execute(
                "SELECT * FROM vault_entry WHERE user_id=? AND entry_id=?",
                ("alice", entry_id),
            ).fetchone()
        finally:
            db.close()

    def test_new_payload_uses_isolated_v2_object_store_not_sqlite_ciphertext(self):
        written = self.vault.put(
            "alice",
            self.key,
            {
                "type": "login",
                "name": "Example",
                "username": "alice@example.test",
                "password": "object-store-secret",
                "notes": "private object note",
            },
        )
        row = self._db_row(written["entry_id"])
        self.assertEqual(b"", bytes(row["nonce"]))
        self.assertEqual(b"", bytes(row["ciphertext"]))
        self.assertTrue(row["payload_object_id"])
        self.assertTrue(row["payload_object_version"])

        secret_store = VaultPayloadStore(self.root, "alice")
        result = secret_store.storage.read_bytes(LogicalObjectId(row["payload_object_id"]))
        self.assertTrue(result.ok)
        raw = result.value
        self.assertNotIn(b"object-store-secret", raw)
        self.assertNotIn(b"private object note", raw)

        normal_catalog = self.root / ".simpleoffice-v2" / "catalog.sqlite3"
        self.assertFalse(normal_catalog.exists())
        self.assertTrue(secret_store.storage.catalog.path.exists())
        self.assertTrue(str(secret_store.storage.catalog.path).startswith(str(self.root / ".simpleoffice-meta")))

        values = self.vault.entries("alice", self.key)
        self.assertEqual("object-store-secret", values[0]["data"]["password"])

    def test_secret_object_store_root_is_private_on_posix(self):
        store = VaultPayloadStore(self.root, "alice")
        if os.name != "posix":
            self.skipTest("POSIX permission bits are not authoritative on this platform")
        container = store.root.parent
        self.assertEqual(0o700, container.stat().st_mode & 0o777)
        self.assertEqual(0o700, store.root.stat().st_mode & 0o777)

    def test_unlock_migrates_verified_legacy_ciphertext(self):
        entry_id = "11111111-1111-4111-8111-111111111111"
        payload = {
            "type": "login",
            "name": "Legacy",
            "username": "alice",
            "password": "legacy-secret",
        }
        nonce = b"123456789012"
        ciphertext = AESGCM(self.key).encrypt(
            nonce,
            _canonical(payload),
            _entry_aad("alice", entry_id, 1),
        )
        now = 1_790_000_000
        db = sqlite_connect(self.vault.path)
        try:
            db.execute(
                """INSERT INTO vault_entry(
                       user_id,entry_id,revision,nonce,ciphertext,created_at,updated_at,deleted_at
                   ) VALUES(?,?,?,?,?,?,?,NULL)""",
                ("alice", entry_id, 1, nonce, ciphertext, now, now),
            )
            db.commit()
        finally:
            db.close()

        unlocked = self.vault.unlock("alice", self.password)
        self.assertEqual(self.key, unlocked)
        row = self._db_row(entry_id)
        self.assertEqual(b"", bytes(row["nonce"]))
        self.assertEqual(b"", bytes(row["ciphertext"]))
        self.assertTrue(row["payload_object_id"])
        self.assertEqual("legacy-secret", self.vault.entries("alice", unlocked)[0]["data"]["password"])

    def test_object_backed_backup_remains_portable_and_secret_safe(self):
        self.vault.put(
            "alice",
            self.key,
            {
                "type": "login",
                "name": "Portable",
                "url": "https://example.test/",
                "password": "portable-secret",
            },
        )
        backup = self.vault.export_backup("alice")
        self.assertNotIn(b"portable-secret", backup)
        envelope = json.loads(backup)
        item = envelope["payload"]["entries"][0]
        self.assertTrue(item["nonce"])
        self.assertTrue(item["ciphertext"])
        self.assertNotIn("payload_object_id", item)

        with tempfile.TemporaryDirectory() as target:
            restored = PasswordVault(Path(target))
            result = restored.import_backup(backup, target_user_id="alice")
            self.assertEqual(1, result["entries"])
            restored_key = restored.unlock("alice", self.password)
            rows = restored.entries("alice", restored_key)
            self.assertEqual("portable-secret", rows[0]["data"]["password"])
            db = sqlite_connect(restored.path)
            db.row_factory = sqlite3.Row
            try:
                raw = db.execute("SELECT * FROM vault_entry WHERE user_id='alice'").fetchone()
            finally:
                db.close()
            self.assertEqual(b"", bytes(raw["nonce"]))
            self.assertTrue(raw["payload_object_id"])

    def test_tampered_object_store_payload_fails_closed(self):
        written = self.vault.put(
            "alice",
            self.key,
            {"type": "login", "name": "Tamper", "password": "safe-secret"},
        )
        row = self._db_row(written["entry_id"])
        store = VaultPayloadStore(self.root, "alice")
        entry = store.storage.catalog.get(LogicalObjectId(row["payload_object_id"])).value
        manifest = store.storage.blobs.current_manifest(entry.object_id)
        chunk_id = manifest["chunks"][0]["physical_id"]
        chunk = store.storage.blobs._chunk_path(chunk_id)
        data = bytearray(chunk.read_bytes())
        data[-1] ^= 1
        chunk.write_bytes(bytes(data))
        with self.assertRaises(RuntimeError):
            self.vault.entries("alice", self.key)


if __name__ == "__main__":
    unittest.main()
