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
        return OperationResult.success("audit-failed-unlock")


class VaultFailedUnlockAuditTests(unittest.TestCase):
    def test_failed_unlock_is_audited_without_password_material(self):
        with tempfile.TemporaryDirectory() as temp:
            audit = CapturingAudit()
            vault = PasswordVault(Path(temp), audit_port=audit)
            vault.create("alice", "correct horse battery staple")

            with self.assertRaisesRegex(ValueError, "Master-Passwort"):
                vault.unlock("alice", "definitely-wrong-password")

            event = [e for e in audit.events if e.operation == "vault_unlock_failed"][-1]
            serialized = repr(event)
            self.assertEqual("invalid_credentials_or_integrity", event.changes["reason"])
            self.assertNotIn("definitely-wrong-password", serialized)
            self.assertNotIn("correct horse battery staple", serialized)

    def test_audit_failure_does_not_replace_authentication_failure(self):
        class FailingAudit(AuditPort):
            def append(self, event):
                return OperationResult.failure(
                    code="storage_unavailable",
                    message="audit unavailable",
                )

        with tempfile.TemporaryDirectory() as temp:
            vault = PasswordVault(Path(temp))
            vault.create("alice", "correct horse battery staple")
            vault.audit = FailingAudit()
            with self.assertRaisesRegex(ValueError, "Master-Passwort"):
                vault.unlock("alice", "wrong-password-value")


if __name__ == "__main__":
    unittest.main()
