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

    def test_secret_metadata_identifiers_remain_visible_while_secret_material_is_redacted(self):
        with tempfile.TemporaryDirectory() as temp:
            history = RevisionHistory(Path(temp))
            history.record(
                "credential_rotated",
                "jens",
                "webdav",
                "device-1",
                {
                    "credential_id": "cred-123",
                    "token_id": "token-456",
                    "credential_fingerprint": "SHA256:abcdef",
                    "credential_label": "Laptop",
                    "credential_type": "app-password",
                    "credential_scope": "documents",
                    "credential": "plaintext-secret",
                    "access_token": "top-secret",
                    "nested": {"password": "also-secret"},
                },
            )
            snapshot = json.loads(
                (history.root / "snapshots" / "webdav" / "device-1.json").read_text(encoding="utf-8")
            )
            self.assertEqual("cred-123", snapshot["credential_id"])
            self.assertEqual("token-456", snapshot["token_id"])
            self.assertEqual("SHA256:abcdef", snapshot["credential_fingerprint"])
            self.assertEqual("Laptop", snapshot["credential_label"])
            self.assertEqual("app-password", snapshot["credential_type"])
            self.assertEqual("documents", snapshot["credential_scope"])
            self.assertEqual("[REDACTED]", snapshot["credential"])
            self.assertEqual("[REDACTED]", snapshot["access_token"])
            self.assertEqual("[REDACTED]", snapshot["nested"]["password"])

    def test_change_count_keeps_real_total_when_display_paths_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            history = RevisionHistory(Path(temp))
            history.record("settings_updated", "jens", "settings", "bulk", {})
            history.record(
                "settings_updated",
                "jens",
                "settings",
                "bulk",
                {f"field_{index}": index for index in range(100)},
            )
            events = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((history.root / "events").glob("*.json"))]
            event = events[-1]
            self.assertEqual(100, event["change_count"])
            self.assertEqual(80, len(event["changed_fields"]))
            self.assertTrue(event["changes_truncated"])

    def test_audit_identity_fields_strip_control_characters(self):
        with tempfile.TemporaryDirectory() as temp:
            history = RevisionHistory(Path(temp))
            history.record(
                "settings_updated\nforged",
                "jens\r\nattacker",
                "settings\tadmin",
                "key\x00value",
                {"request_id": "req-1\nforged"},
            )
            event_path = next((history.root / "events").glob("*.json"))
            event = json.loads(event_path.read_text(encoding="utf-8"))
            self.assertEqual("jens attacker", event["actor"])
            self.assertEqual("settings_updated forged", event["action"])
            self.assertEqual("settings admin", event["category"])
            self.assertEqual("key value", event["key"])
            self.assertEqual("req-1 forged", event["correlation_id"])
            self.assertNotIn("\n", event["actor"])

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

    def test_invalid_event_json_is_reported_instead_of_being_treated_as_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            history = RevisionHistory(Path(temp))
            history.record("document_created", "jens", "documents", "doc-1", {"document_id": "doc-1"})
            event_path = next((history.root / "events").glob("*.json"))
            event_path.write_text("{broken", encoding="utf-8")
            result = history.verify_event_chain()
            self.assertFalse(result["valid"])
            self.assertTrue(any("invalid JSON" in message for message in result["errors"]))

    def test_event_chain_head_sequence_and_hash_are_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            history = RevisionHistory(Path(temp))
            history.record("document_created", "jens", "documents", "doc-1", {"document_id": "doc-1"})
            chain_path = history.root / "event-chain.json"
            chain = json.loads(chain_path.read_text(encoding="utf-8"))
            chain["sequence"] = 99
            chain_path.write_text(json.dumps(chain), encoding="utf-8")
            result = history.verify_event_chain()
            self.assertFalse(result["valid"])
            self.assertTrue(any("final sequence mismatch" in message for message in result["errors"]))

    def test_corrupt_chain_state_blocks_new_audit_write(self):
        with tempfile.TemporaryDirectory() as temp:
            history = RevisionHistory(Path(temp))
            history.record("document_created", "jens", "documents", "doc-1", {"document_id": "doc-1"})
            (history.root / "event-chain.json").write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                history.record("document_updated", "jens", "documents", "doc-1", {"document_id": "doc-1", "path": "b.txt"})

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