"""Federation network operating flags in the disk store + admin UI (no environment)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, app_config_store, db as database
from app import federation_network as network


class FederationNetworkStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        patcher = patch.object(
            app_config_store, "settings_path", return_value=self.root / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_are_empty_and_opt_out(self):
        self.assertEqual([], network.lan_addresses())
        self.assertEqual([], network.lan_ports())
        self.assertEqual([], network.bootstrap_raw())
        self.assertFalse(network.allow_private_targets())
        self.assertFalse(network.allow_loopback())
        self.assertFalse(network.refuse_bad_client())

    def test_lists_roundtrip(self):
        network.save({
            "lan_addresses": "192.168.1.7, 10.0.0.9",
            "lan_ports": "9090,abc,99999,0,9090",
            "bootstrap_urls": "https://directory.example/simpleoffice",
        })
        self.assertEqual(["192.168.1.7", "10.0.0.9"], network.lan_addresses())
        self.assertEqual([9090], network.lan_ports())
        self.assertEqual(
            ["https://directory.example/simpleoffice"], network.bootstrap_raw(),
        )

    def test_flags_are_canonical(self):
        network.save({
            "allow_private_targets": "yes",
            "allow_loopback": "0",
            "refuse_bad_client": True,
        })
        status = network.status()
        self.assertTrue(status["allow_private_targets"])
        self.assertFalse(status["allow_loopback"])
        self.assertTrue(status["refuse_bad_client"])
        self.assertTrue(network.allow_private_targets())
        self.assertTrue(network.refuse_bad_client())

    def test_invalid_values_fail_bilingual(self):
        with self.assertRaises(ValueError) as german:
            network.validate({"lan_addresses": "ok\nbroken"})
        self.assertIn("Netzwerk", str(german.exception))
        with self.assertRaises(ValueError) as english:
            network.validate({"lan_ports": "ok\nbroken"}, "en")
        self.assertIn("network", str(english.exception).lower())
        with self.assertRaises(ValueError):
            network.validate({"allow_loopback": "maybe"})

    def test_set_lan_addresses_stores_native_bridge_values(self):
        network.set_lan_addresses("192.168.4.22,10.0.0.8")
        self.assertEqual(["192.168.4.22", "10.0.0.8"], network.lan_addresses())
        network.set_lan_addresses("")
        self.assertEqual([], network.lan_addresses())


class FederationNetworkAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        patcher = patch.object(
            app_config_store, "settings_path", return_value=base / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("network-admin", generate_password_hash("network-admin-password")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("network-user", generate_password_hash("network-user-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "network-admin", "password": "network-admin-password"},
        )
        self.assertLess(response.status_code, 400)
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_dashboard_shows_network_section(self):
        response = self.client.get("/admin/federation/peer-discovery")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('id="network-settings"', body)
        self.assertIn("Netzwerkeinstellungen", body)

    def test_save_network_settings(self):
        response = self.client.post(
            "/admin/federation/network/settings",
            data={
                "lan_addresses": "192.168.178.44",
                "lan_ports": "8765",
                "bootstrap_urls": "https://directory.example/simpleoffice",
                "allow_private_targets": "1",
                "allow_loopback": "",
                "refuse_bad_client": "1",
            },
        )
        self.assertEqual(302, response.status_code)
        self.assertIn("#network-settings", response.headers["Location"])
        self.assertEqual(["192.168.178.44"], network.lan_addresses())
        self.assertEqual([8765], network.lan_ports())
        self.assertTrue(network.allow_private_targets())
        self.assertFalse(network.allow_loopback())
        self.assertTrue(network.refuse_bad_client())

    def test_save_network_settings_rejects_invalid(self):
        response = self.client.post(
            "/admin/federation/network/settings",
            data={"lan_ports": "ok\nbroken"},
        )
        self.assertEqual(302, response.status_code)
        self.assertEqual([], network.lan_ports())

    def test_network_settings_need_admin(self):
        user_client = app.test_client()
        user_client.post(
            "/auth/login",
            data={"username": "network-user", "password": "network-user-password"},
        )
        response = user_client.post("/admin/federation/network/settings", data={})
        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
