import csv
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.datalogger_collectors import (
    CollectionError,
    collect_file,
    collect_http,
    collect_linux,
    json_path,
    normalize_sensor_allowed_hosts,
    validate_sensor_allowed_hosts,
)
from app.datalogger_store import DataLoggerStore
from app.settings_store import SettingsStore
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

    def test_samples_identify_manual_and_automatic_sources(self):
        channel = self.store.create_channel("Temperatur", "alice", unit="°C")
        self.store.add_sample(channel, 20.0, "alice")
        source = self.store.add_source(channel, "alice", "http_json", "Kellerfühler", {
            "url": "http://sensor.invalid/status", "json_path": "temperature.value",
        })
        self.store.add_sample(channel, 21.5, "worker", source_id=source)
        samples = self.store.samples(channel)
        manual = next(item for item in samples if item["source_kind"] == "manual")
        automatic = next(item for item in samples if item["source_kind"] == "http_json")
        self.assertEqual(("Manuelle Eingabe", "manual"), (manual["source_name"], manual["source_kind"]))
        self.assertEqual(("Kellerfühler", "http_json", "temperature.value"), (
            automatic["source_name"], automatic["source_kind"], automatic["source_metric"],
        ))

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
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(CollectionError, "host_denied"):
                collect_http({"url": "https://example.com/value", "json_path": "value"}, root)

    @patch("app.datalogger_collectors.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 80))])
    @patch("app.datalogger_collectors.build_opener")
    def test_http_reads_bounded_json_and_extracts_number(self, opener, _dns):
        response = MagicMock(); response.read.return_value = b'{"room":{"temperature":22.4}}'
        headers = Message(); headers["Content-Type"] = "application/json"; response.headers = headers
        opener.return_value.open.return_value = response
        with tempfile.TemporaryDirectory() as root:
            SettingsStore(root).save({"datalogger": {"allowed_hosts": ["sensor.local"]}}, "test")
            self.assertEqual(22.4, collect_http({"url": "http://sensor.local/status", "json_path": "room.temperature"}, root))

    @patch("app.datalogger_collectors.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 80))])
    @patch("app.datalogger_collectors.build_opener")
    def test_http_sends_stored_header_secret_not_environment(self, opener, _dns):
        response = MagicMock(); response.read.return_value = b'{"value": 3}'
        headers = Message(); headers["Content-Type"] = "application/json"; response.headers = headers
        opener.return_value.open.return_value = response
        with tempfile.TemporaryDirectory() as root:
            SettingsStore(root).save({"datalogger": {"allowed_hosts": ["sensor.local"]}}, "test")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(3, collect_http({"url": "http://sensor.local/status", "header_secret": "tok-1"}, root))
        sent = opener.return_value.open.call_args[0][0]
        self.assertEqual("tok-1", sent.get_header("Authorization"))

    def test_sensor_allowed_hosts_normalize_and_validate(self):
        self.assertEqual(["localhost", "127.0.0.1", "::1"], normalize_sensor_allowed_hosts(["LOCALHOST ", "127.0.0.1", "::1", 7, ""]))
        self.assertEqual(["localhost"], normalize_sensor_allowed_hosts([""]))
        self.assertEqual(["localhost", "127.0.0.1", "::1"], normalize_sensor_allowed_hosts("not-a-list"))
        with self.assertRaises(ValueError):
            validate_sensor_allowed_hosts([])
        self.assertEqual(["sensor.local"], validate_sensor_allowed_hosts(["Sensor.Local "]))

    def test_linux_load_collector_is_numeric(self):
        with patch("app.datalogger_collectors.os.getloadavg", return_value=(0.5, 0.25, 0.1), create=True):
            self.assertIsInstance(collect_linux({"metric": "load1"}), float)

    def test_linux_explicit_free_and_total_storage(self):
        with tempfile.TemporaryDirectory() as root:
            free = collect_linux({"metric": "disk_free_bytes", "path": root})
            total = collect_linux({"metric": "disk_total_bytes", "path": root})
        self.assertGreater(total, 0)
        self.assertGreaterEqual(free, 0)
        self.assertLessEqual(free, total)

    def test_linux_proc_uptime_reports_real_seconds(self):
        if not Path("/proc/uptime").is_file():
            self.skipTest("Linux procfs not available")
        self.assertGreater(collect_linux({"metric": "uptime_seconds"}), 0)

    def test_worker_records_success_without_flask(self):
        with tempfile.TemporaryDirectory() as root:
            store = DataLoggerStore(root); channel = store.create_channel("Load", "alice")
            store.add_source(channel, "alice", "linux", "Load", {"metric": "load1"}, 10)
            with patch("app.datalogger_collectors.os.getloadavg", return_value=(0.5, 0.25, 0.1), create=True):
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
        self.temp = tempfile.TemporaryDirectory(); self.saved = {k: app.config.get(k) for k in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
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

    def test_local_android_motion_requires_explicit_save_and_matching_editable_channel(self):
        store = DataLoggerStore(app.config["DOCUMENT_ROOT"])
        motion = store.create_channel("Eigene Bewegung", "owner", unit="m/s²", readers=["reader"])
        wrong = store.create_channel("Andere Einheit", "owner", unit="rad/s")
        url = "/datalogger/android-motion"
        headers = {"User-Agent": "Synthetic SimpleOffice4Me-Android/test"}
        self.assertIn(url, self.owner.get("/datalogger/", headers=headers).get_data(as_text=True))
        self.assertNotIn(url, self.owner.get("/datalogger/").get_data(as_text=True))
        self.assertEqual(403, self.owner.get(url, headers={**headers, "X-Forwarded-For": "192.0.2.9"}).status_code)
        page = self.owner.get(url)
        self.assertEqual("private, no-store", page.headers["Cache-Control"])
        options = BeautifulSoup(page.data, "html.parser").select("[data-motion-save] option[data-unit]")
        self.assertEqual({motion, wrong}, {option["value"] for option in options})
        self.assertNotIn(motion, self.reader.get(url).get_data(as_text=True))
        self.assertEqual([], store.samples(motion))
        body = {"channel_id": motion, "kind": "acceleration", "value": "13"}
        self.assertEqual(403, self.reader.post(url, data=body).status_code)
        self.assertEqual(422, self.owner.post(url, data={**body, "channel_id": wrong}).status_code)
        for invalid in ("nan", "inf", "-1", "2001"):
            self.assertEqual(422, self.owner.post(url, data={**body, "value": invalid}).status_code)
        self.assertEqual(400, self.owner.post(url, data={**body, "latitude": "1"}).status_code)
        self.assertEqual(400, self.owner.post(url, data={**body, "value": ["13", "14"]}).status_code)
        self.assertEqual([], store.samples(motion))
        self.assertEqual(303, self.owner.post(url, data=body).status_code)
        saved, = DataLoggerStore(app.config["DOCUMENT_ROOT"]).samples(motion)
        self.assertEqual(13.0, saved["value"])
        self.assertEqual("manual", saved["source_kind"])
        self.assertEqual({"observation": "acceleration"}, saved["metadata"])

    def test_android_motion_save_uses_existing_csrf_and_current_unit(self):
        store = DataLoggerStore(app.config["DOCUMENT_ROOT"])
        motion = store.create_channel("Drehung", "owner", unit="rad/s", editors=["reader"])
        url = "/datalogger/android-motion"
        body = {"channel_id": motion, "kind": "angular_speed", "value": "0"}
        app.config["TEST_CSRF_PROTECTION"] = True
        token = BeautifulSoup(self.reader.get(url).data, "html.parser").select_one("[data-motion-save] [name=_csrf_token]")["value"]
        self.assertEqual(403, self.reader.post(url, data=body).status_code)
        preflight = url + "/authorize"
        self.assertEqual(403, self.reader.post(preflight).status_code)
        auth = {"X-CSRF-Token": token}
        self.assertEqual(403, self.reader.post(preflight, headers={**auth, "X-Forwarded-For": "192.0.2.9"}).status_code)
        self.assertEqual(400, self.reader.post(preflight, headers=auth, data={"sample": "0"}).status_code)
        self.assertEqual(204, self.reader.post(preflight, headers=auth).status_code)
        self.assertEqual([], store.samples(motion))
        store.update_channel(motion, "owner", unit="m/s²", editors=["reader"])
        self.assertEqual(422, self.reader.post(url, data={**body, "_csrf_token": token}).status_code)
        self.assertEqual([], store.samples(motion))
        store.update_channel(motion, "owner", unit="rad/s", editors=["reader"])
        self.assertEqual(303, self.reader.post(url, data={**body, "_csrf_token": token}).status_code)
        self.assertEqual(0, store.samples(motion)[0]["value"])

    def test_unshared_channel_is_not_disclosed(self):
        response = self.owner.post("/datalogger/channels", data={"name": "Privat"})
        channel = response.headers["Location"].rsplit("/", 1)[-1]
        self.assertEqual(404, self.reader.get(f"/datalogger/channels/{channel}").status_code)

    def test_availability_event_history_uses_the_channel_acl(self):
        store = DataLoggerStore(app.config["DOCUMENT_ROOT"])
        channel = store.create_channel("Private sensor", "owner")
        form = {"kind": "http_json", "source_name": "Synthetic Pico", "json_path": "nodes.room_a.available",
                "track_availability": "1"}
        saved = self.owner.post(f"/datalogger/channels/{channel}/sources", data=form)
        self.assertEqual(302, saved.status_code)
        source = store.sources(channel)[0]["source_id"]
        with patch("app.datalogger_store.utc_now", return_value="2026-11-27T00:00:01+00:00"):
            store.add_pico_availability_sample(channel, source, 1)
        with patch("app.datalogger_store.utc_now", return_value="2026-11-27T00:00:02+00:00"):
            store.add_pico_availability_sample(channel, source, 0)
        url = f"/datalogger/channels/{channel}"
        self.assertEqual(404, self.reader.get(url).status_code)
        page = BeautifulSoup(self.owner.get(url).data, "html.parser")
        history = page.select_one('[aria-labelledby="pico-availability-title"]')
        self.assertIn("Nicht erreichbar", history.get_text())
        self.assertIn("Synthetic Pico", history.get_text())
        store.update_channel(channel, "owner", readers=["reader"])
        shared = self.reader.get(url)
        self.assertEqual(200, shared.status_code)
        self.assertIn(b"Nicht erreichbar", shared.data)

    def test_diagnosis_same_range_in_ui_and_exports(self):
        store = DataLoggerStore(app.config['DOCUMENT_ROOT'])
        channel_id = store.create_channel('Speicher frei', 'owner', unit='B')
        store.add_source(channel_id, 'owner', 'linux', 'Lokaler Datenordner',
                         {'metric': 'disk_free_bytes', 'path': self.temp.name}, 60)
        failed = store.add_source(channel_id, 'owner', 'linux', 'Getrenntes Laufwerk',
                                  {'metric': 'disk_free_bytes', 'path': f'{self.temp.name}/not-mounted'}, 60)
        store.add_sample(channel_id, 999, 'owner', measured_at='2024-02-29T23:30:00+00:00')
        run(app.config['DOCUMENT_ROOT'], once=True)
        self.assertEqual(next(row for row in store.sources(channel_id) if row['source_id'] == failed)['last_status'], 'error')
        today = datetime.now(timezone.utc).date().isoformat()
        query = f'?start_day={today}&end_day={today}'
        base = f'/datalogger/channels/{channel_id}'
        view = self.owner.get(base + query)
        self.assertEqual(view.status_code, 200)
        self.assertIn('disk_unavailable', view.get_data(as_text=True))
        self.assertIn('Messwerte als CSV', view.get_data(as_text=True))
        self.assertIn('Verlauf vor und nach dem Fehler', view.get_data(as_text=True))
        self.assertIn('start_day=' + today, view.get_data(as_text=True))
        self.assertNotIn('>999.0 B<', view.get_data(as_text=True))
        response = self.owner.get(base + '/report.csv' + query)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
        row, = [entry for entry in rows if entry['status'] == 'good']
        error, = [entry for entry in rows if entry['status'] == 'error']
        self.assertEqual(error['value'], '')
        self.assertEqual(error['error_code'], 'disk_unavailable')
        self.assertEqual(row['source'], 'Lokaler Datenordner')
        self.assertEqual(row['unit'], 'B')
        self.assertEqual(row['status'], 'good')
        self.assertGreaterEqual(float(row['value']), 0)
        self.assertIn('gültig: 1', self.owner.get(base + '/report.txt' + query).get_data(as_text=True))
        payload = self.owner.get(base + '/data.json' + query).json
        self.assertEqual(len(payload['samples']), 1)
        self.assertEqual(payload['collection_errors'][0]['error_code'], 'disk_unavailable')
        self.assertIsNone(payload['collection_errors'][0]['value'])
        response = self.owner.get(base + '/report.pdf' + query)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertTrue(response.data.startswith(b'%PDF-'))
        self.assertEqual(self.reader.get(base + '/report.csv' + query).status_code, 404)
        self.assertEqual(self.owner.get(base + '/report.csv?start_day=yesterday').status_code, 400)

    def test_logger_control_groups_have_names_on_rendered_pages(self):
        with patch("app.datalogger.platform_kind", return_value="linux"):
            overview = self.owner.get("/datalogger/")
        self.assertEqual(200, overview.status_code)
        overview_html = BeautifulSoup(overview.data, "html.parser")
        presets = overview_html.select_one('[role="group"][aria-label="Server-Messvorlagen"]')
        self.assertIsNotNone(presets)
        self.assertIsNotNone(presets.select_one('form[action$="/uptime_seconds"] button[type="submit"]'))

        created = self.owner.post("/datalogger/channels", data={"name": "Synthetic report channel"})
        detail = self.owner.get(created.headers["Location"])
        self.assertEqual(200, detail.status_code)
        detail_html = BeautifulSoup(detail.data, "html.parser")
        exports = detail_html.select_one('[role="group"][aria-label="Export für den angezeigten Zeitraum"]')
        self.assertIsNotNone(exports)
        self.assertEqual(
            ["report.csv", "report.txt", "report.pdf", "data.json"],
            [link["href"].split("?", 1)[0].rsplit("/", 1)[-1] for link in exports.select("a[href]")],
        )

        source_form = detail_html.select_one('form[action$="/sources"]')
        for group_id, fields in (
            ("source-measurement-heading", {"metric": "Messgröße", "json_path": "JSON-Pfad"}),
            ("source-destination-heading", {"path": "Pfad", "url": "URL"}),
        ):
            group = source_form.select_one(f'[role="group"][aria-labelledby="{group_id}"]')
            self.assertIsNotNone(group)
            self.assertIsNotNone(group.find(id=group_id))
            for name, label_text in fields.items():
                inputs = group.select(f'input[name="{name}"]')
                self.assertEqual(1, len(inputs))
                labels = group.find_all("label", attrs={"for": inputs[0]["id"]})
                self.assertEqual([label_text], [label.get_text(strip=True) for label in labels])

    def test_server_preset_is_visible_private_and_idempotent(self):
        url = '/datalogger/server-presets/uptime_seconds'
        with patch('app.datalogger.platform_kind', return_value='linux'):
            self.assertIn(url, self.owner.get('/datalogger/').get_data(as_text=True))
            self.assertNotIn(url, self.reader.get('/datalogger/').get_data(as_text=True))
            self.assertEqual(self.reader.post(url).status_code, 403)
            app.config['TEST_CSRF_PROTECTION'] = True
            with self.owner.session_transaction() as session:
                session['_csrf_token'] = 't' * 40
            self.assertEqual(self.owner.post(url).status_code, 403)
            headers = {'X-CSRF-Token': 't' * 40}
            first = self.owner.post(url, headers=headers)
            self.assertEqual(first.status_code, 302)
            self.assertEqual(first.location, self.owner.post(url, headers=headers).location)
        with patch('app.datalogger.platform_kind', return_value='android'):
            self.assertEqual(self.owner.post(url, headers=headers).status_code, 404)
        store = DataLoggerStore(app.config['DOCUMENT_ROOT'])
        channel_id = first.location.rsplit('/', 1)[-1]
        self.assertEqual(len(store.sources(channel_id)), 1)
        self.assertEqual(store.channel(channel_id)['unit'], 's')
        self.assertFalse(store.can_read(store.channel(channel_id), 'reader'))

    def test_poll_interval_settings_are_admin_only_csrf_protected_and_persistent(self):
        url = "/datalogger/settings"
        self.assertEqual(200, self.owner.get(url).status_code)
        self.assertEqual(403, self.reader.get(url).status_code)
        app.config["TEST_CSRF_PROTECTION"] = True
        with self.owner.session_transaction() as session:
            session["_csrf_token"] = "s" * 40
        self.assertEqual(403, self.owner.post(url, data={"poll_interval_seconds": "12"}).status_code)
        headers = {"X-CSRF-Token": "s" * 40}
        self.assertEqual(
            403,
            self.reader.post(url, data={"poll_interval_seconds": "12"}, headers=headers).status_code,
        )
        self.assertEqual(
            302,
            self.owner.post(
                url,
                data={"poll_interval_seconds": "12", "allowed_hosts": "sensor.local"},
                headers=headers,
            ).status_code,
        )
        store = DataLoggerStore(app.config["DOCUMENT_ROOT"])
        self.assertEqual(12, store.poll_interval_seconds())
        self.assertEqual(
            ["sensor.local"],
            SettingsStore(app.config["DOCUMENT_ROOT"]).settings()["datalogger"]["allowed_hosts"],
        )
        self.assertEqual(
            400,
            self.owner.post(url, data={"poll_interval_seconds": "61", "allowed_hosts": "sensor.local"}, headers=headers).status_code,
        )
        self.assertEqual(12, store.poll_interval_seconds())
        self.assertEqual(
            400,
            self.owner.post(url, data={"poll_interval_seconds": "12", "allowed_hosts": ""}, headers=headers).status_code,
        )
        self.assertEqual(
            ["sensor.local"],
            SettingsStore(app.config["DOCUMENT_ROOT"]).settings()["datalogger"]["allowed_hosts"],
        )
        self.assertEqual(302, self.owner.post(url, data={"reset": "1"}, headers=headers).status_code)
        self.assertEqual(5, store.poll_interval_seconds())
        self.assertEqual(
            ["localhost", "127.0.0.1", "::1"],
            SettingsStore(app.config["DOCUMENT_ROOT"]).settings()["datalogger"]["allowed_hosts"],
        )


if __name__ == "__main__": unittest.main()
