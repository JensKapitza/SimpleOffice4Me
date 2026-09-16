import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app import personnel
from app.contact_store import ContactStore
from app.federation_store import FederationStore
from app.personnel_time_analytics import ensure_schema, run_auto_sync_once, site_statistics, team_statistics


class PersonnelTimeAnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(self.root / "users.sqlite"), DOCUMENT_ROOT=str(self.root / "docs"))
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "sicheres-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "sicheres-passwort"})
        self.client.get("/personnel")
        with app.app_context():
            db = database.get_db()
            self.user_id = int(db.execute("SELECT id FROM user WHERE username='jens'").fetchone()[0])
            self.employee_id = int(db.execute("SELECT id FROM employee WHERE user_id=?", (self.user_id,)).fetchone()[0])
            ensure_schema()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _add_employee(self, name="Zweiter Mitarbeiter", email="team@example.test"):
        contact = ContactStore(self.root / "docs").upsert({"display_name": name, "email": email}, "jens")
        self.client.post("/personnel/employees", data={"contact_id": contact["contact_id"]})
        with app.app_context():
            return int(database.get_db().execute("SELECT id FROM employee WHERE contact_id=?", (contact["contact_id"],)).fetchone()[0])

    def _insert_day(self, employee_id, start_hour=8, end_hour=16, *, source_kind="local", source_peer=""):
        shown = personnel._local_now().date()
        zone = personnel._personnel_timezone()
        start = datetime.combine(shown, datetime.min.time(), zone).replace(hour=start_hour).astimezone(timezone.utc).isoformat(timespec="seconds")
        end = datetime.combine(shown, datetime.min.time(), zone).replace(hour=end_hour).astimezone(timezone.utc).isoformat(timespec="seconds")
        with app.app_context():
            db = database.get_db()
            db.execute(
                "INSERT INTO employee_punch(employee_id,action,occurred_at,recorded_by,source_kind,source_peer,source_ref) VALUES(?,?,?,?,?,?,?)",
                (employee_id, "clock_in", start, self.user_id, source_kind, source_peer, f"{source_peer}:in:{employee_id}" if source_peer else ""),
            )
            db.execute(
                "INSERT INTO employee_punch(employee_id,action,occurred_at,recorded_by,source_kind,source_peer,source_ref) VALUES(?,?,?,?,?,?,?)",
                (employee_id, "clock_out", end, self.user_id, source_kind, source_peer, f"{source_peer}:out:{employee_id}" if source_peer else ""),
            )
            db.commit()
        return shown

    def _mapping(self, peer_id="peer-a", remote_key="remote-1", employee_id=None, auto_sync=0):
        employee_id = employee_id or self.employee_id
        with app.app_context():
            ensure_schema()
            db = database.get_db()
            db.execute(
                """INSERT INTO employee_time_federation_map(
                       peer_id,remote_employee_key,local_employee_id,remote_label,updated_at,updated_by,
                       auto_sync,sync_interval_minutes,sync_window_days,last_attempt_at,last_success_at,last_sync_status,last_sync_error
                   ) VALUES(?,?,?,?,?,?,?,?,?,'','','','')""",
                (peer_id, remote_key, employee_id, "Remote Eins", datetime.now(timezone.utc).isoformat(), self.user_id, auto_sync, 15, 7),
            )
            db.commit()

    def test_analytics_page_contains_team_site_and_auto_sync_sections(self):
        self._insert_day(self.employee_id)
        response = self.client.get(f"/personnel/time-admin/analytics?employee_id={self.employee_id}&period=7d")
        self.assertEqual(200, response.status_code)
        self.assertIn("Teamstatistik".encode(), response.data)
        self.assertIn("Standort-/Quellenstatistik".encode(), response.data)
        self.assertIn("automatische Synchronisation".encode(), response.data)
        self.assertIn("Wochen".encode(), response.data)
        self.assertIn("Monate".encode(), response.data)

    def test_team_and_site_statistics_use_canonical_punches(self):
        second = self._add_employee()
        shown = self._insert_day(self.employee_id, 8, 16)
        self._insert_day(second, 9, 15, source_kind="federation", source_peer="peer-a")
        with app.app_context():
            team = team_statistics(shown, shown)
            sites = {row["source"]: row for row in site_statistics(shown, shown)}
        self.assertEqual(2, team["totals"]["employees"])
        self.assertEqual(14 * 60, team["totals"]["work_minutes"])
        self.assertEqual(8 * 60, sites["local"]["work_minutes"])
        self.assertEqual(6 * 60, sites["peer-a"]["work_minutes"])
        self.assertEqual(1, sites["peer-a"]["employee_count"])

    def test_auto_sync_settings_are_persisted(self):
        self._mapping()
        response = self.client.post(
            "/personnel/time-admin/analytics/autosync/settings",
            data={
                "peer_id": "peer-a",
                "remote_employee_key": "remote-1",
                "employee_id": str(self.employee_id),
                "period": "30d",
                "auto_sync": "1",
                "sync_interval_minutes": "30",
                "sync_window_days": "21",
            },
        )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            row = database.get_db().execute(
                "SELECT * FROM employee_time_federation_map WHERE peer_id='peer-a' AND remote_employee_key='remote-1'"
            ).fetchone()
            self.assertEqual(1, int(row["auto_sync"]))
            self.assertEqual(30, int(row["sync_interval_minutes"]))
            self.assertEqual(21, int(row["sync_window_days"]))

    def test_due_auto_sync_imports_remote_punches_and_records_success(self):
        self._mapping(auto_sync=1)
        with app.app_context():
            FederationStore(self.root / "docs").save_peer(
                "peer-a", "Standort A", "https://peer-a.example.test", "token-a", {}, True
            )
        shown = personnel._local_now().date()
        zone = personnel._personnel_timezone()
        start = datetime.combine(shown, datetime.min.time(), zone).replace(hour=8).astimezone(timezone.utc).isoformat(timespec="seconds")
        end = datetime.combine(shown, datetime.min.time(), zone).replace(hour=16).astimezone(timezone.utc).isoformat(timespec="seconds")

        def fake_json(url, **_kwargs):
            if url.endswith("/capabilities"):
                return {"resource": "personnel_time", "schema": 1}
            return {
                "employee_key": "remote-1",
                "employee_label": "Remote Eins",
                "complete_range": True,
                "events": [
                    {"event_key": "one", "action": "clock_in", "occurred_at": start},
                    {"event_key": "two", "action": "clock_out", "occurred_at": end},
                ],
            }

        with app.app_context(), patch("app.personnel_time_analytics._json_request", side_effect=fake_json):
            result = run_auto_sync_once()
            rows = database.get_db().execute(
                "SELECT * FROM employee_punch WHERE employee_id=? AND source_kind='federation' ORDER BY occurred_at",
                (self.employee_id,),
            ).fetchall()
            mapping = database.get_db().execute(
                "SELECT * FROM employee_time_federation_map WHERE peer_id='peer-a' AND remote_employee_key='remote-1'"
            ).fetchone()
        self.assertEqual(1, result["synced"])
        self.assertEqual(2, result["inserted"])
        self.assertEqual(["clock_in", "clock_out"], [row["action"] for row in rows])
        self.assertEqual("success", mapping["last_sync_status"])
        self.assertTrue(mapping["last_success_at"])

    def test_disabled_mapping_is_not_run_automatically(self):
        self._mapping(auto_sync=0)
        with app.app_context():
            result = run_auto_sync_once()
        self.assertEqual(0, result["checked"])
        self.assertEqual(0, result["synced"])


if __name__ == "__main__":
    unittest.main()
