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
