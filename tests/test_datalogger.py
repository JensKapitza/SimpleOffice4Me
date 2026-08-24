import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.datalogger_collectors import CollectionError, collect_file, collect_http, collect_linux, json_path
from app.datalogger_store import DataLoggerStore
from tools.datalogger_worker import acquire_lock, run
from tools.launcher import datalogger_enabled, start_datalogger_worker


class DataLoggerStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.store = DataLoggerStore(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def test_persists_channels_samples_and_audit(self):
        channel = self.store.create_channel("Temperatur", "alice", unit="°C", readers=["bob"])
        self.store.add_sample(channel, 21.5, "alice")
        reopened = DataLoggerStore(self.temp.name)
        self.assertEqual(21.5, reopened.samples(channel)[0]["value"])
        self.assertTrue(reopened.can_read(reopened.channel(channel), "bob"))
        with reopened.connect() as db:
            self.assertEqual(2, db.execute("SELECT COUNT(*) FROM metric_event").fetchone()[0])

    def test_reader_cannot_write_but_editor_can(self):
        channel = self.store.create_channel("X", "alice", readers=["reader"], editors=["editor"])
        with self.assertRaises(PermissionError): self.store.add_sample(channel, 1, "reader")
        self.store.add_sample(channel, 2, "editor")
        self.assertEqual(2, self.store.samples(channel)[0]["value"])

    def test_non_finite_samples_are_rejected(self):
        channel = self.store.create_channel("X", "alice")
        for value in ("nan", "inf"):
            with self.assertRaises(ValueError): self.store.add_sample(channel, value, "alice")

    def test_source_intervals_are_bounded_and_disable_is_non_destructive(self):
        channel = self.store.create_channel("X", "alice")
        source = self.store.add_source(channel, "alice", "linux", "Load", {"metric": "load1"}, 1)
        self.assertEqual(10, self.store.sources(channel)[0]["interval_seconds"])
        self.store.set_source_enabled(source, "alice", False)
        self.assertEqual(0, self.store.sources(channel)[0]["enabled"])


class CollectorTest(unittest.TestCase):
    def test_json_path_handles_objects_and_arrays_without_eval(self):
        self.assertEqual(8, json_path({"sensors": [{"value": 8}]}, "sensors[0].value"))
        with self.assertRaises(CollectionError): json_path({}, "missing.value")

    def test_file_metrics_are_bounded_to_document_root(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "a.txt").write_text("abc")
            Path(root, "sub").mkdir(); Path(root, "sub", "b.txt").write_text("12345")
            self.assertEqual(2, collect_file(root, {"path": ".", "metric": "count", "recursive": True}))
            self.assertEqual(8, collect_file(root, {"path": ".", "metric": "total_bytes", "recursive": True}))
            with self.assertRaises(CollectionError): collect_file(root, {"path": "../outside", "metric": "count"})

    def test_file_entry_limit_prevents_unbounded_walk(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "a").write_text("a"); Path(root, "b").write_text("b")
            with self.assertRaisesRegex(CollectionError, "entry_limit"):
                collect_file(root, {"metric": "count", "max_entries": 1})

    def test_http_denies_hosts_unless_explicitly_allowed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(CollectionError, "host_denied"):
                collect_http({"url": "https://example.com/value", "json_path": "value"})

    @patch("app.datalogger_collectors.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 80))])
    @patch("app.datalogger_collectors.build_opener")
    def test_http_reads_bounded_json_and_extracts_number(self, opener, _dns):
        response = MagicMock(); response.read.return_value = b'{"room":{"temperature":22.4}}'
        headers = Message(); headers["Content-Type"] = "application/json"; response.headers = headers
        opener.return_value.open.return_value = response
        with patch.dict(os.environ, {"SIMPLEOFFICE_SENSOR_ALLOWED_HOSTS": "sensor.local"}):
            self.assertEqual(22.4, collect_http({"url": "http://sensor.local/status", "json_path": "room.temperature"}))

    def test_linux_load_collector_is_numeric(self):
        self.assertIsInstance(collect_linux({"metric": "load1"}), float)

    def test_worker_records_success_without_flask(self):
        with tempfile.TemporaryDirectory() as root:
            store = DataLoggerStore(root); channel = store.create_channel("Load", "alice")
            store.add_source(channel, "alice", "linux", "Load", {"metric": "load1"}, 10)
            run(root, once=True)
            self.assertEqual(1, len(store.samples(channel)))
            self.assertEqual("ok", store.sources(channel)[0]["last_status"])

    def test_worker_records_stable_error_code_not_exception_detail(self):
        with tempfile.TemporaryDirectory() as root:
            store = DataLoggerStore(root); channel = store.create_channel("HTTP", "alice")
            store.add_source(channel, "alice", "http_json", "Denied", {"url": "http://example.com", "json_path": "x"}, 10)
            run(root, once=True)
            self.assertEqual("host_denied", store.sources(channel)[0]["last_error_code"])

    def test_launcher_can_disable_background_service(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_DATALOGGER": "0"}):
            self.assertFalse(datalogger_enabled()); self.assertIsNone(start_datalogger_worker("/tmp"))

    def test_only_one_worker_lock_is_granted(self):
        with tempfile.TemporaryDirectory() as root:
            first = acquire_lock(root)
            try: self.assertIsNone(acquire_lock(root))
            finally: first.close()


class DataLoggerWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.saved = {k: app.config.get(k) for k in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "auth.sqlite"), DOCUMENT_ROOT=str(Path(self.temp.name) / "docs"))
        with app.app_context():
            database.ensure_auth_database(); db = database.get_db()
            for name, admin in (("owner", 1), ("reader", 0)):
                db.execute("INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (name, generate_password_hash(name + "-password"), admin))
            db.commit()
        self.owner = app.test_client(); self.reader = app.test_client()
        self.owner.post("/auth/login", data={"username": "owner", "password": "owner-password"})
        self.reader.post("/auth/login", data={"username": "reader", "password": "reader-password"})

    def tearDown(self): app.config.update(self.saved); self.temp.cleanup()

    def test_ui_creation_export_and_feature_gate(self):
        response = self.owner.post("/datalogger/channels", data={"name": "CPU", "unit": "%"})
        channel = response.headers["Location"].rsplit("/", 1)[-1]
        self.owner.post(f"/datalogger/channels/{channel}/samples", data={"value": "12.5"})
        exported = self.owner.get(f"/datalogger/channels/{channel}/data.json")
        self.assertEqual(12.5, exported.json["samples"][0]["value"])
        with app.app_context():
            db = database.get_db(); uid = db.execute("SELECT id FROM user WHERE username='reader'").fetchone()[0]
            db.execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at) VALUES(?,?,0,CURRENT_TIMESTAMP)", (uid, "datalogger")); db.commit()
        self.assertEqual(403, self.reader.get("/datalogger/").status_code)

    def test_unshared_channel_is_not_disclosed(self):
        response = self.owner.post("/datalogger/channels", data={"name": "Privat"})
        channel = response.headers["Location"].rsplit("/", 1)[-1]
        self.assertEqual(404, self.reader.get(f"/datalogger/channels/{channel}").status_code)


if __name__ == "__main__": unittest.main()
