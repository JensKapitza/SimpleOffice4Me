"""Black-box coverage for lazy smart folder discovery and activation."""
import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore
from app.documents_routes_smart_folders import _spreadsheet_safe_cell
from app.smart_folder_views import SmartFolderViews


class SmartFolderViewsTest(unittest.TestCase):
    def test_discovery_is_lazy_and_same_names_do_not_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("source-a/Telegram", "source-b/Telegram"):
                (root / folder).mkdir(parents=True)
                (root / folder / "photo.jpg").write_bytes(b"example")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            first = views.view("source-a/Telegram")
            self.assertEqual(1, first["count"])
            self.assertEqual("source-b/Telegram", first["same_name"][0]["path"])
            self.assertFalse(first["active"])
            self.assertFalse(views.path.exists())
            views.activate("source-a/Telegram", "admin", "Meine Notiz")
            self.assertTrue(views.view("source-a/Telegram")["active"])
            self.assertEqual("Meine Notiz", views.view("source-a/Telegram")["notes"])
            self.assertFalse(views.view("source-b/Telegram")["active"])

    def test_logical_assignment_does_not_move_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("scans", "clients"):
                (root / folder).mkdir()
            source = root / "scans" / "beleg.txt"
            source.write_text("Beleg", encoding="utf-8")
            (root / "clients" / "vertrag.txt").write_text("Vertrag", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            views.activate("clients", "admin")
            document_id = store.get_document(source)["document_id"]
            views.assign(document_id, "clients")
            views.assign(document_id, "clients")
            self.assertEqual(["clients"], views.assigned_to(document_id))
            self.assertEqual(2, views.view("clients")["count"])
            self.assertTrue(source.exists())
            self.assertEqual("scans/beleg.txt", store.get_document(document_id)["last_path"])

    def test_safe_structured_extraction_and_csv_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "imports" / "Telegram"
            folder.mkdir(parents=True)
            (folder / "record.json").write_text('{"invoice": "A-12"}', encoding="utf-8")
            (folder / "record.csv").write_text("invoice,amount\nB-22,3\n", encoding="utf-8")
            (folder / "record.xml").write_text("<root><invoice>C-32</invoice></root>", encoding="utf-8")
            (folder / "hostile.xml").write_text(
                '<!DOCTYPE root [<!ENTITY x SYSTEM "file:///etc/passwd">]><root>&x;</root>',
                encoding="utf-8",
            )
            (folder / "oversized.json").write_bytes(b" " * (256 * 1024 + 1))
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            with self.assertRaisesRegex(ValueError, "erst aktivieren"):
                views.save_rule("imports/Telegram", "Beleg", "invoice", "admin")
            views.activate("imports/Telegram", "admin")
            views.save_rule("imports/Telegram", "Beleg", "invoice", "admin")
            rows = views.extract_rows("imports/Telegram")
            self.assertEqual(3, len(rows))
            self.assertEqual({"A-12", "B-22"}, {row["Beleg"] for row in rows if row["path"].endswith((".json", ".csv"))})
            self.assertIn("C-32", {row["Beleg"] for row in rows})
            self.assertEqual("'=1+1", _spreadsheet_safe_cell("=1+1"))
            self.assertEqual("'\t=1+1", _spreadsheet_safe_cell("\t=1+1"))
            self.assertEqual("ordinary", _spreadsheet_safe_cell("ordinary"))

    def test_logical_merge_preserves_notes_and_rules_without_moving_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("a/Telegram", "b/Telegram"):
                (root / folder).mkdir(parents=True)
                (root / folder / "record.json").write_text('{"invoice": "X"}', encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            views.activate("a/Telegram", "admin", "Notiz aus Quelle")
            views.save_rule("a/Telegram", "Beleg", "invoice", "admin")
            preview = views.merge_preview("a/Telegram", "b/Telegram")
            self.assertEqual(["Beleg"], preview["source_rules"])
            self.assertEqual(1, len(preview["source_paths"]))
            result = views.merge_assignments("a/Telegram", "b/Telegram", "admin")
            self.assertEqual(1, result["documents"])
            self.assertEqual(2, views.view("b/Telegram")["count"])
            with self.assertRaisesRegex(ValueError, "Ordner nicht gefunden"):
                views.view("a/Telegram")
            metadata = views._load()["folders"]["b/Telegram"]
            self.assertEqual("invoice", metadata["rules"]["Beleg"])
            self.assertEqual("Notiz aus Quelle", metadata["merged_notes"][0]["text"])
            self.assertTrue(metadata["merged_notes"][0]["at"])
            self.assertTrue((root / "a/Telegram/record.json").exists())

    def test_conflicting_rules_prevent_merge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("a/Telegram", "b/Telegram"):
                (root / folder).mkdir(parents=True)
                (root / folder / "record.json").write_text('{"invoice": "X"}', encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            views.activate("a/Telegram", "admin")
            views.activate("b/Telegram", "admin")
            views.save_rule("a/Telegram", "Beleg", "invoice", "admin")
            views.save_rule("b/Telegram", "Beleg", "other", "admin")
            with self.assertRaisesRegex(ValueError, "Widersprüchliche"):
                views.merge_assignments("a/Telegram", "b/Telegram", "admin")
            self.assertEqual(1, views.view("a/Telegram")["count"])

    def test_invalid_folder_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            views = SmartFolderViews(DocumentStore(Path(temp)), "admin")
            for folder in ("../secret", "/etc", ".simpleoffice-meta"):
                with self.subTest(folder=folder), self.assertRaises(ValueError):
                    views.normalize(folder)

    def test_persistent_notes_and_assignments_are_user_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "scans" / "receipt.txt"
            source.parent.mkdir()
            source.write_text("Receipt", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            document_id = store.get_document(source)["document_id"]
            admin_views = SmartFolderViews(store, "admin")
            other_views = SmartFolderViews(store, "other-user")
            admin_views.activate("scans", "admin", "private note")
            admin_views.assign(document_id, "scans")

            self.assertEqual("private note", admin_views.view("scans")["notes"])
            self.assertEqual("", other_views.view("scans").get("notes", ""))
            self.assertEqual([], other_views.assigned_to(document_id))


if __name__ == "__main__":
    unittest.main()
