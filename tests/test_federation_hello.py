"""Two-sided hello: a scanned peer appears on both sides (untrusted)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from app import app, app_config_store
from app import db as database
from app.federation_discovery_http import bp as discovery_bp
from app.federation_hello import build_hello, receive_hello, send_hello
from app.federation_qr import encode_peer
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from app.settings_store import SettingsStore


class FederationHelloTwoPeerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_a = Path(self.temp.name) / "peer-a"
        self.root_b = Path(self.temp.name) / "peer-b"
        store_patcher = patch.object(
            app_config_store, "settings_path",
            return_value=Path(self.temp.name) / "app-config.json",
        )
        store_patcher.start()
        self.addCleanup(store_patcher.stop)
        app_config_store.put("federation", {"peer_id": "peer-a"})
        for root, url in ((self.root_a, "https://peer-a.invalid"),
                          (self.root_b, "https://peer-b.invalid")):
            SettingsStore(root).save(
                {"integrations": {"federation_public_url": url,
                                  "federation_profile_country": "DE"}},
                "hello-test",
            )
        self.app_b = Flask(__name__)
        self.app_b.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root_b),
                                 SECRET_KEY="hello-test-secret")
        self.app_b.register_blueprint(discovery_bp)
        self.client_b = self.app_b.test_client()
        ctx = self.app_b.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)
        self.root_admin = Path(self.temp.name) / "admin"
        self.saved_app = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "admin.sqlite"),
                          DOCUMENT_ROOT=str(self.root_admin))
        self.addCleanup(app.config.update, self.saved_app)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES(?,?,1,datetime('now'),datetime('now'))",
                ("boss", generate_password_hash("boss-password")),
            )
            db.commit()
        self.real_client = app.test_client()
        self.real_client.post("/auth/login", data={"username": "boss", "password": "boss-password"})

    def test_hello_registers_peer_visibly_without_trust(self):
        payload = build_hello(self.root_a)
        response = self.client_b.post("/federation/v1/discovery/hello", json=payload)
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["ok"])
        peer = FederationStore(self.root_b).get_peer("peer-a")
        self.assertIsNotNone(peer)
        self.assertFalse(peer["enabled"])
        identity = FederationTrustStore(self.root_b).identity("peer-a")
        self.assertTrue(identity["fingerprint"])
        # Trust stays untouched: no verification level granted by a hello.
        trust = FederationTrustStore(self.root_b)
        granted = trust.get_trust("peer-a")
        self.assertTrue(granted is None or granted.get("trust_level", "NONE") == "NONE")

    def test_hello_round_trip_verifies_responder(self):
        def deliver(url, body, timeout):
            self.assertTrue(url.endswith("/federation/v1/discovery/hello"))
            with self.app_b.test_request_context():
                from app.federation_hello import receive_hello as receive
                return receive(self.root_b, body)

        with patch("app.federation_hello._post_json", side_effect=deliver):
            result = send_hello(self.root_a, {"peer_id": "peer-b",
                                              "base_url": "https://peer-b.invalid",
                                              "fingerprint": ""})
        self.assertTrue(result["ok"])
        self.assertIsNotNone(FederationStore(self.root_b).get_peer("peer-a"))
        self.assertIn("fingerprint", result["responder"])

    def test_tampered_hello_is_rejected(self):
        payload = build_hello(self.root_a)
        payload["label"] = "Evil Relay"
        response = self.client_b.post("/federation/v1/discovery/hello", json=payload)
        self.assertEqual(400, response.status_code)
        self.assertIsNone(FederationStore(self.root_b).get_peer("peer-a"))

    def test_qr_import_announces_hello_and_reports_answer(self):
        payload = encode_peer({
            "peer_id": "office-b", "label": "Office B", "country": "DE",
            "base_url": "https://office-b.example.test",
            "federation": {"name": "SimpleOffice Federation", "min_version": 1, "max_version": 1},
        })
        answer = {"ok": True, "responder": {"peer_id": "office-b"}}
        with patch("app.federation_peer_admin.send_hello", return_value=answer) as hello:
            response = self.real_client.post(
                "/admin/federation/peer-discovery/qr/import", data={"payload": payload})
        self.assertEqual(302, response.status_code)
        hello.assert_called_once()
        sent_profile = hello.call_args[0][1]
        self.assertEqual("office-b", sent_profile["peer_id"])
        with self.real_client.session_transaction() as session:
            flashes = [message for _category, message in session.get("_flashes", [])]
        self.assertTrue(any("meldet sich zurück" in message or "answered back" in message
                            for message in flashes), flashes)

    def test_impersonation_of_known_peer_is_rejected(self):
        first = build_hello(self.root_a)
        response = self.client_b.post("/federation/v1/discovery/hello", json=first)
        self.assertEqual(200, response.status_code)
        forged = build_hello(self.root_b)
        forged["peer_id"] = "peer-a"
        response = self.client_b.post("/federation/v1/discovery/hello", json=forged)
        self.assertEqual(400, response.status_code)
        identity = FederationTrustStore(self.root_b).identity("peer-a")
        self.assertEqual(first["fingerprint"], identity["fingerprint"])
