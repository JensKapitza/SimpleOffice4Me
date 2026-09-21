import tempfile
import unittest
from pathlib import Path

from app.v2.adapters.audit import RevisionHistoryAuditAdapter
from app.v2.contracts import AuditEvent, AuditPort


class V2AuditAdapterTests(unittest.TestCase):
    def test_adapter_implements_port_and_persists_without_secret_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = RevisionHistoryAuditAdapter(root)
            self.assertIsInstance(adapter, AuditPort)
            result = adapter.append(
                AuditEvent(
                    actor="tester",
                    operation="credential_reference_updated",
                    object_id="object-1",
                    occurred_at="2026-09-21T12:00:00+00:00",
                    source="unit-test",
                    correlation_id="request-1",
                    changes={"password": "must-not-survive", "label": "safe"},
                )
            )
            self.assertTrue(result.ok)
            combined = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in (root / ".simpleoffice-history").rglob("*.json")
            )
            self.assertNotIn("must-not-survive", combined)
            self.assertIn("[REDACTED]", combined)

    def test_invalid_event_is_reported_without_exception_leak(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = RevisionHistoryAuditAdapter(Path(temp))
            result = adapter.append(
                AuditEvent(
                    actor="tester",
                    operation="x" * 301,
                    object_id="object-1",
                    occurred_at="2026-09-21T12:00:00+00:00",
                )
            )
            self.assertFalse(result.ok)
            self.assertEqual("storage_unavailable", result.error.code.value)
            self.assertNotIn("301", result.error.message)


if __name__ == "__main__":
    unittest.main()
