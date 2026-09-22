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
        return OperationResult.success("audit-read")


class VaultReadAuditTests(unittest.TestCase):
    def test_read_audit_contains_count_but_no_secret_values(self):
        with tempfile.TemporaryDirectory() as temp:
            audit = CapturingAudit()
            vault = PasswordVault(Path(temp), audit_port=audit)
            key = vault.create("alice", "correct horse battery staple")
            vault.put(
                "alice",
                key,
                {
                    "type": "login",
                    "name": "Example",
                    "username": "alice@example.test",
                    "password": "never-in-audit",
                    "totp": "SECRET-TOTP",
                },
            )

            entries = vault.entries("alice", key)

            self.assertEqual(1, len(entries))
            read_event = [event for event in audit.events if event.operation == "vault_credentials_read"][-1]
            self.assertEqual(1, read_event.changes["entry_count"])
            serialized = repr(read_event)
            self.assertNotIn("never-in-audit", serialized)
            self.assertNotIn("SECRET-TOTP", serialized)
            self.assertNotIn("alice@example.test", serialized)


if __name__ == "__main__":
    unittest.main()
