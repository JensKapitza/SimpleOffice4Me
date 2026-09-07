import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database


class DashboardTimeClockTest(unittest.TestCase):
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

    def test_admin_overview_contains_self_time_clock(self):
        response = self.client.get("/documents/dashboard")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Stempeluhr", response.data)
        self.assertIn(b"Kommen", response.data)
        self.assertIn(b"Zeiten verwalten", response.data)
        with app.app_context():
            row = database.get_db().execute(
                "SELECT employee.id FROM employee JOIN user ON user.id=employee.user_id WHERE user.username='jens'"
            ).fetchone()
            self.assertIsNotNone(row)

    def test_dashboard_punch_returns_to_overview_and_changes_state(self):
        self.client.get("/documents/dashboard")
        response = self.client.post("/personnel/time-admin/self/punch/clock_in")
        self.assertEqual(302, response.status_code)
        self.assertTrue(response.headers["Location"].endswith("/documents/dashboard"))
        with app.app_context():
            row = database.get_db().execute(
                """SELECT employee_punch.action
                   FROM employee_punch JOIN employee ON employee.id=employee_punch.employee_id
                   JOIN user ON user.id=employee.user_id
                   WHERE user.username='jens' ORDER BY employee_punch.id DESC LIMIT 1"""
            ).fetchone()
            self.assertEqual("clock_in", row["action"])
        page = self.client.get("/documents/dashboard")
        self.assertIn(b"anwesend", page.data)
        self.assertIn(b"Gehen", page.data)


if __name__ == "__main__":
    unittest.main()
