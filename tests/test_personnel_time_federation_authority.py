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


class PersonnelTimeFederationAuthorityTest(unittest.TestCase):
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
            self.employee_id = int(db.execute(
                "SELECT employee.id FROM employee JOIN user ON user.id=employee.user_id WHERE user.username='jens'"
            ).fetchone()[0])
            ensure_schema()
            FederationStore(self.root / "docs").save_peer(
                "authority-peer", "Außenstelle", "https://authority.example.test", "secret-token", enabled=True
            )

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _stamp(self, shown, hour):
        local = datetime.combine(shown, time(hour, 0), _personnel_timezone())
        return local.astimezone(timezone.utc).isoformat(timespec="seconds")

    def test_direct_edit_and_delete_cannot_modify_federation_owned_punch(self):
        shown = _local_now().date() - timedelta(days=1)
        payload = {
            "employee_key": "remote-authority",
            "employee_label": "Remote Mitarbeiter",
            "complete_range": True,
            "events": [
                {"event_key": "in", "action": "clock_in", "occurred_at": self._stamp(shown, 8)},
                {"event_key": "out", "action": "clock_out", "occurred_at": self._stamp(shown, 16)},
            ],
        }
        with patch("app.personnel_time_insights._json_request", return_value=payload):
            response = self.client.post(
                "/personnel/time-admin/insights/federation/import",
                data={
                    "peer_id": "authority-peer",
                    "remote_employee_key": "remote-authority",
                    "employee_id": self.employee_id,
                    "start": shown.isoformat(),
                    "end": shown.isoformat(),
                },
            )
        self.assertEqual(302, response.status_code)
        with app.app_context():
            db = database.get_db()
            punch = db.execute(
                "SELECT * FROM employee_punch WHERE employee_id=? AND source_kind='federation' ORDER BY occurred_at LIMIT 1",
                (self.employee_id,),
            ).fetchone()
            punch_id = int(punch["id"])
            original_action = str(punch["action"])
            original_stamp = str(punch["occurred_at"])

        response = self.client.post(
            f"/personnel/time-admin/punch/{punch_id}/edit",
            data={"action": "break_start", "occurred_at": f"{shown.isoformat()}T09:00", "reason": "lokaler Test"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Quellstandort", response.data)
        with app.app_context():
            row = database.get_db().execute("SELECT * FROM employee_punch WHERE id=?", (punch_id,)).fetchone()
            self.assertEqual(original_action, row["action"])
            self.assertEqual(original_stamp, row["occurred_at"])

        response = self.client.post(
            f"/personnel/time-admin/punch/{punch_id}/delete",
            data={"reason": "lokaler Test"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn(b"Quellstandort", response.data)
        with app.app_context():
            row = database.get_db().execute("SELECT * FROM employee_punch WHERE id=?", (punch_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual("federation", row["source_kind"])

    def test_normal_time_admin_request_migrates_provenance_columns(self):
        with app.app_context():
            db = database.get_db()
            for column in ("source_kind", "source_peer", "source_ref", "source_imported_at"):
                self.assertIn(column, {row["name"] for row in db.execute("PRAGMA table_info(employee_punch)")})
        response = self.client.get(f"/personnel/time-admin?employee_id={self.employee_id}")
        self.assertEqual(200, response.status_code)


if __name__ == "__main__":
    unittest.main()
