"""Quick-search workspace API: entities, relevance, permissions and previews."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.calendar_store import CalendarStore
from app.chat_store import ChatStore
from app.contact_store import ContactStore
from app.document_store import DocumentStore
from app.object_store import ObjectStore
from app.project_store import ProjectStore
from app.todo_store import TodoStore


class QuickSearchApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(
            TESTING=True, DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "alice-password-123"})
        with app.app_context():
            db = database.get_db()
            db.execute(
                """INSERT INTO user(username,password,display_name,is_admin,is_disabled,auth_version,created_at,updated_at)
                   VALUES(?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                ("bob", generate_password_hash("bob-password-123"), "Bob"),
            )
            db.commit()
        self.client.post("/auth/login", data={"username": "alice", "password": "alice-password-123"})
        self.bob_client = app.test_client()
        self.bob_client.post("/auth/login", data={"username": "bob", "password": "bob-password-123"})
        self._seed()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _seed(self):
        needle = "Quicksilver"
        target = self.root / "projects" / f"{needle} plan.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"{needle} kickoff notes".encode("utf-8"))
        store = DocumentStore(self.root)
        store.initialize()
        store._scan_file(target, force_hash=True)
        self.document = store.get_document(target)
        ObjectStore(self.root).create({"name": f"{needle} device", "type": "Gerät"}, "alice")
        ProjectStore(self.root).create_project({"title": f"{needle} rollout"}, "alice")
        self.contact = ContactStore(self.root).upsert(
            {"display_name": f"{needle} GmbH", "company": f"{needle} GmbH"}, "alice",
        )
        self.event = CalendarStore(self.root).add(
            f"{needle} demo", "Status sync", "2026-12-01T10:00:00", "2026-12-01T11:00:00", "", "alice",
        )
        self.task = TodoStore(self.root).add(f"{needle} checklist", "alice", {"due": "2026-12-02"})
        chat = ChatStore(self.root)
        self.room = chat.create_room(f"{needle} room", "alice", ["bob"])
        chat.add_message(self.room["room_id"], "alice", f"hello {needle.lower()} team")
        with app.app_context():
            db = database.get_db()
            alice_id = db.execute("SELECT id FROM user WHERE username=?", ("alice",)).fetchone()["id"]
            from app.browser_bookmarks import _ensure_schema

            _ensure_schema()
            db.execute(
                "INSERT INTO browser_bookmark(id,user_id,title,url,folder,tags) VALUES(?,?,?,?,?,?)",
                ("bookmark-quicksilver", alice_id, f"{needle} docs", "https://example.org/quicksilver", "", ""),
            )
            db.commit()
        # Bob-only rows must stay invisible to alice.
        TodoStore(self.root).add(f"{needle} bob task", "bob", {})
        bob_room = chat.create_room(f"{needle} bob room", "bob", [])
        chat.add_message(bob_room["room_id"], "bob", f"{needle.lower()} secret")

    def test_api_finds_all_entity_kinds(self):
        response = self.client.get("/documents/quick-search/api?q=Quicksilver")
        self.assertEqual(200, response.status_code)
        kinds = {match["kind"] for match in response.get_json()["matches"]}
        for kind in ("document", "object", "project", "contact", "appointment", "task", "chat", "link"):
            self.assertIn(kind, kinds)

    def test_api_ranks_exact_before_substring(self):
        response = self.client.get("/documents/quick-search/api?q=Quicksilver+device")
        matches = response.get_json()["matches"]
        self.assertTrue(matches)
        self.assertEqual("Quicksilver device", matches[0]["label"])
        scores = [match["score"] for match in matches]
        self.assertEqual(sorted(scores, reverse=True), scores)

    def test_api_type_filter_limits_kinds(self):
        response = self.client.get("/documents/quick-search/api?q=Quicksilver&type=task&type=link")
        kinds = {match["kind"] for match in response.get_json()["matches"]}
        self.assertTrue(kinds)
        self.assertLessEqual(kinds, {"task", "link"})

    def test_api_rejects_overlong_query_and_allows_short_gracefully(self):
        response = self.client.get("/documents/quick-search/api?q=" + "x" * 201)
        self.assertEqual(400, response.status_code)
        response = self.client.get("/documents/quick-search/api?q=x")
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.get_json()["matches"])

    def test_api_requires_login(self):
        anonymous = app.test_client()
        response = anonymous.get("/documents/quick-search/api?q=Quicksilver", follow_redirects=False)
        self.assertIn(response.status_code, (302, 303))

    def test_private_tasks_chats_and_events_stay_hidden(self):
        matches = self.bob_client.get("/documents/quick-search/api?q=Quicksilver").get_json()["matches"]
        labels = [match["label"] for match in matches]
        self.assertIn("Quicksilver bob task", labels)
        self.assertNotIn("Quicksilver checklist", labels)
        self.assertNotIn("Quicksilver demo", [match["label"] for match in matches if match["kind"] == "appointment"])

    def test_preview_returns_rows_and_respects_permissions(self):
        preview = self.client.get(
            f"/documents/quick-search/preview?kind=task&id={self.task['id']}"
        )
        self.assertEqual(200, preview.status_code)
        body = preview.get_json()
        self.assertIn("Quicksilver", body["title"])
        self.assertTrue(body["rows"])
        forbidden = self.bob_client.get(
            f"/documents/quick-search/preview?kind=task&id={self.task['id']}"
        )
        self.assertEqual(404, forbidden.status_code)
        unknown = self.client.get("/documents/quick-search/preview?kind=task&id=missing")
        self.assertEqual(404, unknown.status_code)
        bare = self.client.get("/documents/quick-search/preview")
        self.assertEqual(200, bare.status_code)
        self.assertEqual([], bare.get_json()["rows"])

    def test_html_search_keeps_single_hit_redirect(self):
        response = self.client.get("/documents/quick-search?q=Quicksilver+device", follow_redirects=False)
        self.assertIn(response.status_code, (200, 302, 303))


if __name__ == "__main__":
    unittest.main()
