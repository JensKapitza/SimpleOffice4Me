"""Isolated federation testnet seeding, including the disposable local identity.

The isolated Compose network (deploy/docker/compose.federation-test.yaml) is
the only place whose ``SIMPLEOFFICE_FEDERATION_*`` values this tool accepts.
Production code reads no identity environment variables at runtime (No-Env
rule) and the master identity stays build-time only in app/build_master.py;
these tests guard both rules alongside the seeding behavior so the CI smoke
expectations are never weakened to match a broken identity.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, app_config_store
from app import federation_local_identity as local_identity
from app.federation_store import FederationStore
from app.settings_store import SettingsStore
from tools import federation_testnet_seed as seed


ROOT = Path(__file__).resolve().parents[1]

PEER_A_ENV = {
    "SIMPLEOFFICE_FEDERATION_PEER_ID": "federation-test-peer-a",
    "SIMPLEOFFICE_FEDERATION_PUBLIC_URL": "http://peer-a:8080",
    "SIMPLEOFFICE_FEDERATION_TOKEN": "federation-test-only-global-token",
    "SIMPLEOFFICE_FEDERATION_TEST_LOCAL_PEER_TOKEN": "federation-test-only-peer-a-token",
    "SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_ID": "federation-test-peer-b",
    "SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_URL": "http://peer-b:8080",
    "SIMPLEOFFICE_FEDERATION_TEST_REMOTE_PEER_TOKEN": "federation-test-only-peer-b-token",
    "SIMPLEOFFICE_FEDERATION_TEST_RELAY_PEER_ID": "federation-test-relay",
    "SIMPLEOFFICE_FEDERATION_TEST_RELAY_URL": "http://relay:8080",
    "SIMPLEOFFICE_FEDERATION_TEST_RELAY_TOKEN": "relay-a",
}

RELAY_ENV = {
    "SIMPLEOFFICE_FEDERATION_PEER_ID": "federation-test-relay",
    "SIMPLEOFFICE_FEDERATION_PUBLIC_URL": "http://relay:8080",
    "SIMPLEOFFICE_FEDERATION_TOKEN": "federation-test-only-global-token",
    "SIMPLEOFFICE_FEDERATION_TEST_PEER_A_ID": "federation-test-peer-a",
    "SIMPLEOFFICE_FEDERATION_TEST_PEER_A_RELAY_TOKEN": "relay-a",
    "SIMPLEOFFICE_FEDERATION_TEST_PEER_B_ID": "federation-test-peer-b",
    "SIMPLEOFFICE_FEDERATION_TEST_PEER_B_RELAY_TOKEN": "relay-b",
}


class TestnetSeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")
        }
        app.config.update(
            TESTING=True,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        patcher = patch.object(
            app_config_store, "settings_path", return_value=base / "app-config.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_peer_seed_writes_disposable_local_identity(self):
        with patch("sys.argv", ["federation_testnet_seed"]), patch.dict(os.environ, PEER_A_ENV):
            seed.main()
        with app.app_context():
            self.assertEqual("federation-test-peer-a", local_identity.peer_id())
            self.assertEqual("http://peer-a:8080", local_identity.public_url())
            self.assertEqual("federation-test-only-global-token", local_identity.shared_token())
            stored_url = SettingsStore(self.root).settings()["integrations"]["federation_public_url"]
            self.assertEqual("http://peer-a:8080", stored_url)
            peers = FederationStore(self.root)
            self.assertEqual("http://peer-b:8080", peers.get_peer("federation-test-peer-b")["base_url"])
            self.assertEqual("http://relay:8080", peers.get_peer("federation-test-relay")["base_url"])

    def test_relay_seed_writes_relay_local_identity(self):
        argv = ["federation_testnet_seed", "--configure-relay-node"]
        with patch("sys.argv", argv), patch.dict(os.environ, RELAY_ENV):
            seed.main()
        with app.app_context():
            self.assertEqual("federation-test-relay", local_identity.peer_id())
            self.assertEqual("http://relay:8080", local_identity.public_url())
            stored_url = SettingsStore(self.root).settings()["integrations"]["federation_public_url"]
            self.assertEqual("http://relay:8080", stored_url)
            peers = FederationStore(self.root)
            self.assertEqual("http://peer-a:8080", peers.get_peer("federation-test-peer-a")["base_url"])
            self.assertEqual("http://peer-b:8080", peers.get_peer("federation-test-peer-b")["base_url"])

    def test_non_testnet_identity_is_refused(self):
        env = dict(PEER_A_ENV)
        env["SIMPLEOFFICE_FEDERATION_PEER_ID"] = "office-prod"
        env["SIMPLEOFFICE_FEDERATION_PUBLIC_URL"] = "https://office.example"
        with patch("sys.argv", ["federation_testnet_seed"]), patch.dict(os.environ, env):
            with self.assertRaises(SystemExit):
                seed.main()

    def test_mismatched_testnet_url_is_refused(self):
        env = dict(PEER_A_ENV)
        env["SIMPLEOFFICE_FEDERATION_PUBLIC_URL"] = "http://peer-b:8080"
        with patch("sys.argv", ["federation_testnet_seed"]), patch.dict(os.environ, env):
            with self.assertRaises(SystemExit):
                seed.main()

    def test_missing_local_identity_is_refused(self):
        env = dict(PEER_A_ENV)
        del env["SIMPLEOFFICE_FEDERATION_TOKEN"]
        with patch("sys.argv", ["federation_testnet_seed"]), patch.dict(os.environ, env):
            os.environ.pop("SIMPLEOFFICE_FEDERATION_TOKEN", None)
            with self.assertRaises(SystemExit):
                seed.main()

    def test_production_identity_rules_are_untouched(self):
        build_master = (ROOT / "app" / "build_master.py").read_text(encoding="utf-8")
        self.assertNotIn("os.environ", build_master)
        self.assertIn('LICENSE_MASTER_PEER_ID = "license-master"', build_master)
        for name in ("federation_local_identity.py", "federation_local_profile.py"):
            source = (ROOT / "app" / name).read_text(encoding="utf-8")
            self.assertNotIn("SIMPLEOFFICE_FEDERATION_PEER_ID", source)
            self.assertNotIn("SIMPLEOFFICE_FEDERATION_PUBLIC_URL", source)
        smoke = (ROOT / "tools" / "federation_testnet_smoke.py").read_text(encoding="utf-8")
        self.assertIn('"federation-test-peer-a", "http://peer-a:8080"', smoke)
        self.assertIn('"federation-test-relay", "http://relay:8080"', smoke)


if __name__ == "__main__":
    unittest.main()
