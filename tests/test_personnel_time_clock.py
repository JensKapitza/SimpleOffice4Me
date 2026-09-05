import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from app import app
from app import db as database
from app.contact_store import ContactStore
from app.personnel import _local_now


class PersonnelTimeClockTest(unittest.TestCase):
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
        self.root = root

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _add_employee(self, name="Mitarbeiter Eins", email="mitarbeiter@example.test"):
        contact = ContactStore(self.root / "docs").upsert({"display_name": name, "email": email}, "jens")
        response = self.client.post("/personnel/employees", data={"contact_id": contact["contact_id"]})
        self.assertEqual(302, response.status_code)
        with app.app_context():
            row = database.get_db().execute(
                "SELECT * FROM employee WHERE contact_id=?", (contact["contact_id"],)
            ).fetchone()
            return int(row["id"])

    def test_admin_gets_own_time_account_and_can_punch_without_manual_employee_enrolment(self):
        response = self.client.get("/personnel")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Stempeluhr verwalten", response.data)
        with app.app_context():
            db = database.get_db()
            user_id = int(db.execute("SELECT id FROM user WHERE username='jens'").fetchone()[0])
            employee = db.execute("SELECT * FROM employee WHERE user_id=?", (user_id,)).fetchone()
            self.assertIsNotNone(employee)
            employee_id = int(employee["id"])
        response = self.client.post("/personnel/punch/clock_in")
        self.assertEqual(302, response.status_code)
        with app.app_context():
            row = database.get_db().execute(
                "SELECT * FROM employee_punch WHERE employee_id=? ORDER BY id DESC LIMIT 1", (employee_id,)
            ).fetchone()
            self.assertEqual("clock_in", row["action"])

    def test_self_service_guides_user_through_only_valid_next_punches(self):
        response = self.client.get("/personnel")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Noch nicht eingestempelt", response.data)
        self.assertIn(b"/personnel/punch/clock_in", response.data)
        self.assertNotIn(b"/personnel/punch/break_start", response.data)
        self.assertNotIn(b"/personnel/punch/break_end", response.data)
        self.assertNotIn(b"/personnel/punch/clock_out", response.data)

        self.assertEqual(302, self.client.post("/personnel/punch/clock_in").status_code)
        response = self.client.get("/personnel")
        self.assertIn(b"Arbeitszeit l\xc3\xa4uft", response.data)
        self.assertIn(b"/personnel/punch/break_start", response.data)
        self.assertIn(b"/personnel/punch/clock_out", response.data)
        self.assertNotIn(b"/personnel/punch/clock_in", response.data)
        self.assertNotIn(b"/personnel/punch/break_end", response.data)
        self.assertNotIn(b"Aktueller Status: clock_in", response.data)

        self.assertEqual(302, self.client.post("/personnel/punch/break_start").status_code)
        response = self.client.get("/personnel")
        self.assertIn(b"Pause l\xc3\xa4uft", response.data)
        self.assertIn(b"/personnel/punch/break_end", response.data)
        self.assertNotIn(b"/personnel/punch/break_start", response.data)
        self.assertNotIn(b"/personnel/punch/clock_out", response.data)

        self.assertEqual(302, self.client.post("/personnel/punch/break_end").status_code)
        response = self.client.get("/personnel")
        self.assertIn(b"Arbeitszeit l\xc3\xa4uft wieder", response.data)
        self.assertIn(b"/personnel/punch/break_start", response.data)
        self.assertIn(b"/personnel/punch/clock_out", response.data)
        self.assertNotIn(b"/personnel/punch/break_end", response.data)

    def test_admin_quick_clock_shows_only_valid_next_actions(self):
        self.client.get("/personnel")
        employee_id = self._add_employee("Gefuehrter Mitarbeiter", "guided@example.test")
        response = self.client.get(f"/personnel/time-admin?employee_id={employee_id}")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Nicht eingestempelt", response.data)
        self.assertIn(f"/{employee_id}/punch/clock_in".encode(), response.data)
        self.assertNotIn(f"/{employee_id}/punch/break_start".encode(), response.data)

        self.assertEqual(302, self.client.post(f"/personnel/time-admin/{employee_id}/punch/clock_in").status_code)
        response = self.client.get(f"/personnel/time-admin?employee_id={employee_id}")
        self.assertIn(b"Arbeitszeit l\xc3\xa4uft", response.data)
        self.assertIn(f"/{employee_id}/punch/break_start".encode(), response.data)
        self.assertIn(f"/{employee_id}/punch/clock_out".encode(), response.data)
        self.assertNotIn(f"/{employee_id}/punch/break_end".encode(), response.data)

    def test_admin_can_clock_employee_now_and_audit_actor_is_preserved(self):
        self.client.get("/personnel")
        employee_id = self._add_employee()
        response = self.client.post(f"/personnel/time-admin/{employee_id}/punch/clock_in")
        self.assertEqual(302, response.status_code)
        response = self.client.post(f"/personnel/time-admin/{employee_id}/punch/clock_out")
        self.assertEqual(302, response.status_code)
        with app.app_context():
            db = database.get_db()
            user_id = int(db.execute("SELECT id FROM user WHERE username='jens'").fetchone()[0])
            punches = db.execute(
                "SELECT * FROM employee_punch WHERE employee_id=? ORDER BY id", (employee_id,)
            ).fetchall()
            self.assertEqual(["clock_in", "clock_out"], [row["action"] for row in punches])
            self.assertTrue(all(int(row["recorded_by"]) == user_id for row in punches))
            audits = db.execute(
                "SELECT action,actor_user_id FROM employee_time_audit WHERE employee_id=? ORDER BY id", (employee_id,)
            ).fetchall()
            self.assertEqual(2, sum(row["action"] == "admin_quick_punch" for row in audits))
            self.assertTrue(all(int(row["actor_user_id"]) == user_id for row in audits))

    def test_admin_can_add_and_correct_forgotten_punch_with_reason_and_history(self):
        self.client.get("/personnel")
        employee_id = self._add_employee("Vergesslicher Mitarbeiter", "vergessen@example.test")
        with app.app_context():
            now = _local_now().replace(second=0, microsecond=0)
        start = now - timedelta(hours=2)
        end = now - timedelta(minutes=10)
        response = self.client.post(
            f"/personnel/time-admin/{employee_id}/add",
            data={"action": "clock_in", "occurred_at": start.strftime("%Y-%m-%dT%H:%M"), "reason": "Kommen vergessen"},
        )
        self.assertEqual(302, response.status_code)
        response = self.client.post(
            f"/personnel/time-admin/{employee_id}/add",
            data={"action": "clock_out", "occurred_at": end.strftime("%Y-%m-%dT%H:%M"), "reason": "Gehen nachgetragen"},
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            db = database.get_db()
            punch = db.execute(
                "SELECT * FROM employee_punch WHERE employee_id=? AND action='clock_in' ORDER BY id LIMIT 1", (employee_id,)
            ).fetchone()
            punch_id = int(punch["id"])
        corrected = start - timedelta(minutes=15)
        response = self.client.post(
            f"/personnel/time-admin/punch/{punch_id}/edit",
            data={"action": "clock_in", "occurred_at": corrected.strftime("%Y-%m-%dT%H:%M"), "reason": "Startzeit laut Einsatznotiz korrigiert"},
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            db = database.get_db()
            audit = db.execute(
                "SELECT * FROM employee_time_audit WHERE punch_id=? AND action='admin_punch_updated' ORDER BY id DESC LIMIT 1",
                (punch_id,),
            ).fetchone()
            self.assertIsNotNone(audit)
            self.assertEqual("Startzeit laut Einsatznotiz korrigiert", audit["reason"])
            self.assertIn("occurred_at", audit["before_json"])
            self.assertIn("occurred_at", audit["after_json"])

    def test_non_admin_cannot_use_time_admin(self):
        self.client.post("/auth/logout")
        self.client.post("/auth/register", data={"username": "kollege", "password": "sicheres-passwort"})
        self.client.post("/auth/login", data={"username": "kollege", "password": "sicheres-passwort"})
        self.assertEqual(403, self.client.get("/personnel/time-admin").status_code)


if __name__ == "__main__":
    unittest.main()
