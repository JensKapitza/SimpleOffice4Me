import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.contact_store import ContactStore
from app.personnel import _absence_days, _day_summary, _schedule_summary, close_due_months, required_break_minutes


class PersonnelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(root / "users.sqlite"), DOCUMENT_ROOT=str(root / "docs"))
        with app.app_context(): database.ensure_auth_database()
        self.client = app.test_client(); self.client.post("/auth/register", data={"username":"jens","password":"sicheres-passwort"}); self.client.post("/auth/login", data={"username":"jens","password":"sicheres-passwort"})
        self.contact = ContactStore(root / "docs").upsert({"display_name":"Jens Kapitza","email":"jens@example.test"}, "jens")

    def tearDown(self): app.config.update(self.saved); self.temp.cleanup()

    def test_break_thresholds(self):
        self.assertEqual(0, required_break_minutes(360)); self.assertEqual(15, required_break_minutes(361)); self.assertEqual(30, required_break_minutes(480)); self.assertEqual(30, required_break_minutes(540)); self.assertEqual(45, required_break_minutes(541))

    def test_schedule_summary_calculates_end_and_weekdays(self):
        self.assertEqual("16:30", _schedule_summary({"0":{"start":"08:00","hours":8.5}}, 0)["end"])
        self.assertEqual(5, _absence_days("2026-09-07", "2026-09-13"))

    def test_admin_enrols_contact_and_generated_account_is_disabled(self):
        response = self.client.post("/personnel/employees", data={"contact_id":self.contact["contact_id"]})
        self.assertEqual(302, response.status_code)
        with app.app_context():
            employee = database.get_db().execute("SELECT * FROM employee").fetchone(); user = database.get_db().execute("SELECT * FROM user WHERE id=?", (employee["user_id"],)).fetchone()
            self.assertEqual(self.contact["contact_id"], employee["contact_id"]); self.assertEqual(1, user["is_disabled"])

    def test_schedule_rejects_more_than_ten_hours(self):
        self.client.post("/personnel/employees", data={"contact_id":self.contact["contact_id"]})
        data = {f"start_{i}":"08:00" for i in range(7)} | {f"hours_{i}":"0" for i in range(7)}; data["hours_0"] = "10.25"
        self.client.post("/personnel/employees/1/settings", data=data)
        with app.app_context(): self.assertEqual("{}", database.get_db().execute("SELECT schedule_json FROM employee WHERE id=1").fetchone()[0])

    def test_previous_month_is_frozen_on_tenth(self):
        self.client.post("/personnel/employees", data={"contact_id":self.contact["contact_id"]})
        with app.app_context():
            self.assertEqual(0, close_due_months(__import__("datetime").date(2026, 9, 9)))
            self.assertEqual(1, close_due_months(__import__("datetime").date(2026, 9, 10)))
            row = database.get_db().execute("SELECT month FROM employee_month_close").fetchone()
            self.assertEqual("2026-08", row["month"])
            self.assertEqual(0, close_due_months(__import__("datetime").date(2026, 9, 20)))

    def test_overlapping_absence_is_rejected_and_open_request_can_be_cancelled(self):
        self.client.post("/personnel/employees", data={"contact_id":self.contact["contact_id"]})
        with app.app_context():
            user_id = database.get_db().execute("SELECT id FROM user WHERE username='jens'").fetchone()[0]
            database.get_db().execute("UPDATE employee SET user_id=?", (user_id,)); database.get_db().commit()
        self.client.post("/personnel/absence", data={"kind":"urlaub","starts_on":"2026-09-07","ends_on":"2026-09-11","tags":"Sommer"})
        self.client.post("/personnel/absence", data={"kind":"frei","starts_on":"2026-09-10","ends_on":"2026-09-12"})
        with app.app_context():
            rows = database.get_db().execute("SELECT * FROM employee_absence").fetchall()
            self.assertEqual(1, len(rows)); absence_id = rows[0]["id"]
        self.assertEqual(302, self.client.post(f"/personnel/absence/{absence_id}/cancel").status_code)
        with app.app_context(): self.assertEqual("cancelled", database.get_db().execute("SELECT status FROM employee_absence WHERE id=?", (absence_id,)).fetchone()[0])

    def test_personnel_page_contains_agenda_and_state_aware_punches(self):
        self.client.post("/personnel/employees", data={"contact_id":self.contact["contact_id"]})
        with app.app_context():
            user_id = database.get_db().execute("SELECT id FROM user WHERE username='jens'").fetchone()[0]
            database.get_db().execute("UPDATE employee SET user_id=?", (user_id,)); database.get_db().commit()
        response = self.client.get("/personnel")
        self.assertIn(b"Personalagenda", response.data)
        self.assertIn(b"Wochen-Soll", response.data)
        self.assertGreaterEqual(response.data.count(b"<tr>"), 15)

    def test_punch_after_local_midnight_belongs_to_new_day(self):
        self.client.post("/personnel/employees", data={"contact_id":self.contact["contact_id"]})
        with app.app_context():
            db = database.get_db(); employee_id = db.execute("SELECT id FROM employee").fetchone()[0]; user_id = db.execute("SELECT id FROM user WHERE username='jens'").fetchone()[0]
            db.execute("INSERT INTO employee_punch(employee_id,action,occurred_at,recorded_by) VALUES(?,?,?,?)", (employee_id, "clock_in", "2026-08-31T22:30:00+00:00", user_id)); db.commit()
            self.assertEqual([], _day_summary(employee_id, __import__("datetime").date(2026, 8, 31))["events"])
            self.assertEqual("clock_in", _day_summary(employee_id, __import__("datetime").date(2026, 9, 1))["state"])


if __name__ == "__main__": unittest.main()
