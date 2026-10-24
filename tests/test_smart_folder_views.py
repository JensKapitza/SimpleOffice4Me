"""Black-box coverage for lazy smart folder discovery and activation."""
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid5

from app.document_store import DocumentStore
from app.documents_routes_smart_folders import _spreadsheet_safe_cell
from app.smart_folder_views import SmartFolderViews
from app.todo_store import TodoStore


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

    def test_name_collisions_use_unicode_normalization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            composed = root / "source-a" / "Café"
            decomposed = root / "source-b" / "Cafe\u0301"
            composed.mkdir(parents=True)
            decomposed.mkdir(parents=True)
            (composed / "one.txt").write_text("one", encoding="utf-8")
            (decomposed / "two.txt").write_text("two", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")

            matches = views.view("source-a/Café")["same_name"]

            self.assertEqual(["source-b/Cafe\u0301"], [item["path"] for item in matches])

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

    def test_remove_logical_assignment_preserves_original_and_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "scans" / "beleg.txt"
            target = root / "clients" / "vertrag.txt"
            source.parent.mkdir()
            target.parent.mkdir()
            source.write_text("Beleg", encoding="utf-8")
            target.write_text("Vertrag", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            document_id = store.get_document(source)["document_id"]
            views.assign(document_id, "clients")
            self.assertEqual(2, views.view("clients")["count"])
            self.assertTrue(views.unassign(document_id, "clients"))
            self.assertFalse(views.unassign(document_id, "clients"))
            self.assertEqual([], views.assigned_to(document_id))
            self.assertEqual(1, views.view("clients")["count"])
            self.assertTrue(source.exists())
            self.assertEqual("scans/beleg.txt", store.get_document(document_id)["last_path"])

    def test_bulk_assignment_validates_all_members_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("scans", "clients"):
                (root / folder).mkdir()
            for name in ("a.txt", "b.txt"):
                (root / "scans" / name).write_text(name, encoding="utf-8")
            (root / "clients" / "existing.txt").write_text("x", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            ids = [doc["document_id"] for doc in views.view("scans")["members"]]
            with self.assertRaises(ValueError):
                views.assign_many([ids[0], "nonexistent-id"], "scans", "clients")
            self.assertEqual([], views.assigned_to(ids[0]))
            self.assertEqual(2, views.assign_many(ids, "scans", "clients"))
            self.assertEqual(3, views.view("clients")["count"])
            self.assertEqual(2, views.assign_many(ids, "scans", "clients"))
            self.assertEqual(3, views.view("clients")["count"])
            self.assertTrue((root / "scans" / "a.txt").exists())

    def test_bulk_unassignment_is_atomic_and_does_not_remove_physical_members(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("scans", "clients"):
                (root / folder).mkdir()
            for name in ("a.txt", "b.txt"):
                (root / "scans" / name).write_text(name, encoding="utf-8")
            (root / "clients" / "existing.txt").write_text("x", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            ids = [doc["document_id"] for doc in views.view("scans")["members"]]
            views.assign_many(ids, "scans", "clients")
            with self.assertRaises(ValueError):
                views.unassign_many([ids[0], "missing-id"], "clients")
            self.assertEqual(3, views.view("clients")["count"])
            self.assertEqual(2, len(views.view("clients")["explicit_members"]))
            self.assertEqual(2, views.unassign_many(ids, "clients"))
            self.assertEqual(0, len(views.view("clients")["explicit_members"]))
            physical_id = store.get_document(root / "clients" / "existing.txt")["document_id"]
            with self.assertRaisesRegex(ValueError, "ausdrücklich"):
                views.unassign_many([physical_id], "clients")
            self.assertEqual(1, views.view("clients")["count"])
            self.assertEqual(2, views.view("scans")["count"])
            self.assertTrue((root / "scans" / "a.txt").exists())


        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "scans" / "camera.jpg"
            target = root / "imports" / "batch" / "Telegram"
            source.parent.mkdir()
            target.mkdir(parents=True)
            source.write_bytes(b"scan")
            (target / "existing.jpg").write_bytes(b"existing")
            store = DocumentStore(root)
            store.scan()
            document_id = store.get_document(source)["document_id"]
            views = SmartFolderViews(store, "admin")

            views.assign(document_id, "imports/batch/Telegram")

            view = views.view("imports/batch/Telegram")
            self.assertEqual(2, view["count"])
            self.assertFalse(view["active"])
            self.assertNotIn("imports/batch/Telegram", views._load()["folders"])
            self.assertTrue(source.exists())

    def test_import_origin_view_survives_a_physical_move(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            import_id = "a" * 32
            source_path = "selected-root/Telegram"
            original = root / "imports" / import_id / source_path / "photo.jpg"
            original.parent.mkdir(parents=True)
            original.write_bytes(b"photo")
            store = DocumentStore(root)
            store.scan()
            document = store.get_document(original)
            folder_id = uuid5(UUID(hex=import_id), source_path).hex
            store.update_metadata(
                document["document_id"],
                attributes={
                    "folder_import_origin": {
                        "import_id": import_id,
                        "folder_id": folder_id,
                        "source_path": source_path,
                        "source_name": "Telegram",
                    },
                },
                author="admin",
            )

            store.move_document(document["document_id"], "archive", "admin")
            views = SmartFolderViews(store, "admin")
            source_view = views.view(f"imports/{import_id}/{source_path}")

            self.assertEqual(1, source_view["count"])
            self.assertEqual([folder_id], source_view["source_folder_ids"])
            self.assertEqual([source_path], source_view["source_folder_paths"])

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
            result = views.merge_assignments(
                "a/Telegram", "b/Telegram", "admin",
                expected_fingerprint=preview["fingerprint"],
            )
            self.assertEqual(1, result["documents"])
            self.assertEqual(2, views.view("b/Telegram")["count"])
            with self.assertRaisesRegex(ValueError, "Ordner nicht gefunden"):
                views.view("a/Telegram")
            metadata = views._load()["folders"]["b/Telegram"]
            self.assertEqual("invoice", metadata["rules"]["Beleg"])
            self.assertEqual("Notiz aus Quelle", metadata["merged_notes"][0]["text"])
            self.assertTrue(metadata["merged_notes"][0]["at"])
            self.assertTrue((root / "a/Telegram/record.json").exists())
            event = views._load()["merge_events"][0]
            self.assertEqual("smart_folder_merged", event["action"])
            self.assertEqual("a/Telegram", event["source"])
            self.assertEqual("b/Telegram", event["target"])
            self.assertEqual("admin", event["actor"])
            self.assertEqual(1, event["document_count"])
            self.assertEqual(64, len(event["document_ids_sha256"]))
            self.assertEqual(["Beleg"], event["rules"])
            self.assertTrue(event["notes_transferred"])

    def test_logical_merge_keeps_folder_tasks_linked_to_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("a/Telegram", "b/Telegram"):
                path = root / folder
                path.mkdir(parents=True)
                (path / "record.txt").write_text("record", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            views.activate("a/Telegram", "admin")
            views.activate("b/Telegram", "admin")
            source_id = views.folder_task_id("a/Telegram", "admin")
            target_id = views.folder_task_id("b/Telegram", "admin")
            task = TodoStore(root).add(
                "Quellordner prüfen", "admin", smart_folder_id=source_id,
            )

            preview = views.merge_preview("a/Telegram", "b/Telegram")
            views.merge_assignments(
                "a/Telegram", "b/Telegram", "admin",
                expected_fingerprint=preview["fingerprint"],
            )

            self.assertEqual(
                [target_id, source_id],
                views.task_folder_ids("b/Telegram", "admin"),
            )
            self.assertEqual(source_id, TodoStore(root).items("admin")[0]["smart_folder_id"])
            self.assertEqual(task["id"], TodoStore(root).items("admin")[0]["id"])

    def test_logical_merge_requires_a_normalized_name_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("source/Telegram", "target/Invoices"):
                path = root / folder
                path.mkdir(parents=True)
                (path / "record.txt").write_text("record", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")

            with self.assertRaisesRegex(ValueError, "gleichnamige"):
                views.merge_preview("source/Telegram", "target/Invoices")

    def test_logical_merge_rejects_stale_document_impact_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for folder in ("a/Telegram", "b/Telegram"):
                path = root / folder
                path.mkdir(parents=True)
                (path / "record.txt").write_text("record", encoding="utf-8")
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            views.activate("a/Telegram", "admin")
            views.activate("b/Telegram", "admin")
            preview = views.merge_preview("a/Telegram", "b/Telegram")
            (root / "a/Telegram/new.txt").write_text("new", encoding="utf-8")
            store.scan()

            with self.assertRaisesRegex(ValueError, "geändert"):
                views.merge_assignments(
                    "a/Telegram", "b/Telegram", "admin",
                    expected_fingerprint=preview["fingerprint"],
                )

            self.assertEqual(2, views.view("a/Telegram")["count"])
            self.assertEqual(1, views.view("b/Telegram")["count"])

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
            preview = views.merge_preview("a/Telegram", "b/Telegram")
            with self.assertRaisesRegex(ValueError, "Widersprüchliche"):
                views.merge_assignments(
                    "a/Telegram", "b/Telegram", "admin",
                    expected_fingerprint=preview["fingerprint"],
                )
            self.assertEqual(1, views.view("a/Telegram")["count"])

    def test_preview_selection_is_limited_to_supported_sizes_and_folder_members(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "imports" / "Telegram"
            folder.mkdir(parents=True)
            for index in range(6):
                (folder / f"photo-{index}.jpg").write_bytes(f"photo-{index}".encode())
            store = DocumentStore(root)
            store.scan()
            views = SmartFolderViews(store, "admin")
            views.activate("imports/Telegram", "admin")
            document_ids = [item["document_id"] for item in views.view("imports/Telegram")["members"]]

            views.set_preview_documents("imports/Telegram", document_ids[:4], "admin")

            self.assertEqual(document_ids[:4], views.view("imports/Telegram")["preview_document_ids"])
            self.assertEqual(4, len(views.view("imports/Telegram")["preview_members"]))
            with self.assertRaisesRegex(ValueError, "1, 4 oder 6"):
                views.set_preview_documents("imports/Telegram", document_ids[:2], "admin")
            with self.assertRaisesRegex(ValueError, "gehören"):
                views.set_preview_documents(
                    "imports/Telegram", [*document_ids[:3], "not-a-member"], "admin",
                )
            views.set_preview_documents("imports/Telegram", [], "admin")
            self.assertEqual([], views.view("imports/Telegram")["preview_document_ids"])

    def test_folder_path_rejects_ambiguous_or_windows_style_inputs(self):
        for path in ("a//b", " a/b", "a/b ", "C:/Windows", "a/../b", "a/./b", "/tmp", "a/" + chr(0) + "b", "a/" + chr(10) + "b", "a/" + chr(127) + "b"):
            with self.subTest(path=repr(path)):
                with self.assertRaises(ValueError):
                    SmartFolderViews.normalize(path)
        self.assertEqual("clients/2026", SmartFolderViews.normalize("clients/2026"))

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
