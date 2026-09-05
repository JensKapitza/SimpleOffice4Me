import json
import tempfile
import unittest
from pathlib import Path

from app.revision_history import RevisionHistory


class RevisionHistoryHardeningTest(unittest.TestCase):
    def test_events_include_context_changes_hash_chain_and_redact_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            history = RevisionHistory(root)
            history.record(
                "settings_updated",
                "jens",
                "settings",
                "application-defaults",
                {
                    "updated_at": "2026-09-05T16:00:00+00:00",
                    "interface": {"timezone": "Europe/Berlin"},
                    "api_token": "super-secret-token",
                    "nested": {"password": "niemals-speichern"},
                },
            )
            history.record(
                "settings_updated",
                "jens",
                "settings",
                "application-defaults",
                {
                    "updated_at": "2026-09-05T16:01:00+00:00",
                    "interface": {"timezone": "Europe/Paris"},
                    "api_token": "anderes-geheimnis",
                    "nested": {"password": "auch-nicht"},
                },
            )

            snapshot = json.loads(
                (history.root / "snapshots" / "settings" / "application-defaults.json").read_text(encoding="utf-8")
            )
            self.assertEqual("[REDACTED]", snapshot["api_token"])
            self.assertEqual("[REDACTED]", snapshot["nested"]["password"])
            self.assertNotIn("anderes-geheimnis", json.dumps(snapshot))

            events = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((history.root / "events").glob("*.json"))]
            self.assertEqual(2, len(events))
            self.assertEqual(2, events[-1]["schema_version"])
            self.assertEqual("Einstellungen geändert", events[-1]["action_label"])
            self.assertEqual("success", events[-1]["outcome"])
            self.assertEqual("info", events[-1]["severity"])
            self.assertIn("interface.timezone", events[-1]["changed_fields"])
            self.assertEqual(events[0]["event_hash"], events[1]["previous_event_hash"])
            self.assertTrue(history.verify_event_chain()["valid"])

    def test_tampered_event_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            history = RevisionHistory(root)
            history.record("document_created", "jens", "documents", "doc-1", {"document_id": "doc-1", "path": "a.txt"})
            event_path = next((history.root / "events").glob("*.json"))
            event = json.loads(event_path.read_text(encoding="utf-8"))
            event["actor"] = "manipuliert"
            event_path.write_text(json.dumps(event), encoding="utf-8")

            result = history.verify_event_chain()
            self.assertFalse(result["valid"])
            self.assertTrue(any("event hash mismatch" in message for message in result["errors"]))

    def test_error_actions_are_classified_for_logbook(self):
        with tempfile.TemporaryDirectory() as temp:
            history = RevisionHistory(Path(temp))
            history.record(
                "malware_scan_failed",
                "scanner",
                "documents",
                "doc-2",
                {"document_id": "doc-2", "error": "scanner unavailable", "outcome": "failed"},
            )
            event_path = next((history.root / "events").glob("*.json"))
            event = json.loads(event_path.read_text(encoding="utf-8"))
            self.assertEqual("error", event["outcome"])
            self.assertEqual("error", event["severity"])
            self.assertEqual("scanner unavailable", event["details"]["error"])


if __name__ == "__main__":
    unittest.main()
