import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.printershare_quickwins import _clamp_int, _parse_bool, _safe_job, run_maintenance
from app.printershare_store import PrinterShareStore


class PrinterShareQuickWinsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.root = Path(self.temp.name) / "documents"
        app.config.update(
            TESTING=True,
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", generate_password_hash("admin-password")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("alice", generate_password_hash("alice-password")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("bob", generate_password_hash("bob-password")),
            )
            db.commit()
        self.store = PrinterShareStore(self.root, app.config["SECRET_KEY"])
        self.admin = app.test_client()
        self.alice = app.test_client()
        self.bob = app.test_client()
        self.admin.post("/auth/login", data={"username": "admin", "password": "admin-password"})
        self.alice.post("/auth/login", data={"username": "alice", "password": "alice-password"})
        self.bob.post("/auth/login", data={"username": "bob", "password": "bob-password"})
        self._insert_job("alice-ok", "alice", "spooled", "no_store", "")
        self._insert_job("alice-failed", "alice", "failed", "ttl", "printershare-retained/missing.printenc")
        self._insert_job("bob-ok", "bob", "spooled", "no_store", "")
        self._insert_job("remote-ok", "peer-a", "spooled", "no_store", "", source="federation")

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def _insert_job(self, job_id, source_peer, status, retention, payload_path, source="web"):
        now = int(time.time())
        with sqlite3.connect(self.store.jobs_path) as db:
            db.execute(
                """INSERT INTO print_job(
                       job_id,source,source_peer,printer_id,printer_name,status,retention,
                       expires_at,payload_path,content_type,payload_size,payload_sha256,
                       policy_revision,spool_reference,error,created_at,completed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id, source, source_peer, "printer-1", "Office Printer", status, retention,
                    now + 3600 if retention == "ttl" else 0, payload_path, "application/pdf", 1234,
                    "a" * 64, "policy-1", "spool-42", "paper jam" if status == "failed" else "",
                    now, now if status == "spooled" else 0,
                ),
            )

    def test_helpers_clamp_numbers(self):
        self.assertEqual(5, _clamp_int("5", 1, 1, 10))
        self.assertEqual(1, _clamp_int("-3", 1, 1, 10))
        self.assertEqual(10, _clamp_int("100", 1, 1, 10))
        self.assertEqual(7, _clamp_int("bad", 7, 1, 10))

    def test_helpers_parse_booleans(self):
        self.assertIs(_parse_bool("yes"), True)
        self.assertIs(_parse_bool("0"), False)
        self.assertIsNone(_parse_bool("maybe"))
        self.assertIsNone(_parse_bool(None))

    def test_safe_job_does_not_expose_payload_path(self):
        value = _safe_job({
            "job_id": "x", "source": "web", "source_peer": "alice", "printer_id": "p",
            "printer_name": "Printer", "status": "spooled", "retention": "ttl",
            "expires_at": int(time.time()) + 20, "payload_path": "secret/path.printenc",
            "content_type": "application/pdf", "payload_size": 10, "payload_sha256": "b" * 64,
            "policy_revision": "p", "created_at": int(time.time()), "completed_at": int(time.time()),
            "spool_reference": "private-spool-ref", "error": "",
        })
        self.assertNotIn("payload_path", value)
        self.assertNotIn("spool_reference", value)
        self.assertTrue(value["retained"])

    def test_safe_job_exposes_spool_reference_only_to_admin(self):
        row = {
            "job_id": "x", "source": "web", "source_peer": "alice", "printer_id": "p",
            "printer_name": "Printer", "status": "spooled", "retention": "no_store",
            "expires_at": 0, "payload_path": "", "content_type": "application/pdf",
            "payload_size": 10, "payload_sha256": "b" * 64, "policy_revision": "p",
            "created_at": int(time.time()), "completed_at": int(time.time()),
            "spool_reference": "spool-9", "error": "",
        }
        self.assertNotIn("spool_reference", _safe_job(row))
        self.assertEqual("spool-9", _safe_job(row, admin=True)["spool_reference"])

    def test_status_requires_login(self):
        response = app.test_client().get("/printershare/api/status")
        self.assertEqual(302, response.status_code)

    def test_status_returns_operational_summary(self):
        response = self.alice.get("/printershare/api/status")
        data = response.get_json()
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, data["schema"])
        self.assertIn("stats", data)
        self.assertEqual(4, data["stats"]["total"])
        self.assertFalse(data["admin"])

    def test_status_and_jobs_are_never_cached(self):
        for path in ("/printershare/api/status", "/printershare/api/jobs"):
            response = self.alice.get(path)
            self.assertEqual(200, response.status_code)
            self.assertIn("no-store", response.headers.get("Cache-Control", ""))
            self.assertEqual("nosniff", response.headers.get("X-Content-Type-Options"))
            self.assertTrue(response.headers.get("X-Request-ID"))

    def test_user_jobs_are_scoped_to_current_user(self):
        data = self.alice.get("/printershare/api/jobs").get_json()
        self.assertEqual(2, data["total"])
        self.assertEqual({"alice-ok", "alice-failed"}, {row["job_id"] for row in data["jobs"]})

    def test_admin_can_see_all_jobs(self):
        data = self.admin.get("/printershare/api/jobs?limit=200").get_json()
        self.assertEqual(4, data["total"])
        self.assertEqual(4, len(data["jobs"]))

    def test_job_filters_work(self):
        data = self.alice.get("/printershare/api/jobs?status=failed&retention=ttl").get_json()
        self.assertEqual(1, data["total"])
        self.assertEqual("alice-failed", data["jobs"][0]["job_id"])

    def test_admin_source_filter_work(self):
        data = self.admin.get("/printershare/api/jobs?source=federation").get_json()
        self.assertEqual(1, data["total"])
        self.assertEqual("remote-ok", data["jobs"][0]["job_id"])

    def test_job_search_matches_printer(self):
        data = self.alice.get("/printershare/api/jobs?q=Office%20Printer").get_json()
        self.assertEqual(2, data["total"])

    def test_user_cannot_fetch_another_users_job(self):
        self.assertEqual(404, self.alice.get("/printershare/api/jobs/bob-ok").status_code)
        self.assertEqual(200, self.admin.get("/printershare/api/jobs/bob-ok").status_code)

    def test_csv_export_is_user_scoped(self):
        response = self.alice.get("/printershare/api/jobs.csv?limit=200")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("alice-ok", body)
        self.assertNotIn("bob-ok", body)
        self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_settings_api_does_not_return_secrets(self):
        data = self.alice.get("/printershare/api/settings").get_json()
        self.assertIn("max_job_bytes", data)
        self.assertNotIn("SECRET_KEY", data)
        self.assertEqual(["no_store", "ttl", "permanent"], data["retention_modes"])

    def test_printers_api_has_stable_shape(self):
        response = self.alice.get("/printershare/api/printers")
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.get_json()["schema"])
        self.assertIn("printers", response.get_json())

    def test_health_api_has_storage_checks(self):
        data = self.alice.get("/printershare/api/health").get_json()
        self.assertIn("storage_directory", data["checks"])
        self.assertIn("database_exists", data["checks"])
        self.assertTrue(data["checks"]["database_exists"])

    def test_non_admin_cannot_run_maintenance(self):
        response = self.alice.post("/printershare/admin/maintenance?apply=1")
        self.assertEqual(403, response.status_code)

    def test_maintenance_dry_run_does_not_mutate_missing_reference(self):
        result = run_maintenance(self.store, apply=False)
        self.assertEqual(1, result["missing_references_cleared"])
        with sqlite3.connect(self.store.jobs_path) as db:
            path = db.execute("SELECT payload_path FROM print_job WHERE job_id='alice-failed'").fetchone()[0]
        self.assertTrue(path)

    def test_maintenance_apply_clears_missing_reference(self):
        result = run_maintenance(self.store, apply=True)
        self.assertEqual(1, result["missing_references_cleared"])
        with sqlite3.connect(self.store.jobs_path) as db:
            path = db.execute("SELECT payload_path FROM print_job WHERE job_id='alice-failed'").fetchone()[0]
        self.assertEqual("", path)


if __name__ == "__main__":
    unittest.main()
