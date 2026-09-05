import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database


class PersonnelGuidedClockTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(root / "users.sqlite"), DOCUMENT_ROOT=str(root / "docs"))
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "sicheres-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "sicheres-passwort"})

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _page(self):
        response = self.client.get("/personnel")
        self.assertEqual(200, response.status_code)
        return response.data

    def test_clock_only_shows_valid_next_steps_in_plain_language(self):
        page = self._page()
        self.assertIn(b"Noch nicht eingestempelt", page)
        self.assertIn(b"/personnel/punch/clock_in", page)
        self.assertNotIn(b"/personnel/punch/break_start", page)
        self.assertNotIn(b"Aktueller Status: clock_", page)

        self.assertEqual(302, self.client.post("/personnel/punch/clock_in").status_code)
        page = self._page()
        self.assertIn(b"Arbeitszeit l\xc3\xa4uft", page)
        self.assertIn(b"/personnel/punch/break_start", page)
        self.assertIn(b"/personnel/punch/clock_out", page)
        self.assertNotIn(b"/personnel/punch/clock_in", page)
        self.assertNotIn(b"/personnel/punch/break_end", page)

        self.assertEqual(302, self.client.post("/personnel/punch/break_start").status_code)
        page = self._page()
        self.assertIn(b"Pause l\xc3\xa4uft", page)
        self.assertIn(b"/personnel/punch/break_end", page)
        self.assertNotIn(b"/personnel/punch/clock_out", page)
        self.assertNotIn(b"/personnel/punch/break_start", page)

        self.assertEqual(302, self.client.post("/personnel/punch/break_end").status_code)
        page = self._page()
        self.assertIn(b"Arbeitszeit l\xc3\xa4uft wieder", page)
        self.assertIn(b"/personnel/punch/break_start", page)
        self.assertIn(b"/personnel/punch/clock_out", page)
        self.assertNotIn(b"/personnel/punch/break_end", page)

        self.assertEqual(302, self.client.post("/personnel/punch/clock_out").status_code)
        page = self._page()
        self.assertIn(b"Ausgestempelt", page)
        self.assertIn(b"/personnel/punch/clock_in", page)
        self.assertNotIn(b"/personnel/punch/break_start", page)
        self.assertNotIn(b"/personnel/punch/break_end", page)
        self.assertNotIn(b"/personnel/punch/clock_out", page)

    def test_backend_rejects_an_action_that_is_not_the_next_valid_step(self):
        self._page()
        self.client.post("/personnel/punch/clock_in")
        self.client.post("/personnel/punch/break_start")
        response = self.client.post("/personnel/punch/clock_out", follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Diese Buchung passt nicht zum aktuellen Stempelstatus", response.data)
        with app.app_context():
            actions = [
                row["action"]
                for row in database.get_db().execute("SELECT action FROM employee_punch ORDER BY id").fetchall()
            ]
        self.assertEqual(["clock_in", "break_start"], actions)


if __name__ == "__main__":
    unittest.main()
