import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, db
from app.master_cluster import MasterClusterSettings


class MasterClusterRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(TESTING=True, DATABASE=str(self.base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            db.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "admin", "password": "synthetic-cluster-password"})
        self.client.post("/auth/login", data={"username": "admin", "password": "synthetic-cluster-password"})
        self.route = "/admin/licensing/cluster-settings"
        self.settings = MasterClusterSettings(self.root)

    def test_admin_can_save_standard_txt_record_and_render_settings(self):
        values = {"mode": "backup-active", "txt_record_name": "_simpleoffice-master.simpleoffice4me.back2heaven.de"}
        self.assertEqual(302, self.client.post(self.route, data=values).status_code)
        self.assertEqual(values, self.settings.load())
        self.assertEqual(200, self.client.get(self.route).status_code)

    def test_corrupt_settings_render_repair_without_changing_saved_state(self):
        self.settings.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.path.write_text("{invalid")
        response = self.client.get(self.route)
        self.assertEqual(200, response.status_code)
        self.assertIn("ausdrücklich neu speichern", response.data.decode())
        self.assertEqual("{invalid", self.settings.path.read_text())

    def test_non_admin_and_anonymous_cannot_access_or_save_settings(self):
        with app.app_context():
            conn = db.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        for method in (self.client.get, self.client.post):
            self.assertEqual(403, method(self.route).status_code)
        self.assertEqual(403, self.client.get("/admin/licensing/cluster-status.json").status_code)
        self.assertEqual(302, app.test_client().get(self.route).status_code)
        self.assertFalse(self.settings.path.exists())

    def test_invalid_input_does_not_replace_saved_settings(self):
        values = {"mode": "backup-active", "txt_record_name": "_simpleoffice-master.example.test"}
        self.settings.save(values, "admin")
        self.assertEqual(302, self.client.post(self.route, data={"mode": "invalid", "txt_record_name": "bad..name"}).status_code)
        self.assertEqual(values, self.settings.load())

    def test_csrf_required_for_settings_mutation(self):
        with patch.dict(app.config, {"TEST_CSRF_PROTECTION": True}):
            self.assertEqual(200, self.client.get(self.route).status_code)
            values = {"mode": "backup-active", "txt_record_name": "_simpleoffice-master.example.test"}
            self.assertEqual(403, self.client.post(self.route, data=values).status_code)
            self.assertFalse(self.settings.path.exists())
            with self.client.session_transaction() as session:
                values["_csrf_token"] = session["_csrf_token"]
            self.assertEqual(302, self.client.post(self.route, data=values).status_code)

    def test_probe_failure_does_not_stop_next_worker_cycle(self):
        from app import federation_discovery_runtime as runtime
        calls = 0
        def probe(root):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("synthetic probe failure")
            raise KeyboardInterrupt()
        with patch.object(runtime, "LICENSE_MASTER_MODE", True), patch.object(runtime, "configured_country", return_value=""), patch.object(runtime, "record_master_address_status", side_effect=probe), patch.object(runtime.time, "sleep"):
            with self.assertRaises(KeyboardInterrupt):
                runtime._worker(app)
        self.assertEqual(2, calls)
        self.assertEqual("standby_probe_failed", app.extensions["simpleoffice_master_address_status"]["operating_state"])
        app.extensions.pop("simpleoffice_master_address_status", None)
