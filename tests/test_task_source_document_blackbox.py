"""HTTP black-box regression tests for source-document task navigation (#762)."""
import io
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app import app
from app.db import ensure_auth_database
from app.document_store import DocumentStore
from app.todo_store import TodoStore


class SourceDocumentTaskBlackBoxTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "browser-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"})

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def _create_document_task(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"safe fixture"), "original.txt", "jens")
        document_id = document["document_id"]
        response = self.client.post(f"/documents/{document_id}/tasks", data={"title": "Review fixture"})
        self.assertEqual(302, response.status_code)
        return document_id

    def _source_link(self, url, document_id):
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        soup = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        links = [link for link in soup.find_all("a") if link.get_text(strip=True) == "Quelldatei öffnen"]
        self.assertTrue(any(link.get("href", "").endswith(f"/documents/{document_id}") for link in links))
        return links

    def test_document_created_task_links_source_in_both_task_views(self):
        document_id = self._create_document_task()
        for url in ("/documents/tasks", "/tasks/"):
            with self.subTest(url=url):
                links = self._source_link(url, document_id)
                detail = self.client.get(links[0]["href"])
                self.assertEqual(200, detail.status_code)

    def test_legacy_task_without_source_still_renders(self):
        created = self.client.post("/documents/todo", data={"title": "Legacy task"})
        self.assertEqual(302, created.status_code)
        for url in ("/documents/tasks", "/tasks/"):
            page = self.client.get(url)
            self.assertEqual(200, page.status_code)
            self.assertIn("Legacy task", page.get_data(as_text=True))
            self.assertNotIn("Quelldatei öffnen", page.get_data(as_text=True))

    def test_unknown_source_id_does_not_produce_broken_link(self):
        created = self.client.post("/documents/todo", data={
            "title": "Missing document", "source_document_id": "nonexistent-source-id",
        })
        self.assertEqual(302, created.status_code)
        for url in ("/documents/tasks", "/tasks/"):
            response = self.client.get(url)
            self.assertEqual(200, response.status_code)
            body = response.get_data(as_text=True)
            self.assertIn("Quelldatei nicht verfügbar oder Zugriff verweigert", body)
            self.assertNotIn("Quelldatei öffnen", body)

    def test_default_source_task_text_does_not_disclose_the_filename(self):
        filename = "private-customer-contract.pdf"
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"private fixture"), filename, "jens")

        response = self.client.post(f"/documents/{document['document_id']}/tasks")

        self.assertEqual(302, response.status_code)
        task = TodoStore(self.root).items("jens")[0]
        self.assertEqual("Dokument prüfen", task["title"])
        self.assertIn("Quelldokument öffnen", task["description"])
        self.assertNotIn(filename, task["title"] + task["description"])
        for url in ("/documents/tasks", "/tasks/"):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertEqual(200, page.status_code)
                self.assertNotIn(filename, page.get_data(as_text=True))

    def test_document_task_ignores_forged_source_id_and_retains_link_after_update(self):
        document_id = self._create_document_task()
        forged = self.client.post(
            f"/documents/{document_id}/tasks",
            data={"title": "Forged source", "source_document_id": "attacker-document"},
        )
        self.assertEqual(302, forged.status_code)
        task = next(row for row in TodoStore(self.root).items("jens") if row["title"] == "Forged source")
        self.assertEqual(document_id, task["source_document_id"])
        updated = self.client.post(
            f"/documents/todo/{task['id']}",
            data={"title": "Updated task", "status": "in-process", "return_to": "tasks",
                  "source_document_id": "attacker-document"},
        )
        self.assertEqual(302, updated.status_code)
        saved = next(row for row in TodoStore(self.root).items("jens") if row["id"] == task["id"])
        self.assertEqual(document_id, saved["source_document_id"])
        for url in ("/documents/tasks", "/tasks/"):
            with self.subTest(url=url):
                self._source_link(url, document_id)

    def test_source_document_link_survives_move_and_rename(self):
        document_id = self._create_document_task()
        moved = DocumentStore(self.root).move_document(
            document_id, "renamed", "jens", destination_name="renamed-source.txt",
        )

        self.assertTrue(moved["last_path"].endswith("renamed-source.txt"))
        for url in ("/documents/tasks", "/tasks/"):
            with self.subTest(url=url):
                links = self._source_link(url, document_id)
                self.assertEqual(200, self.client.get(links[0]["href"]).status_code)

    def test_soft_deleted_source_has_a_clear_unavailable_state(self):
        document_id = self._create_document_task()
        DocumentStore(self.root).soft_delete_document(document_id, "jens")

        for url in ("/documents/tasks", "/tasks/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(200, response.status_code)
                body = response.get_data(as_text=True)
                self.assertIn("Quelldatei nicht verfügbar oder Zugriff verweigert", body)
                self.assertNotIn(f'href="/documents/{document_id}"', body)


if __name__ == "__main__":
    unittest.main()
