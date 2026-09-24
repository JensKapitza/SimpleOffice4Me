from __future__ import annotations

import hashlib
import json
import secrets
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.password_vault import (
    KDF_N,
    KDF_P,
    KDF_R,
    PasswordVault,
    _canonical,
    _kdf,
    _profile_aad,
)
from app.v2.crypto import CryptoService
from app.v2.vault_key_hierarchy import (
    decode_password_protection,
    recover_vault_key_from_bundle,
)


class V2PasswordVaultKeyHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.password = secrets.token_urlsafe(24)
        self.vault = PasswordVault(self.root)
        self.key = self.vault.create("alice", self.password)

    def tearDown(self):
        self.temp.cleanup()

    def _install_legacy_password_wrap(self, password: str) -> None:
        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(12)
        wrapping_key = _kdf(password, salt)
        wrapped = AESGCM(wrapping_key).encrypt(
            nonce,
            self.key,
            _profile_aad("alice"),
        )
        with self.vault._db() as db:
            db.execute(
                """UPDATE vault_profile
                   SET salt=?,wrap_nonce=?,wrapped_key=?,kdf_n=?,kdf_r=?,kdf_p=?,
                       key_protection=''
                   WHERE user_id='alice'""",
                (salt, nonce, wrapped, KDF_N, KDF_R, KDF_P),
            )

    def test_new_profile_uses_central_argon2id_protection_only(self):
        with self.vault._db() as db:
            row = db.execute(
                "SELECT * FROM vault_profile WHERE user_id='alice'"
            ).fetchone()

        protected = decode_password_protection(str(row["key_protection"]))
        self.assertEqual("argon2id-aes256gcm", protected.method)
        self.assertEqual(b"", bytes(row["salt"]))
        self.assertEqual(b"", bytes(row["wrap_nonce"]))
        self.assertEqual(b"", bytes(row["wrapped_key"]))
        self.assertEqual(0, int(row["kdf_n"]))
        self.assertEqual(self.key, self.vault.unlock("alice", self.password))
        with self.assertRaisesRegex(ValueError, "Master-Passwort"):
            self.vault.unlock("alice", secrets.token_urlsafe(24))

    def test_master_password_change_rewraps_only_the_vault_key(self):
        entry = self.vault.put(
            "alice",
            self.key,
            {"type": "secure_note", "name": "Synthetic", "notes": "payload"},
        )
        new_password = secrets.token_urlsafe(26)

        self.vault.change_master_password("alice", self.password, new_password)

        with self.assertRaises(ValueError):
            self.vault.unlock("alice", self.password)
        unlocked = self.vault.unlock("alice", new_password)
        self.assertEqual(self.key, unlocked)
        rows = self.vault.entries("alice", unlocked)
        self.assertEqual(entry["entry_id"], rows[0]["entry_id"])
        with self.vault._db() as db:
            row = db.execute(
                "SELECT key_protection,kdf_n,salt FROM vault_profile WHERE user_id='alice'"
            ).fetchone()
        self.assertTrue(str(row["key_protection"]))
        self.assertEqual(0, int(row["kdf_n"]))
        self.assertEqual(b"", bytes(row["salt"]))

    def test_legacy_scrypt_profile_migrates_after_successful_unlock(self):
        legacy_password = "abcdefghij"
        self._install_legacy_password_wrap(legacy_password)

        unlocked = self.vault.unlock("alice", legacy_password)

        self.assertEqual(self.key, unlocked)
        with self.vault._db() as db:
            row = db.execute(
                "SELECT * FROM vault_profile WHERE user_id='alice'"
            ).fetchone()
        self.assertTrue(str(row["key_protection"]))
        self.assertEqual(
            "argon2id-aes256gcm",
            decode_password_protection(str(row["key_protection"])).method,
        )
        self.assertEqual(b"", bytes(row["salt"]))
        self.assertEqual(0, int(row["kdf_n"]))
        self.assertEqual(self.key, self.vault.unlock("alice", legacy_password))

    def test_legacy_kdf_policy_tamper_fails_before_scrypt_work(self):
        self._install_legacy_password_wrap("abcdefghij")
        with self.vault._db() as db:
            db.execute(
                "UPDATE vault_profile SET kdf_n=? WHERE user_id='alice'",
                (KDF_N * 2,),
            )

        with self.assertRaisesRegex(ValueError, "Master-Passwort"):
            self.vault.unlock("alice", "abcdefghij")

    def test_offline_recovery_bundle_recovers_key_without_live_vault(self):
        material = self.vault.enable_recovery("alice", self.key)
        bundle = self.vault.export_recovery_bundle("alice", self.key)

        self.assertEqual(material.recovery_bundle, bundle)
        self.assertEqual(
            self.key,
            recover_vault_key_from_bundle(bundle, material.recovery_key),
        )
        with self.assertRaises(ValueError):
            recover_vault_key_from_bundle(
                bundle,
                CryptoService.generate_recovery_key(),
            )

    def test_recovery_bundle_user_binding_is_authenticated(self):
        material = self.vault.enable_recovery("alice", self.key)
        payload = json.loads(material.recovery_bundle)
        payload["user_id"] = "mallory"
        tampered = json.dumps(payload, sort_keys=True).encode("utf-8")

        with self.assertRaisesRegex(ValueError, "authentication"):
            recover_vault_key_from_bundle(tampered, material.recovery_key)

    def test_recovery_rotation_and_disable_change_live_profile_only(self):
        first = self.vault.enable_recovery("alice", self.key)
        second = self.vault.rotate_recovery("alice", self.key)

        self.assertEqual(
            self.key,
            self.vault.unlock_with_recovery("alice", second.recovery_key),
        )
        with self.assertRaisesRegex(ValueError, "Recovery-Key"):
            self.vault.unlock_with_recovery("alice", first.recovery_key)

        # Portable bundles are deliberate snapshots. Full revocation of an
        # exported old bundle needs a vault-key rotation.
        self.assertEqual(
            self.key,
            recover_vault_key_from_bundle(
                first.recovery_bundle,
                first.recovery_key,
            ),
        )

        self.vault.disable_recovery("alice", self.key)
        self.assertFalse(self.vault.recovery_configured("alice"))
        with self.assertRaisesRegex(ValueError, "nicht eingerichtet"):
            self.vault.unlock_with_recovery("alice", second.recovery_key)

    def test_v2_backup_preserves_password_and_recovery_hierarchy(self):
        created = self.vault.put(
            "alice",
            self.key,
            {
                "type": "login",
                "name": "Synthetic",
                "username": "alice@example.test",
                "password": secrets.token_urlsafe(20),
            },
        )
        recovery = self.vault.enable_recovery("alice", self.key)
        backup = self.vault.export_backup("alice")
        payload = json.loads(backup)["payload"]
        self.assertEqual(2, payload["version"])
        self.assertTrue(payload["profile"]["key_protection"])
        self.assertTrue(payload["profile"]["recovery_record"])

        with tempfile.TemporaryDirectory() as restored_temp:
            restored = PasswordVault(Path(restored_temp))
            result = restored.import_backup(backup)
            self.assertEqual({"profiles": 1, "entries": 1}, result)
            restored_key = restored.unlock("alice", self.password)
            self.assertEqual(self.key, restored_key)
            self.assertEqual(
                self.key,
                restored.unlock_with_recovery("alice", recovery.recovery_key),
            )
            rows = restored.entries("alice", restored_key)
            self.assertEqual(created["entry_id"], rows[0]["entry_id"])
            self.assertEqual("Synthetic", rows[0]["data"]["name"])

    def test_version_one_legacy_backup_remains_importable(self):
        legacy_password = "abcdefghij"
        self._install_legacy_password_wrap(legacy_password)
        backup = json.loads(self.vault.export_backup("alice"))
        payload = backup["payload"]
        payload["version"] = 1
        payload["profile"].pop("key_protection", None)
        payload["profile"].pop("recovery_record", None)
        backup["sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
        encoded = json.dumps(backup, ensure_ascii=False, sort_keys=True).encode("utf-8")

        with tempfile.TemporaryDirectory() as restored_temp:
            restored = PasswordVault(Path(restored_temp))
            restored.import_backup(encoded)
            self.assertEqual(
                self.key,
                restored.unlock("alice", legacy_password),
            )
            with restored._db() as db:
                row = db.execute(
                    "SELECT key_protection FROM vault_profile WHERE user_id='alice'"
                ).fetchone()
            self.assertTrue(str(row["key_protection"]))


if __name__ == "__main__":
    unittest.main()
