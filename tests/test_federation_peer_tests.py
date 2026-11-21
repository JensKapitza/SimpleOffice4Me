"""Blackbox: per-peer diagnostics (file echo, tone, QR, print dry-run)."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from werkzeug.security import generate_password_hash

from app import app, app_config_store
from app import db as database
from app.federation_audio_http import bp as audio_bp
from app.federation_blocks_http import bp as blocks_bp
from app.federation_peer_tests import test_file_send, test_print, test_qr, test_tone
from app.federation_print_auth import PrintRequestProof, new_nonce, sign_request
from app.federation_print_http import bp as print_bp
from app.federation_store import FederationStore
from app.printershare_store import PrinterDiscoveryResult, PrinterShareStore
from app.settings_store import SettingsStore


class PeerEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "peer"
        self.flask = Flask(__name__)
        self.flask.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root),
                                 SECRET_KEY="peer-test-secret")
        self.flask.register_blueprint(blocks_bp)
        self.flask.register_blueprint(audio_bp)
        self.client = self.flask.test_client()
        ctx = self.flask.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)

    def test_file_echo_returns_hash_without_storing(self):
        payload = b"x" * 1024
        response = self.client.post("/federation/v1/blocks/test", data=payload)
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), body["sha256"])

    def test_file_echo_rejects_oversize(self):
        response = self.client.post("/federation/v1/blocks/test", data=b"y" * 5000)
        self.assertEqual(413, response.status_code)

    def test_tone_renders_and_reports_playback_honestly(self):
        with patch("app.federation_audio_http.play_file") as play:
            response = self.client.post("/federation/v1/audio/test-tone", json={})
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["rendered"])
        self.assertTrue(body["played"])
        play.assert_called_once()
        events = [row for row in FederationStore(self.root).events()
                  if row.get("action") == "federation_audio_test_tone"]
        self.assertEqual(1, len(events))

    def test_tone_reports_missing_player_without_failing(self):
        with patch("app.federation_audio_http.play_file",
                   side_effect=RuntimeError("Kein Audio-Player gefunden")):
            response = self.client.post("/federation/v1/audio/test-tone", json={})
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["rendered"])
        self.assertFalse(body["played"])
        self.assertIn("Audio-Player", body["player_error"])


class PeerSenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "local"
        store_patcher = patch.object(
            app_config_store, "settings_path",
            return_value=Path(self.temp.name) / "app-config.json",
        )
        store_patcher.start()
        self.addCleanup(store_patcher.stop)
        app_config_store.put("federation", {"peer_id": "local-peer"})
        SettingsStore(self.root).save(
            {"integrations": {"federation_public_url": "https://local.invalid",
                              "federation_profile_country": "DE"}}, "peer-test")
        self.flask = Flask(__name__)
        self.flask.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root),
                                 SECRET_KEY="peer-test-secret")
        ctx = self.flask.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)
        FederationStore(self.root).save_peer(
            "remote-peer", "Remote", "https://remote.invalid", "peer-token",
            {"printing": {"send": True}}, True)

    def test_file_send_compares_hash(self):
        expected = hashlib.sha256(b"z" * 1024).hexdigest()
        with patch("app.federation_peer_tests._post",
                   return_value=(200, {"ok": True, "sha256": expected})) as post:
            with patch("app.federation_peer_tests.secrets.token_bytes", return_value=b"z" * 1024):
                result = test_file_send(self.root, "remote-peer")
        self.assertTrue(result["ok"])
        self.assertTrue(post.call_args[0][2].endswith("/federation/v1/blocks/test"))

    def test_file_send_detects_mismatch(self):
        with patch("app.federation_peer_tests._post",
                   return_value=(200, {"ok": True, "sha256": "0" * 64})):
            with self.assertRaises(ValueError):
                test_file_send(self.root, "remote-peer")

    def test_tone_reports_stages(self):
        with patch("app.federation_peer_tests._post",
                   return_value=(200, {"ok": True, "rendered": True, "played": False,
                                       "player_error": "no player"})):
            result = test_tone(self.root, "remote-peer")
        self.assertTrue(result["ok"])
        self.assertFalse(result["played"])

    def test_qr_roundtrip_verifies_identity(self):
        result = test_qr(self.root)
        self.assertTrue(result["ok"])
        self.assertEqual("local-peer", result["peer_id"])

    def test_disabled_peer_is_rejected(self):
        FederationStore(self.root).save_peer(
            "remote-peer", "Remote", "https://remote.invalid", "",
            {"printing": {"send": True}}, False)
        with self.assertRaises(ValueError):
            test_file_send(self.root, "remote-peer")


class PrintDryRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.flask = Flask(__name__)
        self.flask.config.update(TESTING=True, DOCUMENT_ROOT=str(self.root),
                                 SECRET_KEY="print-test-secret")
        self.flask.register_blueprint(print_bp)
        store_patcher = patch.object(
            app_config_store, "settings_path", return_value=self.root / "app-config.json")
        store_patcher.start()
        self.addCleanup(store_patcher.stop)
        discover = patch("app.printershare_store.discover_printer_inventory",
                         return_value=PrinterDiscoveryResult([{
                             "printer_id": "printer-1", "name": "P1", "driver": "d",
                             "port": "TEST:", "is_default": True, "kind": "label",
                             "backend": "test"}]))
        discover.start()
        self.addCleanup(discover.stop)
        with self.flask.app_context():
            app_config_store.put_secret("federation", "token", "receiver-token")
            store = PrinterShareStore(self.root, "print-test-secret")
            store.update_settings({"federation_enabled": True,
                                   "federation_default_retention": "permanent"})
            store.set_printer("printer-1", kind="label", federation_shared=True, label="P1")
            FederationStore(self.root).save_peer(
                "source-a", "Source A", "https://source-a.invalid", "source-token",
                {"printing": {"receive": True}}, True)
            self.revision = store.policy_revision()
        self.client = self.flask.test_client()
        ctx = self.flask.app_context()
        ctx.push()
        self.addCleanup(ctx.pop)

    def _headers(self, payload: bytes):
        import time

        proof = PrintRequestProof(
            peer_id="source-a", timestamp=int(time.time()), nonce=new_nonce(),
            printer_id="printer-1", policy_revision=self.revision,
            retention_ceiling="permanent", ttl_ceiling_seconds=0,
            content_type="text/plain", filename="Testdruck Federation",
            payload_sha256=hashlib.sha256(payload).hexdigest(), payload_size=len(payload))
        return {
            "Authorization": "Bearer receiver-token",
            "X-SimpleOffice-Print-Timestamp": str(proof.timestamp),
            "X-SimpleOffice-Print-Nonce": proof.nonce,
            "X-SimpleOffice-Payload-Size": str(proof.payload_size),
            "X-SimpleOffice-Payload-SHA256": proof.payload_sha256,
            "X-SimpleOffice-Print-Signature": sign_request(proof, "source-token"),
            "X-SimpleOffice-Policy-Revision": proof.policy_revision,
            "X-SimpleOffice-Retention-Ceiling": proof.retention_ceiling,
            "X-SimpleOffice-TTL-Ceiling": str(proof.ttl_ceiling_seconds),
            "X-SimpleOffice-Content-Type": proof.content_type,
            "X-SimpleOffice-Filename": proof.filename,
        }

    def test_dry_run_verifies_call_without_spooling(self):
        payload = b"Federation print dry-run (no spooling)"
        with patch("app.printershare_store.spool_payload") as spool:
            response = self.client.post("/federation/v1/print/jobs/printer-1?dry_run=1",
                                        data=payload, headers=self._headers(payload))
        self.assertEqual(201, response.status_code)
        spool.assert_not_called()
        receipt = response.get_json()["receipt"]
        self.assertEqual("test", receipt["status"])
        jobs = PrinterShareStore(self.root, "print-test-secret").jobs()
        self.assertEqual(1, len(jobs))
        self.assertEqual("test", jobs[0]["status"])
        self.assertEqual("federation", jobs[0]["source"])

    def test_real_submit_still_spools(self):
        payload = b"real job"
        with patch("app.printershare_store.spool_payload", return_value="spool-ok") as spool:
            response = self.client.post("/federation/v1/print/jobs/printer-1",
                                        data=payload, headers=self._headers(payload))
        self.assertEqual(201, response.status_code)
        spool.assert_called_once()
        self.assertEqual("spooled", response.get_json()["receipt"]["status"])


class PeerAdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(base / "users.sqlite"),
                          DOCUMENT_ROOT=str(base / "documents"))
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES(?,?,1,datetime('now'),datetime('now'))",
                ("boss", generate_password_hash("boss-password")))
            db.commit()
        self.client = app.test_client()
        self.client.post("/auth/login", data={"username": "boss", "password": "boss-password"})

    def tearDown(self):
        app.config.update(self.saved)

    def test_admin_test_buttons_post_and_flash(self):
        cases = [
            ("test_file_send", "test-file", {"ok": True, "sha256": "ab12", "latency_ms": 3}),
            ("test_tone", "test-tone", {"ok": True, "rendered": True, "played": True}),
            ("test_print", "test-print", {"ok": True, "printer_id": "p1", "job_id": "j12345678"}),
        ]
        for sender, action, answer in cases:
            with patch(f"app.federation_admin.{sender}", return_value=answer) as call:
                response = self.client.post(f"/admin/federation/peers/peer-x/{action}")
            self.assertEqual(302, response.status_code, action)
            call.assert_called_once()
        with patch("app.federation_admin.test_qr",
                   return_value={"ok": True, "peer_id": "local", "fingerprint": "abc123"}) as call:
            response = self.client.post("/admin/federation/peers/peer-x/test-qr")
        self.assertEqual(302, response.status_code)
        call.assert_called_once()
