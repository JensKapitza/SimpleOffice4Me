import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.contact_store import ContactStore
from app.personnel import close_due_months, required_break_minutes


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
        self.assertEqual(0, required_break_minutes(360)); self.assertEqual(30, required_break_minutes(361)); self.assertEqual(30, required_break_minutes(540)); self.assertEqual(45, required_break_minutes(541))

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


if __name__ == "__main__": unittest.main()
