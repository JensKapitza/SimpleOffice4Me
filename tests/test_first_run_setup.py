import json
import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app.calendar_collections import CalendarCollections
from app.contact_store import ContactStore
from app.db import ensure_auth_database, get_db
from app.webdav import authenticate_password
from app.todo_store import TodoStore
from app.document_store import DocumentStore


class FirstRunSetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.base_url = "https://office.example"
        self.client.post("/auth/register", data={"username": "jens", "password": "browser-passwort"}, base_url=self.base_url)
        self.client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"}, base_url=self.base_url)

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_dashboard_and_setup_explain_services_and_acl(self):
        dashboard = self.client.get("/documents/dashboard", base_url=self.base_url).get_data(as_text=True)
        response = self.client.get("/documents/setup", base_url=self.base_url)
        body = response.get_data(as_text=True)

        self.assertIn("Erststart beginnen", dashboard)
        self.assertEqual(200, response.status_code)
        self.assertIn("https://office.example/webdav/files/jens/", body)
        self.assertIn("https://office.example/caldav/calendars/jens/", body)
        self.assertIn("https://office.example/caldav/calendars/jens/tasks/", body)
        self.assertIn("VTODO", body)
        self.assertIn("https://office.example/carddav/addressbooks/jens/contacts/", body)
        self.assertIn("Lesen", body)
        self.assertIn("Schreiben", body)
        self.assertIn("Verwalten", body)

    def test_dark_theme_is_saved_per_user_and_rendered_on_next_request(self):
        response = self.client.post(
            "/documents/settings",
            data={
                "display_name": "Jens", "theme": "dark", "default_language": "de",
                "timezone": "Europe/Berlin", "default_state": "new",
                "default_duration_minutes": "60", "default_expiry_days": "7",
            },
            base_url=self.base_url,
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            self.assertEqual("dark", get_db().execute("SELECT theme FROM user WHERE username='jens'").fetchone()[0])
        page = self.client.get("/documents/settings", base_url=self.base_url).get_data(as_text=True)
        self.assertIn('data-theme-preference="dark"', page)
        self.assertIn('value="dark" selected', page)

    def test_dashboard_task_details_are_saved_in_shared_task_store(self):
        created = self.client.post(
            "/documents/todo",
            data={"title": "Kunde anrufen", "description": "Rückfrage", "due": "2026-09-03", "priority": "1", "categories": "CRM,Kunde"},
            base_url=self.base_url,
        )
        self.assertEqual(302, created.status_code)
        task = TodoStore(self.root).items("jens")[0]
        self.assertEqual("Rückfrage", task["description"])
        self.assertEqual(["CRM", "Kunde"], task["categories"])
        dashboard = self.client.get("/documents/dashboard", base_url=self.base_url).get_data(as_text=True)
        self.assertIn("Kunde anrufen", dashboard); self.assertIn("2026-09-03", dashboard)
        self.assertIn("Aufgaben verwalten", dashboard)
        self.assertIn(f'/documents/tasks#task-{task["id"]}', dashboard)
        self.assertNotIn(f'/documents/todo/{task["id"]}" class="row', dashboard)
        updated = self.client.post(f"/documents/todo/{task['id']}", data={"title": "Kunde zurückrufen", "status": "in-process", "percent_complete": "50"}, base_url=self.base_url)
        self.assertEqual(302, updated.status_code)
        task = TodoStore(self.root).items("jens")[0]
        self.assertEqual("Kunde zurückrufen", task["title"]); self.assertEqual(50, task["percent_complete"])

    def test_task_management_page_list_kanban_comments_and_time(self):
        created = self.client.post("/documents/todo", data={"title": "VTODO UI", "return_to": "tasks", "due": "2026-09-03"}, base_url=self.base_url)
        self.assertIn("/documents/tasks", created.headers["Location"])
        task = TodoStore(self.root).items("jens")[0]
        page = self.client.get("/documents/tasks", base_url=self.base_url)
        self.assertEqual(200, page.status_code); self.assertIn("VTODO UI", page.get_data(as_text=True)); self.assertIn("Kanban", page.get_data(as_text=True))
        self.assertEqual(302, self.client.post(f"/documents/tasks/{task['id']}/comments", data={"text": "Kommentar"}, base_url=self.base_url).status_code)
        self.assertEqual(302, self.client.post(f"/documents/tasks/{task['id']}/time", data={"minutes": "15", "note": "Test"}, base_url=self.base_url).status_code)
        stored = TodoStore(self.root).items("jens")[0]
        self.assertEqual("Kommentar", stored["comments"][0]["text"]); self.assertEqual(15, stored["time_entries"][0]["minutes"])

    def test_document_task_has_description_and_reachable_attachments(self):
        document = DocumentStore(self.root).import_upload(io.BytesIO(b"offer"), "Angebot.pdf", "jens")
        email_document = DocumentStore(self.root).import_upload(io.BytesIO(b"mail"), "Anfrage.eml", "jens")
        contact = ContactStore(self.root).upsert(
            {"display_name": "Kunde Beispiel", "email": "kunde@example.test"}, "jens"
        )
        response = self.client.post(
            f"/documents/{document['document_id']}/tasks",
            data={"title": "Angebot prüfen"},
            base_url=self.base_url,
        )
        self.assertEqual(302, response.status_code)
        task = TodoStore(self.root).items("jens")[0]
        self.assertIn("erforderliche Bearbeitung", task["description"])
        self.assertEqual([document["document_id"]], task["document_ids"])
        TodoStore(self.root).update(
            task["id"],
            {
                "contact_id": contact["contact_id"],
                "email_document_id": email_document["document_id"],
                "extra_lines": ['ATTACH;FILENAME="extern.pdf":https://files.example/extern.pdf'],
            },
            "jens",
        )
        body = self.client.get("/documents/tasks", base_url=self.base_url).get_data(as_text=True)
        self.assertIn("Angebot.pdf", body)
        self.assertIn(f"/documents/{document['document_id']}", body)
        self.assertIn("extern.pdf", body)
        self.assertIn("https://files.example/extern.pdf", body)
        self.assertIn("Kunde Beispiel", body)
        self.assertIn(f"/documents/contacts/{contact['contact_id']}", body)
        self.assertIn("Anfrage.eml", body)
        self.assertIn(f"/documents/{email_document['document_id']}", body)

    def test_remote_http_disables_secret_creation(self):
        client = app.test_client()
        client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"}, base_url="http://office.example")
        body = client.get("/documents/setup", base_url="http://office.example").get_data(as_text=True)
        self.assertIn("HTTPS fehlt", body)
        self.assertIn("disabled", body)
        denied = client.post("/documents/setup/access", base_url="http://office.example")
        self.assertEqual(400, denied.status_code)
        self.assertFalse((self.root / ".simpleoffice-meta" / "webdav-credentials.json").exists())

    def test_provisioning_creates_separate_working_secrets_once(self):
        response = self.client.post("/documents/setup/access", base_url=self.base_url)
        body = response.get_data(as_text=True)
        inputs = body.split('readonly value="')[1:4]
        passwords = [item.split('"', 1)[0] for item in inputs]

        self.assertEqual(200, response.status_code)
        self.assertEqual(3, len(passwords))
        self.assertEqual(3, len(set(passwords)))
        with app.test_request_context(base_url="https://office.example"):
            self.assertIsNotNone(authenticate_password("jens", passwords[0]))
        self.assertTrue(CalendarCollections(self.root).authenticate("jens", passwords[1]))
        self.assertTrue(ContactStore(self.root).carddav_authenticate("jens", passwords[2]))

        stored = (self.root / ".simpleoffice-meta" / "webdav-credentials.json").read_text(encoding="utf-8")
        self.assertNotIn(passwords[0], stored)
        later = self.client.get("/documents/setup", base_url=self.base_url).get_data(as_text=True)
        for password in passwords:
            self.assertNotIn(password, later)

    def test_completion_is_per_user_audited_and_export_has_no_passwords(self):
        response = self.client.post("/documents/setup/complete", data={"platform": "linux"}, base_url=self.base_url)
        self.assertEqual(302, response.status_code)
        state = json.loads((self.root / ".simpleoffice-meta" / "setup.json").read_text(encoding="utf-8"))
        self.assertEqual("linux", state["users"]["jens"]["platform"])
        dashboard = self.client.get("/documents/dashboard", base_url=self.base_url).get_data(as_text=True)
        self.assertNotIn("Erststart beginnen", dashboard)

        exported = self.client.get("/documents/setup/export.txt", base_url=self.base_url)
        body = exported.get_data(as_text=True)
        self.assertEqual(200, exported.status_code)
        self.assertIn("attachment; filename=SimpleOffice-Einrichtung.txt", exported.headers["Content-Disposition"])
        self.assertIn("sshfs -p 2222 jens@office.example:/", body)
        self.assertIn("keine Passwörter", body)


if __name__ == "__main__":
    unittest.main()
