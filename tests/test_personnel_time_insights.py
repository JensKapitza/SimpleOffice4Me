from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.federation_store import FederationStore
from app.personnel import _local_now, _personnel_timezone
from app.personnel_time_insights import cross_area_statistics, ensure_schema, period_statistics
from app.project_store import ProjectStore
from app.todo_store import TodoStore


class PersonnelTimeInsightsTest(unittest.TestCase):
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
        self.assertEqual(200, self.client.get("/personnel").status_code)
        with app.app_context():
            self.user_id = int(database.get_db().execute("SELECT id FROM user WHERE username='jens'").fetchone()[0])
            self.employee = dict(database.get_db().execute("SELECT * FROM employee WHERE user_id=?", (self.user_id,)).fetchone())
            ensure_schema()

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _stamp(self, shown, hour: int, minute: int = 0) -> str:
        local = datetime.combine(shown, time(hour, minute), _personnel_timezone())
        return local.astimezone(timezone.utc).isoformat(timespec="seconds")

    def _insert_local_day(self, shown, start_hour=8, end_hour=16):
        with app.app_context():
            db = database.get_db()
            for action, stamp in (
                ("clock_in", self._stamp(shown, start_hour)),
                ("clock_out", self._stamp(shown, end_hour)),
            ):
                db.execute(
                    "INSERT INTO employee_punch(employee_id,action,occurred_at,recorded_by) VALUES(?,?,?,?)",
                    (self.employee["id"], action, stamp, self.user_id),
                )
            db.commit()

    def test_statistics_and_cross_area_time_use_existing_canonical_sources(self):
        shown = _local_now().date() - timedelta(days=1)
        self._insert_local_day(shown)
        with app.app_context():
            db = database.get_db()
            schedule = {str(shown.weekday()): {"hours": 8, "start": "08:00"}}
            import json
            db.execute("UPDATE employee SET schedule_json=? WHERE id=?", (json.dumps(schedule), self.employee["id"]))
            db.commit()
            employee = dict(db.execute(
                "SELECT employee.*,user.username,user.display_name FROM employee LEFT JOIN user ON user.id=employee.user_id WHERE employee.id=?",
                (self.employee["id"],),
            ).fetchone())
            project_store = ProjectStore(self.root / "docs")
            project = project_store.create_project({"title": "Einsatz"}, "jens")
            task = project_store.add_task(project["project_id"], {"title": "Montage"}, "jens")
            project_store.book_time(project["project_id"], task["task_id"], shown.isoformat(), "2", "Montage", "jens", "30")
            todo_store = TodoStore(self.root / "docs")
            todo = todo_store.add("Dokumentation", "jens")
            todo_store.book_time(todo["id"], 30, "Nachbereitung", "jens", shown.isoformat())
            stats = period_statistics(employee, shown, shown)
            areas = cross_area_statistics(employee, shown, shown)
        self.assertEqual(8 * 60, stats["totals"]["work_minutes"])
        self.assertEqual(8 * 60, stats["totals"]["presence_target_minutes"])
        self.assertEqual(0, stats["totals"]["balance_minutes"])
        self.assertEqual(180, areas["total_minutes"])
        self.assertEqual(150, areas["project_minutes"])
        self.assertEqual(30, areas["task_minutes"])
        response = self.client.get(f"/personnel/time-admin/insights?employee_id={self.employee['id']}&period=custom&start={shown}&end={shown}")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Statistik &amp; Federation", response.data)
        self.assertIn(b"Gebuchte Fachzeit", response.data)

    def test_federation_export_is_default_deny_and_opt_in(self):
        response = self.client.get("/federation/v1/personnel/time/capabilities")
        self.assertEqual(403, response.status_code)
        response = self.client.post(
            "/personnel/time-admin/insights/federation/export",
            data={"employee_id": self.employee["id"], "enabled": "1"},
        )
        self.assertEqual(302, response.status_code)
        response = self.client.get("/federation/v1/personnel/time/capabilities")
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("personnel_time", payload["resource"])
        self.assertFalse(payload["reexports_federated_events"])
        employees = self.client.get("/federation/v1/personnel/time/employees").get_json()["employees"]
        self.assertEqual(1, len(employees))
        self.assertEqual(self.employee["contact_id"], employees[0]["employee_key"])
        self.assertNotIn("email", employees[0])

    def test_federation_import_is_idempotent_updates_remote_correction_and_never_reexports(self):
        shown = _local_now().date() - timedelta(days=1)
        peer_id = "branch-office"
        remote_key = "remote-employee-1"
        with app.app_context():
            FederationStore(self.root / "docs").save_peer(peer_id, "Außenstelle", "https://branch.example.test", "secret-token", enabled=True)
        first = {
            "employee_key": remote_key,
            "complete_range": True,
            "events": [
                {"event_key": "a", "action": "clock_in", "occurred_at": self._stamp(shown, 8)},
                {"event_key": "b", "action": "clock_out", "occurred_at": self._stamp(shown, 16)},
            ],
        }
        form = {
            "peer_id": peer_id,
            "remote_employee_key": remote_key,
            "employee_id": str(self.employee["id"]),
            "start": shown.isoformat(),
            "end": shown.isoformat(),
        }
        with patch("app.personnel_time_insights._json_request", return_value=first):
            self.assertEqual(302, self.client.post("/personnel/time-admin/insights/federation/import", data=form).status_code)
            self.assertEqual(302, self.client.post("/personnel/time-admin/insights/federation/import", data=form).status_code)
        with app.app_context():
            db = database.get_db()
            rows = db.execute(
                "SELECT * FROM employee_punch WHERE employee_id=? AND source_kind='federation' ORDER BY occurred_at",
                (self.employee["id"],),
            ).fetchall()
            self.assertEqual(2, len(rows))
            self.assertTrue(all(row["source_peer"] == peer_id for row in rows))
            mapping = db.execute("SELECT * FROM employee_time_federation_map WHERE peer_id=?", (peer_id,)).fetchone()
            self.assertEqual(self.employee["id"], mapping["local_employee_id"])
        corrected = {**first, "events": [first["events"][0], {**first["events"][1], "occurred_at": self._stamp(shown, 15, 30)}]}
        with patch("app.personnel_time_insights._json_request", return_value=corrected):
            self.assertEqual(302, self.client.post("/personnel/time-admin/insights/federation/import", data=form).status_code)
        with app.app_context():
            db = database.get_db()
            rows = db.execute(
                "SELECT * FROM employee_punch WHERE employee_id=? AND source_kind='federation' ORDER BY occurred_at",
                (self.employee["id"],),
            ).fetchall()
            self.assertEqual(2, len(rows))
            self.assertEqual(self._stamp(shown, 15, 30), rows[1]["occurred_at"])
            audits = db.execute(
                "SELECT action FROM employee_time_audit WHERE employee_id=? AND action LIKE 'federation_punch_%' ORDER BY id",
                (self.employee["id"],),
            ).fetchall()
            self.assertEqual(3, len(audits))
            self.assertEqual("federation_punch_updated", audits[-1]["action"])
        self.client.post(
            "/personnel/time-admin/insights/federation/export",
            data={"employee_id": self.employee["id"], "enabled": "1"},
        )
        response = self.client.get(
            "/federation/v1/personnel/time/punches",
            query_string={"employee_key": self.employee["contact_id"], "start": shown.isoformat(), "end": shown.isoformat()},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.get_json()["events"])

    def test_invalid_remote_sequence_rolls_back_entire_import(self):
        shown = _local_now().date() - timedelta(days=1)
        peer_id = "broken-office"
        with app.app_context():
            FederationStore(self.root / "docs").save_peer(peer_id, "Fehlerstelle", "https://broken.example.test", "secret-token", enabled=True)
        payload = {
            "employee_key": "remote-broken",
            "complete_range": True,
            "events": [{"event_key": "bad", "action": "clock_out", "occurred_at": self._stamp(shown, 8)}],
        }
        with patch("app.personnel_time_insights._json_request", return_value=payload):
            response = self.client.post(
                "/personnel/time-admin/insights/federation/import",
                data={
                    "peer_id": peer_id,
                    "remote_employee_key": "remote-broken",
                    "employee_id": self.employee["id"],
                    "start": shown.isoformat(),
                    "end": shown.isoformat(),
                },
            )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            count = database.get_db().execute(
                "SELECT COUNT(*) FROM employee_punch WHERE employee_id=? AND source_kind='federation'",
                (self.employee["id"],),
            ).fetchone()[0]
            self.assertEqual(0, count)

    def test_non_admin_cannot_open_statistics_workspace(self):
        self.client.post("/auth/logout")
        self.client.post("/auth/register", data={"username": "kollege", "password": "sicheres-passwort"})
        self.client.post("/auth/login", data={"username": "kollege", "password": "sicheres-passwort"})
        self.assertEqual(403, self.client.get("/personnel/time-admin/insights").status_code)


if __name__ == "__main__":
    unittest.main()
