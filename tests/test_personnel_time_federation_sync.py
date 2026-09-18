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
from app.personnel_time_insights import ensure_schema


class PersonnelTimeFederationSyncTest(unittest.TestCase):
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
            self.employee_id = int(db.execute("SELECT employee.id FROM employee JOIN user ON user.id=employee.user_id WHERE user.username='jens'").fetchone()[0])
            self.contact_id = str(db.execute("SELECT contact_id FROM employee WHERE id=?", (self.employee_id,)).fetchone()[0])
            ensure_schema()
            FederationStore(self.root / "docs").save_peer(
                "branch-sync", "Zweigstelle", "https://branch.example.test", "secret-token", enabled=True
            )

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _stamp(self, shown, hour, minute=0):
        local = datetime.combine(shown, time(hour, minute), _personnel_timezone())
        return local.astimezone(timezone.utc).isoformat(timespec="seconds")

    def _import(self, shown, events):
        payload = {
            "employee_key": "remote-1",
            "employee_label": "Remote Mitarbeiter",
            "complete_range": True,
            "events": events,
        }
        with patch("app.personnel_time_insights._json_request", return_value=payload):
            return self.client.post(
                "/personnel/time-admin/insights/federation/import",
                data={
                    "peer_id": "branch-sync",
                    "remote_employee_key": "remote-1",
                    "employee_id": self.employee_id,
                    "start": shown.isoformat(),
                    "end": shown.isoformat(),
                },
            )

    def test_complete_range_removes_remote_event_deleted_at_source(self):
        shown = _local_now().date() - timedelta(days=1)
        events = [
            {"event_key": "in", "action": "clock_in", "occurred_at": self._stamp(shown, 8)},
            {"event_key": "break-in", "action": "break_start", "occurred_at": self._stamp(shown, 12)},
            {"event_key": "break-out", "action": "break_end", "occurred_at": self._stamp(shown, 12, 30)},
            {"event_key": "out", "action": "clock_out", "occurred_at": self._stamp(shown, 16, 30)},
        ]
        self.assertEqual(302, self._import(shown, events).status_code)
        without_break = [events[0], events[-1]]
        self.assertEqual(302, self._import(shown, without_break).status_code)
        with app.app_context():
            db = database.get_db()
            punches = db.execute(
                "SELECT action,source_ref FROM employee_punch WHERE employee_id=? AND source_peer='branch-sync' ORDER BY occurred_at",
                (self.employee_id,),
            ).fetchall()
            self.assertEqual(["clock_in", "clock_out"], [row["action"] for row in punches])
            removed = db.execute(
                "SELECT COUNT(*) FROM employee_time_audit WHERE employee_id=? AND action='federation_punch_removed'",
                (self.employee_id,),
            ).fetchone()[0]
            self.assertEqual(2, removed)
            mapping = db.execute(
                "SELECT remote_label FROM employee_time_federation_map WHERE peer_id='branch-sync' AND remote_employee_key='remote-1'"
            ).fetchone()
            self.assertEqual("Remote Mitarbeiter", mapping["remote_label"])

    def test_remote_delete_in_closed_month_is_rejected_without_partial_change(self):
        shown = _local_now().date() - timedelta(days=35)
        events = [
            {"event_key": "in", "action": "clock_in", "occurred_at": self._stamp(shown, 8)},
            {"event_key": "out", "action": "clock_out", "occurred_at": self._stamp(shown, 16)},
        ]
        self.assertEqual(302, self._import(shown, events).status_code)
        with app.app_context():
            db = database.get_db()
            db.execute(
                "INSERT INTO employee_month_close(employee_id,month,work_minutes,break_minutes,closed_at) VALUES(?,?,?,?,?)",
                (self.employee_id, shown.strftime("%Y-%m"), 480, 0, datetime.now(timezone.utc).isoformat()),
            )
            db.commit()
        self.assertEqual(302, self._import(shown, []).status_code)
        with app.app_context():
            db = database.get_db()
            count = db.execute(
                "SELECT COUNT(*) FROM employee_punch WHERE employee_id=? AND source_peer='branch-sync'",
                (self.employee_id,),
            ).fetchone()[0]
            self.assertEqual(2, count)
            removed = db.execute(
                "SELECT COUNT(*) FROM employee_time_audit WHERE employee_id=? AND action='federation_punch_removed'",
                (self.employee_id,),
            ).fetchone()[0]
            self.assertEqual(0, removed)


if __name__ == "__main__":
    unittest.main()
