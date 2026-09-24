import json
import os
import tempfile
import unittest
from pathlib import Path

from app.v2.contracts import ErrorCode, OperationResult
from app.v2.master_keys import (
    MasterKeyProfileStore,
    decode_recovery_key,
    encode_recovery_key,
    recover_master_key_from_bundle,
)


class CapturingAudit:
    def __init__(self, fail_operation=""):
        self.events = []
        self.fail_operation = fail_operation

    def append(self, event):
        self.events.append(event)
        if event.operation == self.fail_operation:
            return OperationResult.failure(ErrorCode.STORAGE_UNAVAILABLE, "synthetic audit failure")
        return OperationResult.success(f"audit-{len(self.events)}")


class MasterKeyProfileStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audit = CapturingAudit()
        self.store = MasterKeyProfileStore(self.root, "synthetic-admin", audit_port=self.audit)
        self.profile = "synthetic-user"
        self.password = "correct synthetic password"

    def tearDown(self):
        self.temp.cleanup()

    def test_create_persists_only_protected_material(self):
        material = self.store.create(self.profile, self.password)
        master = self.store.unlock_with_password(self.profile, self.password)
        path = self.store._path(self.profile)
        raw = path.read_bytes()

        self.assertEqual(32, len(master))
        self.assertEqual(32, len(material.recovery_key))
        self.assertNotIn(self.password.encode("utf-8"), raw)
        self.assertNotIn(master, raw)
        self.assertNotIn(material.recovery_key, raw)
        self.assertNotIn(self.profile.encode("utf-8"), raw)
        self.assertTrue(self.store.configured(self.profile))
        if os.name == "posix":
            self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_password_unlock_and_change_keep_same_master_key(self):
        self.store.create(self.profile, self.password)
        before = self.store.unlock_with_password(self.profile, self.password)

        self.store.change_password(self.profile, self.password, "new synthetic password")
        after = self.store.unlock_with_password(self.profile, "new synthetic password")

        self.assertEqual(before, after)
        with self.assertRaisesRegex(ValueError, "invalid or profile was modified"):
            self.store.unlock_with_password(self.profile, self.password)

    def test_recovery_bundle_is_portable_without_live_store(self):
        material = self.store.create(self.profile, self.password)
        expected = self.store.unlock_with_password(self.profile, self.password)
        recovered = recover_master_key_from_bundle(material.recovery_bundle, material.recovery_key)

        self.assertEqual(expected, recovered)
        bundle = json.loads(material.recovery_bundle)
        self.assertNotIn("password", bundle)
        self.assertNotIn(self.profile, material.recovery_bundle.decode("utf-8"))

    def test_recovery_rotation_invalidates_old_material(self):
        original = self.store.create(self.profile, self.password)
        master = self.store.unlock_with_password(self.profile, self.password)
        rotated = self.store.rotate_recovery_key(self.profile, self.password)

        self.assertEqual(master, self.store.unlock_with_recovery_key(self.profile, rotated.recovery_key))
        self.assertEqual(master, recover_master_key_from_bundle(rotated.recovery_bundle, rotated.recovery_key))
        with self.assertRaises(ValueError):
            self.store.unlock_with_recovery_key(self.profile, original.recovery_key)
        with self.assertRaises(ValueError):
            recover_master_key_from_bundle(rotated.recovery_bundle, original.recovery_key)

    def test_master_key_replacement_rotates_password_and_recovery_wrapping(self):
        original = self.store.create(self.profile, self.password)
        old_master = self.store.unlock_with_password(self.profile, self.password)
        new_master = self.store.crypto.generate_master_key()
        new_recovery = self.store.crypto.generate_recovery_key()

        material = self.store.replace_master_key(
            self.profile,
            self.password,
            new_master,
            recovery_key=new_recovery,
            expected_current_master_key=old_master,
        )

        self.assertEqual(new_master, self.store.unlock_with_password(self.profile, self.password))
        self.assertEqual(new_master, self.store.unlock_with_recovery_key(self.profile, new_recovery))
        self.assertEqual(new_recovery, material.recovery_key)
        self.assertEqual(
            new_master,
            recover_master_key_from_bundle(material.recovery_bundle, material.recovery_key),
        )
        with self.assertRaises(ValueError):
            self.store.unlock_with_recovery_key(self.profile, original.recovery_key)
        self.assertIn(
            "master_key_rotated",
            [event.operation for event in self.audit.events],
        )

    def test_failed_master_key_replacement_restores_previous_profile(self):
        original = self.store.create(self.profile, self.password)
        old_master = self.store.unlock_with_password(self.profile, self.password)
        self.audit.fail_operation = "master_key_rotated"

        with self.assertRaisesRegex(RuntimeError, "audited"):
            self.store.replace_master_key(
                self.profile,
                self.password,
                self.store.crypto.generate_master_key(),
                recovery_key=self.store.crypto.generate_recovery_key(),
                expected_current_master_key=old_master,
            )

        self.audit.fail_operation = ""
        self.assertEqual(old_master, self.store.unlock_with_password(self.profile, self.password))
        self.assertEqual(
            old_master,
            self.store.unlock_with_recovery_key(self.profile, original.recovery_key),
        )

    def test_profile_swap_is_detected_by_authenticated_binding(self):
        first = self.store.create("profile-one", "first synthetic password")
        second = self.store.create("profile-two", "second synthetic password")
        first_path = self.store._path("profile-one")
        second_path = self.store._path("profile-two")
        first_path.write_bytes(second_path.read_bytes())

        with self.assertRaises(ValueError):
            self.store.unlock_with_recovery_key("profile-one", first.recovery_key)

        self.assertEqual(
            self.store.unlock_with_password("profile-two", "second synthetic password"),
            recover_master_key_from_bundle(second.recovery_bundle, second.recovery_key),
        )

    def test_wrong_password_audit_contains_no_secret_values(self):
        material = self.store.create(self.profile, self.password)
        with self.assertRaises(ValueError):
            self.store.unlock_with_password(self.profile, "definitely wrong password")

        serialized = json.dumps([
            {
                "operation": event.operation,
                "object_id": event.object_id,
                "changes": dict(event.changes),
            }
            for event in self.audit.events
        ])
        self.assertNotIn(self.password, serialized)
        self.assertNotIn("definitely wrong password", serialized)
        self.assertNotIn(encode_recovery_key(material.recovery_key), serialized)
        self.assertIn("master_key_unlock_failed", serialized)

    def test_audit_failure_rolls_back_initial_profile(self):
        audit = CapturingAudit(fail_operation="master_key_profile_created")
        store = MasterKeyProfileStore(self.root, "synthetic-admin", audit_port=audit)

        with self.assertRaisesRegex(RuntimeError, "audited"):
            store.create(self.profile, self.password)
        self.assertFalse(store.configured(self.profile))

    def test_failed_password_change_audit_restores_previous_record(self):
        self.store.create(self.profile, self.password)
        expected = self.store.unlock_with_password(self.profile, self.password)
        self.audit.fail_operation = "master_key_password_changed"

        with self.assertRaisesRegex(RuntimeError, "audited"):
            self.store.change_password(self.profile, self.password, "replacement synthetic password")

        self.audit.fail_operation = ""
        self.assertEqual(expected, self.store.unlock_with_password(self.profile, self.password))
        with self.assertRaises(ValueError):
            self.store.unlock_with_password(self.profile, "replacement synthetic password")

    def test_failed_recovery_rotation_audit_preserves_old_recovery_key(self):
        original = self.store.create(self.profile, self.password)
        expected = self.store.unlock_with_password(self.profile, self.password)
        self.audit.fail_operation = "master_key_recovery_rotated"

        with self.assertRaisesRegex(RuntimeError, "audited"):
            self.store.rotate_recovery_key(self.profile, self.password)

        self.audit.fail_operation = ""
        self.assertEqual(expected, self.store.unlock_with_recovery_key(self.profile, original.recovery_key))

    def test_stale_profile_lock_blocks_mutation_without_breaking_unlock(self):
        self.store.create(self.profile, self.password)
        expected = self.store.unlock_with_password(self.profile, self.password)
        lock = self.store.base / f".{self.store.status(self.profile)['profile_hash']}.lock"
        lock.mkdir(mode=0o700)

        with self.assertRaisesRegex(RuntimeError, "busy or has a stale lock"):
            self.store.change_password(self.profile, self.password, "replacement synthetic password")

        self.assertEqual(expected, self.store.unlock_with_password(self.profile, self.password))

    @unittest.skipUnless(os.name == "posix", "symlink hardening test requires POSIX semantics")
    def test_symlinked_master_key_directory_is_rejected_for_reads(self):
        other = self.root / "other-master-keys"
        other.mkdir()
        self.store._ensure_base()
        base = self.root / ".simpleoffice-v2" / "master-keys"
        for child in base.iterdir():
            if child.is_file():
                child.unlink()
        base.rmdir()
        base.symlink_to(other, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "must be a real directory"):
            self.store.configured(self.profile)

    def test_recovery_key_text_encoding_is_strict_and_round_trips(self):
        material = self.store.create(self.profile, self.password)
        text = encode_recovery_key(material.recovery_key)

        self.assertEqual(material.recovery_key, decode_recovery_key(text))
        with self.assertRaises(ValueError):
            decode_recovery_key(text + "*")
        with self.assertRaises(ValueError):
            decode_recovery_key("short")

    def test_status_exposes_only_non_secret_metadata(self):
        self.store.create(self.profile, self.password)
        status = self.store.status(self.profile)

        self.assertTrue(status["configured"])
        self.assertFalse(status["raw_master_key_persisted"])
        self.assertFalse(status["raw_recovery_key_persisted"])
        self.assertEqual("argon2id-aes256gcm", status["password_method"])
        self.assertEqual("recovery-aes256gcm", status["recovery_method"])


if __name__ == "__main__":
    unittest.main()
