import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.document_store import POLICY_FILE, DocumentStore
from app.retention import add_years, parse_deadline


PAST = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


class RetentionTest(unittest.TestCase):
    def test_deadline_parser_uses_end_of_day_and_handles_leap_years(self):
        self.assertEqual(23, parse_deadline("2026-08-03").hour)
        self.assertEqual("2025-02-28", add_years(parse_deadline("2024-02-29"), 1).date().isoformat())

    def test_folder_and_tag_deadlines_are_inherited_and_explained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            child = root / "Steuer" / "2026"
            child.mkdir(parents=True)
            source = child / "rechnung.txt"
            source.write_text("Rechnung", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            document = store.get_document(source)

            policy_path = root / POLICY_FILE
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["retention"] = {
                "rules": [
                    {
                        "id": "tag-2026",
                        "kind": "retention",
                        "tag": "rechnung",
                        "years": 8,
                        "label": "Steuerunterlagen acht Jahre",
                    }
                ]
            }
            policy_path.write_text(json.dumps(policy), encoding="utf-8")

            status = store.retention_status(document["document_id"], now=NOW)

            self.assertEqual("folder", status["retention_findings"][0]["source_type"])
            self.assertEqual("rechnung", status["retention_findings"][0]["tag"])
            self.assertEqual(2034, parse_deadline(status["retention_until"]).year)
            self.assertFalse(status["cleanup_eligible"])

    def test_longest_transitive_deadline_wins_and_missing_deadline_blocks_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("a.txt", "b.txt", "c.txt"):
                (root / name).write_text(name, encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            first, second, third = (store.get_document(f"{name}.txt") for name in ("a", "b", "c"))
            store.add_deadline(first["document_id"], "retention", "2020-01-01", "Alt", "tester")
            store.add_deadline(second["document_id"], "retention", "2035-01-01", "Lang", "tester")
            store.add_link(first["document_id"], second["document_id"], author="tester", propagates_retention=True)
            store.add_link(second["document_id"], third["document_id"], author="tester", propagates_retention=True)

            status = store.retention_status(first["document_id"], now=NOW)

            self.assertEqual(2035, parse_deadline(status["retention_until"]).year)
            self.assertIn(third["document_id"], status["missing_retention_document_ids"])
            self.assertFalse(status["cleanup_eligible"])

    def test_expired_work_deadline_locks_edits_but_allows_another_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "vertrag.txt"
            source.write_text("Vertrag", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            document = store.get_document(source)
            store.add_deadline(document["document_id"], "work", PAST.isoformat(), "Vertrag beendet", "tester")

            with self.assertRaisesRegex(ValueError, "locked"):
                store.add_note(document["document_id"], "Nicht mehr erlaubt", "tester")
            added = store.add_deadline(document["document_id"], "retention", "2036-01-01", "Aufbewahrung", "tester")

            self.assertEqual("retention", added["kind"])

    def test_cleanup_requires_all_deadlines_and_only_moves_after_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "alt.txt"
            source.write_text("unverändert", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            document = store.get_document(source)
            store.add_deadline(document["document_id"], "retention", "2020-01-01", "Abgelaufen", "tester")

            preview = store.cleanup_expired("Aussonderung", "tester", now=NOW)
            self.assertEqual(1, len(preview["candidates"]))
            self.assertTrue(source.is_file())

            applied = store.cleanup_expired("Aussonderung", "tester", apply=True, now=NOW)
            moved = root / applied["moved"][0]["to"]
            self.assertTrue(moved.is_file())
            self.assertEqual("unverändert", moved.read_text(encoding="utf-8"))
            self.assertFalse(source.exists())
            self.assertEqual([], store.cleanup_candidates(now=NOW))
            self.assertTrue(any(event.get("type") == "retention_cleanup_completed" for event in store.logbook()))


if __name__ == "__main__":
    unittest.main()
