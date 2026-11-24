"""Round trip the rendered search form and reset link on the task board."""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from app import app
from app.todo_store import TodoStore

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # unittest discovery may put tests/ directly on sys.path.
    from http_api_fixture import PublicHttpTestCase


class TaskFilterNavigationTests(PublicHttpTestCase):
    def _document(self, path, **kwargs):
        response = self.client.get(path, **kwargs)
        self.assertEqual(200, response.status_code)
        return BeautifulSoup(response.text, "html.parser")

    def _active_view(self, page):
        active = page.select('a[href^="/tasks/?"][aria-current="page"]')
        self.assertEqual(1, len(active))
        return parse_qs(urlsplit(active[0]["href"]).query)["view"]

    def test_search_and_reset_do_not_leave_overdue(self):
        store = TodoStore(app.config["DOCUMENT_ROOT"])
        overdue = (date.today() - timedelta(days=2)).isoformat()
        future = (date.today() + timedelta(days=2)).isoformat()
        title = "Rechnung Ä & + prüfen"
        matched = store.add(title, "owner", {"due": overdue, "assigned_to": ["bob"]})
        other = store.add("Anderen Vertrag prüfen", "owner", {"due": overdue, "assigned_to": ["carol"]})
        later = store.add(title, "owner", {"due": future, "assigned_to": ["bob"]})
        page = self._document("/tasks/?view=overdue")
        form = page.select_one("#task-q").find_parent("form")
        self.assertEqual("get", form["method"])
        view = form.select_one('input[type="hidden"][name="view"]')
        self.assertIsNotNone(view)
        self.assertEqual("overdue", view["value"])
        self.assertIsNone(form.select_one("[data-task-filter-reset]"))
        self.assertEqual(["overdue"], self._active_view(page))

        result = self._document(form["action"], query_string={
            view["name"]: view["value"],
            form.select_one('input[name="q"]')["name"]: title,
            form.select_one('select[name="list_id"]')["name"]: matched["list_id"],
            form.select_one('input[name="assigned"]')["name"]: "bob",
        })
        self.assertIsNotNone(result.find(id=f"task-{matched['id']}"))
        self.assertIsNone(result.find(id=f"task-{other['id']}"))
        self.assertIsNone(result.find(id=f"task-{later['id']}"))
        self.assertEqual(["overdue"], self._active_view(result))

        clear = result.select_one("[data-task-filter-reset]")["href"]
        self.assertEqual({"view": ["overdue"]}, parse_qs(urlsplit(clear).query))
        reset = self._document(clear)
        self.assertIsNotNone(reset.find(id=f"task-{matched['id']}"))
        self.assertIsNotNone(reset.find(id=f"task-{other['id']}"))
        self.assertIsNone(reset.find(id=f"task-{later['id']}"))
        self.assertEqual(["overdue"], self._active_view(reset))
        self.assertEqual("", reset.select_one("#task-q")["value"])
        self.assertEqual("", reset.select_one("#task-assigned")["value"])
        self.assertIsNone(reset.select_one("[data-task-filter-reset]"))

    def test_invalid_view_falls_back_to_all_and_clear_remains_canonical(self):
        page = self._document("/tasks/?view=not-a-view&q=practice")
        form = page.select_one("#task-q").find_parent("form")
        self.assertIsNone(form.select_one('input[name="view"]'))
        self.assertEqual(["all"], self._active_view(page))
        self.assertEqual("/tasks/", page.select_one("[data-task-filter-reset]")["href"])
