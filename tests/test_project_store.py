import tempfile
import unittest
from pathlib import Path

from app.project_store import ProjectStore


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_project_task_dependencies_results_and_evidence(self):
        project = self.store.create_project({"title": "Fenster kaputt", "location": "Wohnung 2", "resources": "Vermieter, Fensterbauer"}, "jens")
        photos = self.store.add_task(project["project_id"], {"title": "Fotos erstellen", "status": "completed", "result": "Fotos liegen vor"}, "jens")
        contact = self.store.add_task(project["project_id"], {"title": "Vermieter informieren", "predecessors": [photos["task_id"]], "resources": "E-Mail"}, "jens")
        self.store.attach_document(project["project_id"], "document-photo", "jens", photos["task_id"])
        self.store.add_note(project["project_id"], "Mit Vermieter telefoniert", "jens")
        self.store.add_link(project["project_id"], "https://example.test/termin", "Termin", "jens", contact["task_id"])

        stored = self.store.project(project["project_id"])
        self.assertEqual(stored["tasks"][1]["predecessors"], [photos["task_id"]])
        self.assertEqual(stored["tasks"][0]["document_ids"], ["document-photo"])
        self.assertEqual(stored["tasks"][1]["links"][0]["label"], "Termin")
        self.assertEqual(stored["notes"][0]["created_by"], "jens")

    def test_task_cannot_reference_unknown_or_itself(self):
        project = self.store.create_project({"title": "Test"}, "jens")
        with self.assertRaisesRegex(ValueError, "unknown task dependency"):
            self.store.add_task(project["project_id"], {"title": "Schritt", "predecessors": ["missing"]}, "jens")
        task = self.store.add_task(project["project_id"], {"title": "Schritt"}, "jens")
        with self.assertRaisesRegex(ValueError, "invalid task dependency"):
            self.store.update_task(project["project_id"], task["task_id"], {"title": "Schritt", "predecessors": [task["task_id"]]}, "jens")

    def test_task_status_and_time_entries_are_persisted(self):
        project = self.store.create_project({"title": "Test"}, "jens")
        task = self.store.add_task(project["project_id"], {"title": "Telefonat"}, "jens")
        self.store.update_task(project["project_id"], task["task_id"], {"title": "Telefonat", "status": "in_progress"}, "jens")
        entry = self.store.book_time(project["project_id"], task["task_id"], "2026-07-28", "2", "Vermieter angerufen", "jens")

        stored = self.store.project(project["project_id"])["tasks"][0]
        self.assertEqual("in_progress", stored["status"])
        self.assertEqual(120, entry["minutes"])
        self.assertEqual(120, stored["time_entries"][0]["minutes"])

    def test_exact_hours_and_minutes_do_not_use_decimal_fraction(self):
        project = self.store.create_project({"title": "Test"}, "jens")
        task = self.store.add_task(project["project_id"], {"title": "Montage"}, "jens")

        exact_hour = self.store.book_time(project["project_id"], task["task_id"], "2026-08-24", "1", "", "jens", "0")
        hour_and_two = self.store.book_time(project["project_id"], task["task_id"], "2026-08-24", "1", "", "jens", "2")
        colon = self.store.book_time(project["project_id"], task["task_id"], "2026-08-24", "1:30", "", "jens")

        self.assertEqual(60, exact_hour["minutes"])
        self.assertEqual(62, hour_and_two["minutes"])
        self.assertEqual(90, colon["minutes"])

    def test_time_group_becomes_one_invoice_line_and_keeps_private_evidence(self):
        project = self.store.create_project({"title": "Kundenanlage"}, "jens")
        first = self.store.add_task(project["project_id"], {"title": "A"}, "jens")
        second = self.store.add_task(project["project_id"], {"title": "B"}, "jens")
        entry_a = self.store.book_time(project["project_id"], first["task_id"], "2026-08-24", "1", "A intern", "jens", "0")
        entry_b = self.store.book_time(project["project_id"], second["task_id"], "2026-08-24", "1", "B intern", "jens", "0")

        group = self.store.create_time_group(project["project_id"], {
            "title": "Installation intern", "invoice_text": "Installation",
            "hours": "1", "minutes": "30", "entry_ids": [entry_a["entry_id"], entry_b["entry_id"]],
        }, "jens")

        own = self.store.billing_projection(project["project_id"], "jens")
        other = self.store.billing_projection(project["project_id"], "kollege")
        self.assertEqual([{"source_type": "time_group", "source_id": group["group_id"], "description": "Installation", "minutes": 90}], own["lines"])
        self.assertEqual(["A intern", "B intern"], [item["note"] for item in own["private_groups"][0]["entries"]])
        self.assertEqual([], other["private_groups"])

        with self.assertRaisesRegex(ValueError, "only belong to one"):
            self.store.create_time_group(project["project_id"], {
                "title": "Doppelt", "invoice_text": "Doppelt", "hours": "1", "minutes": "0",
                "entry_ids": [entry_a["entry_id"]],
            }, "jens")
