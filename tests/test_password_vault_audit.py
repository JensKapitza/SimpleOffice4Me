import base64
import tempfile
import unittest
from pathlib import Path

from app.password_vault import PasswordVault
from app.v2.contracts import AuditPort, OperationResult


class CapturingAudit(AuditPort):
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)
        return OperationResult.success("audit-1")


class VaultAuditTests(unittest.TestCase):
    def test_vault_events_never_include_secret_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            audit = CapturingAudit()
            vault = PasswordVault(Path(temp), audit_port=audit)
            key = vault.create("alice", "correct horse battery staple")
            written = vault.put(
                "alice",
                key,
                {
                    "type": "login",
                    "name": "Example",
                    "username": "alice@example.test",
                    "password": "super-secret-password",
                    "totp": "JBSWY3DPEHPK3PXP",
                    "notes": "secret note",
                },
            )
            vault.export_backup("alice")
            vault.delete("alice", written["entry_id"])

            serialized = repr(audit.events)
            self.assertNotIn("super-secret-password", serialized)
            self.assertNotIn("JBSWY3DPEHPK3PXP", serialized)
            self.assertNotIn("secret note", serialized)
            operations = [event.operation for event in audit.events]
            self.assertIn("vault_created", operations)
            self.assertIn("vault_credential_written", operations)
            self.assertIn("vault_backup_exported", operations)
            self.assertIn("vault_credential_deleted", operations)

    def test_recovery_lifecycle_is_audited_without_recovery_key_material(self):
        with tempfile.TemporaryDirectory() as temp:
            audit = CapturingAudit()
            vault = PasswordVault(Path(temp), audit_port=audit)
            key = vault.create("alice", "correct horse battery staple")

            first = vault.enable_recovery("alice", key)
            vault.unlock_with_recovery("alice", first.recovery_key)
            vault.export_recovery_bundle("alice", key)
            second = vault.rotate_recovery("alice", key)
            vault.unlock_with_recovery("alice", second.recovery_key)
            vault.disable_recovery("alice", key)

            serialized = repr(audit.events)
            for recovery_key in (first.recovery_key, second.recovery_key):
                self.assertNotIn(repr(recovery_key), serialized)
                encoded = base64.urlsafe_b64encode(recovery_key).decode("ascii")
                self.assertNotIn(encoded, serialized)
            operations = [event.operation for event in audit.events]
            self.assertIn("vault_recovery_enabled", operations)
            self.assertIn("vault_recovered", operations)
            self.assertIn("vault_recovery_bundle_exported", operations)
            self.assertIn("vault_recovery_rotated", operations)
            self.assertIn("vault_recovery_disabled", operations)

    def test_unlock_and_master_password_change_are_audited(self):
        with tempfile.TemporaryDirectory() as temp:
            audit = CapturingAudit()
            vault = PasswordVault(Path(temp), audit_port=audit)
            vault.create("alice", "correct horse battery staple")
            vault.unlock("alice", "correct horse battery staple")
            vault.change_master_password(
                "alice",
                "correct horse battery staple",
                "another correct horse battery staple",
            )
            operations = [event.operation for event in audit.events]
            self.assertIn("vault_unlocked", operations)
            self.assertIn("vault_master_password_changed", operations)


if __name__ == "__main__":
    unittest.main()
