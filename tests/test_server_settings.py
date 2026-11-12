"""Server bind + background workers from the settings file, env as override."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simpleoffice_server_config import (
    DEFAULT_SERVER_SETTINGS,
    effective_server_settings,
    normalize_server_settings,
    strict_server_settings,
    validate_server_settings,
)
from tools.launcher import (
    background_index_enabled,
    datalogger_enabled,
    osm_index_enabled,
    start_mini_worker,
    waitress_options,
)


def _write_server_section(root: Path, section: dict) -> None:
    meta = root / ".simpleoffice-meta"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "settings.json").write_text(
        json.dumps({"server": section}), encoding="utf-8",
    )


class ServerConfigUnitTests(unittest.TestCase):
    def test_normalize_falls_back_to_defaults(self):
        result = normalize_server_settings({"port": "abc", "wsgi_threads": True})
        self.assertEqual(8080, result["port"])
        self.assertEqual(4, result["wsgi_threads"])
        self.assertEqual("127.0.0.1", result["host"])

    def test_validate_rejects_garbage(self):
        for values in (
            {"host": ""},
            {"host": "has space"},
            {"port": 0},
            {"port": 70000},
            {"wsgi_threads": 0},
            {"wsgi_channel_timeout": 5},
            {"trusted_proxy_hops": 17},
            {"container_role": "master"},
            {"background_index_enabled": "yes"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_server_settings(values)

    def test_validate_accepts_full_section(self):
        section = dict(DEFAULT_SERVER_SETTINGS)
        section.update({"host": "0.0.0.0", "port": 9090, "container_role": "web"})
        self.assertEqual("0.0.0.0", validate_server_settings(section)["host"])

    def test_effective_prefers_env_over_stored_over_default(self):
        stored = {"port": 9090, "wsgi_threads": 8}
        with patch.dict(os.environ, {}, clear=True):
            merged = effective_server_settings(stored=stored)
            self.assertEqual(9090, merged["port"])
            self.assertEqual(8, merged["wsgi_threads"])
            self.assertEqual("127.0.0.1", merged["host"])
        with patch.dict(os.environ, {"SIMPLEOFFICE_PORT": "9191"}, clear=True):
            merged = effective_server_settings(stored=stored)
            self.assertEqual(9191, merged["port"])
            self.assertEqual(8, merged["wsgi_threads"])

    def test_strict_raises_on_invalid_env(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_PORT": "0"}, clear=True):
            with self.assertRaises(ValueError):
                strict_server_settings(stored={})


class LauncherServerTests(unittest.TestCase):
    def test_stored_section_wins_over_first_start_config(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            _write_server_section(Path(temp), {"host": "0.0.0.0", "port": 9090})
            options = waitress_options({"host": "127.0.0.1", "port": 8080}, 1024, temp)
        self.assertEqual("0.0.0.0", options["host"])
        self.assertEqual(9090, options["port"])

    def test_env_override_wins_over_stored_section(self):
        environment = {"SIMPLEOFFICE_PORT": "9191", "SIMPLEOFFICE_WSGI_THREADS": "8"}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, environment, clear=True):
            _write_server_section(Path(temp), {"port": 9090, "wsgi_threads": 2})
            options = waitress_options({"host": "127.0.0.1", "port": 8080}, 1024, temp)
        self.assertEqual(9191, options["port"])
        self.assertEqual(8, options["threads"])

    def test_invalid_stored_section_stops_start(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            _write_server_section(Path(temp), {"port": 0})
            with self.assertRaises(RuntimeError):
                waitress_options({"host": "127.0.0.1", "port": 8080}, 1024, temp)

    def test_worker_toggles_follow_stored_section(self):
        section = {"background_index_enabled": False, "osm_index_enabled": False, "datalogger_enabled": False}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            _write_server_section(Path(temp), section)
            self.assertFalse(background_index_enabled(temp))
            self.assertFalse(osm_index_enabled(temp))
            self.assertFalse(datalogger_enabled(temp))
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"SIMPLEOFFICE_BACKGROUND_INDEX": "1"}, clear=True,
        ):
            _write_server_section(Path(temp), section)
            self.assertTrue(background_index_enabled(temp))
            self.assertFalse(osm_index_enabled(temp))

    def test_mini_autostart_follows_store_and_role(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {}, clear=True), patch(
                "simpleoffice_mini_core.read_status", return_value={},
            ), patch("tools.launcher.subprocess.Popen") as popen:
                _write_server_section(Path(temp), {})
                self.assertIs(start_mini_worker(temp), popen.return_value)
            with patch.dict(os.environ, {}, clear=True), patch(
                "tools.launcher.subprocess.Popen",
            ) as popen:
                _write_server_section(
                    Path(temp),
                    {"mini_services_autostart": False, "container_role": ""},
                )
                self.assertIsNone(start_mini_worker(temp))
                popen.assert_not_called()
            with patch.dict(os.environ, {}, clear=True), patch(
                "tools.launcher.subprocess.Popen",
            ) as popen:
                _write_server_section(Path(temp), {"container_role": "error-relay"})
                self.assertIsNone(start_mini_worker(temp))
                popen.assert_not_called()


class ServerStoreTests(unittest.TestCase):
    def test_defaults_without_file_or_env(self):
        from app.settings_store import SettingsStore

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            # Sanitise the ambient developer machine: no override may leak in.
            for name in (
                "SIMPLEOFFICE_HOST", "SIMPLEOFFICE_PORT", "SIMPLEOFFICE_WSGI_THREADS",
                "SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT", "SIMPLEOFFICE_TRUSTED_PROXY_HOPS",
                "SIMPLEOFFICE_BACKGROUND_INDEX", "SIMPLEOFFICE_OSM_INDEX",
                "SIMPLEOFFICE_DATALOGGER", "SIMPLEOFFICE_MINI_SERVICES_AUTOSTART",
                "SIMPLEOFFICE_CONTAINER_ROLE",
            ):
                os.environ.pop(name, None)
            server = SettingsStore(Path(temp)).settings()["server"]
        self.assertEqual(dict(DEFAULT_SERVER_SETTINGS), server)

    def test_save_roundtrip_and_strict_validation(self):
        from app.settings_store import SettingsStore

        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            store = SettingsStore(Path(temp))
            store.save(
                {"server": {
                    "host": "0.0.0.0", "port": 9090, "wsgi_threads": 8,
                    "wsgi_channel_timeout": 300, "trusted_proxy_hops": 1,
                    "background_index_enabled": False, "osm_index_enabled": False,
                    "datalogger_enabled": False, "mini_services_autostart": False,
                    "container_role": "web",
                }},
                "server-test",
            )
            server = store.settings()["server"]
            self.assertEqual("0.0.0.0", server["host"])
            self.assertEqual(9090, server["port"])
            self.assertFalse(server["background_index_enabled"])
            self.assertEqual("web", server["container_role"])
            with self.assertRaises(ValueError):
                store.save({"server": {"port": 0}}, "server-test")


class ServerAdminTests(unittest.TestCase):
    def setUp(self):
        from app import app, db as database

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        self.addCleanup(lambda: app.config.update(saved))
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post(
            "/auth/register",
            data={"username": "admin", "password": "synthetic-server-password"},
        )
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": "synthetic-server-password"},
        )

    def _settings(self):
        from app.settings_store import SettingsStore

        return SettingsStore(self.root).settings()["server"]

    def test_admin_page_renders_both_languages(self):
        page = self.client.get("/admin/settings/server")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("Bind address", body)
        self.assertIn('id="server-host"', body)
        self.client.post("/auth/language", data={"language": "de"})
        page = self.client.get("/admin/settings/server")
        self.assertEqual(200, page.status_code)
        body = page.get_data(as_text=True)
        self.assertIn("Bind-Adresse", body)
        self.assertNotIn("Bind address", body)
        self.assertIn("LAN-Proxy aktivieren und automatisch starten", body)
        self.assertIn("Proxy starten", body)

    def test_admin_can_save_reset_and_is_validated(self):
        response = self.client.post(
            "/admin/settings/server",
            data={
                "host": "0.0.0.0", "port": "9090", "wsgi_threads": "8",
                "wsgi_channel_timeout": "300", "trusted_proxy_hops": "1",
                "background_index_enabled": "1", "osm_index_enabled": "",
                "datalogger_enabled": "1", "mini_services_autostart": "",
                "container_role": "web",
            },
        )
        self.assertEqual(302, response.status_code)
        server = self._settings()
        self.assertEqual("0.0.0.0", server["host"])
        self.assertEqual(9090, server["port"])
        self.assertTrue(server["background_index_enabled"])
        self.assertFalse(server["osm_index_enabled"])
        rejected = self.client.post("/admin/settings/server", data={"port": "0"})
        self.assertEqual(302, rejected.status_code)
        self.assertEqual(9090, self._settings()["port"])
        reset = self.client.post("/admin/settings/server", data={"reset": "1"})
        self.assertEqual(302, reset.status_code)
        self.assertEqual(dict(DEFAULT_SERVER_SETTINGS), self._settings())

    def test_non_admin_cannot_save_server_settings(self):
        from app import app, db as database

        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        response = self.client.post("/admin/settings/server", data={"port": "9090"})
        self.assertEqual(403, response.status_code)

    def test_admin_can_start_and_stop_lan_proxy_without_restarting_web_server(self):
        from app.lan_proxy import lan_proxy_runtime
        from app.settings_store import SettingsStore

        SettingsStore(self.root).save({"lan_proxy": {
            "enabled": False,
            "bind_host": "192.168.1.42",
            "port": 8443,
            "tls_cert": "/tmp/synthetic-certificate.pem",
            "tls_key": "/tmp/synthetic-private-key.pem",
        }}, "test")
        with patch("app.admin_server_settings._apply_lan_proxy", return_value={
            "state": "running", "message": "running", "origin": "https://192.168.1.42:8443",
        }) as start_proxy:
            started = self.client.post(
                "/admin/settings/server/lan-proxy", data={"action": "start"},
            )
        self.assertEqual(302, started.status_code)
        self.assertTrue(SettingsStore(self.root).settings()["lan_proxy"]["enabled"])
        start_proxy.assert_called_once()

        with patch.object(lan_proxy_runtime, "stop", return_value={
            "state": "disabled", "message": "stopped", "origin": "",
        }) as stop_proxy:
            stopped = self.client.post(
                "/admin/settings/server/lan-proxy", data={"action": "stop"},
            )
        self.assertEqual(302, stopped.status_code)
        self.assertFalse(SettingsStore(self.root).settings()["lan_proxy"]["enabled"])
        stop_proxy.assert_called_once()

    def test_non_admin_cannot_control_lan_proxy(self):
        from app import app, db as database

        with app.app_context():
            conn = database.get_db()
            conn.execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            conn.commit()
        response = self.client.post(
            "/admin/settings/server/lan-proxy", data={"action": "start"},
        )
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
