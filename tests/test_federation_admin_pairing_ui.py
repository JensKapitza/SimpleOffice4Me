"""Focused administrator UI coverage for pairing and QR identity."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import session

from app import app
from app.federation_identity import FederationIdentity
from app.federation_pairing import FederationPairingTokens
from app.federation_qr import encode_connection_invite, encode_peer
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationAdminPairingUiTests(FederationAdminUiFixture, unittest.TestCase):
    def test_read_only_qr_images_do_not_refresh_a_stale_session_cookie(self):
        profile = {
            "peer_id": "office-a", "label": "Office A", "country": "DE",
            "base_url": "http://192.168.1.20:8090",
        }
        with patch("app.federation_peer_admin.local_profile", return_value=profile), patch(
            "app.federation_peer_admin._lan_connect_profiles",
            return_value=[{"payload": encode_peer(profile)}],
        ):
            for path in (
                "/admin/federation/peer-discovery/qr.svg",
                "/admin/federation/peer-discovery/qr/lan/0.svg",
            ):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(200, response.status_code)
                    self.assertIn("no-store", response.headers["Cache-Control"])
                    self.assertIn("Cookie", response.headers.get("Vary", ""))
                    self.assertNotIn("Set-Cookie", response.headers)

    def test_qr_image_still_sets_cookie_for_a_real_session_change(self):
        profile = {
            "peer_id": "office-a", "label": "Office A", "country": "DE",
            "base_url": "http://192.168.1.20:8090",
        }

        def change_session():
            session["qr-session-regression"] = "changed"
            return [{"payload": encode_peer(profile)}]

        with patch("app.federation_peer_admin._lan_connect_profiles", side_effect=change_session):
            response = self.client.get("/admin/federation/peer-discovery/qr/lan/0.svg")

        self.assertEqual(200, response.status_code)
        self.assertIn("Set-Cookie", response.headers)

    def test_one_scan_invite_page_is_short_lived_and_has_no_store_headers(self):
        profile = {
            "peer_id": "office-a", "label": "Office A", "country": "DE",
            "base_url": "http://192.168.1.20:8090",
        }
        with patch("app.federation_peer_admin._lan_connect_profiles", return_value=[{
            "address": "192.168.1.20", "port": 8090,
            "endpoint": profile["base_url"], "payload": encode_peer(profile),
        }]):
            response = self.client.post(
                "/admin/federation/peer-discovery/pairing/create-invite/0",
            )
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store, private", response.headers["Cache-Control"])
        body = response.get_data(as_text=True)
        self.assertIn("Einmal auf Gerät B scannen", body)
        self.assertIn("Er läuft nach 10 Minuten", body)

    def test_one_scan_import_calls_pairing_flow_without_manual_token(self):
        payload = encode_connection_invite({
            "peer_id": "office-a", "label": "Office A", "country": "DE",
            "base_url": "https://office-a.example.test",
        }, "a" * 24, "b" * 43)
        with patch("app.federation_peer_admin.send_connection_invite", return_value={"ok": True}) as pair:
            response = self.client.post(
                "/admin/federation/peer-discovery/qr/import",
                data={"payload": payload},
            )
        self.assertEqual(302, response.status_code)
        pair.assert_called_once()
        self.assertEqual("office-a", pair.call_args.args[1]["peer"]["peer_id"])
        self.assertTrue(pair.call_args.args[2].startswith("http://localhost"))
        with self.client.session_transaction() as session:
            flashes = [message for _category, message in session.get("_flashes", [])]
        self.assertTrue(any("eingerichtet" in message for message in flashes), flashes)

    def test_pairing_code_requires_discovered_public_key(self):
        FederationTrustStore(self.root).remember(
            "office-b", "DE", "fingerprint-only", "legacy-discovery",
        )

        response = self.client.post(
            "/admin/federation/peer-discovery/pairing/office-b/issue",
            follow_redirects=True,
        )

        self.assertEqual(200, response.status_code)
        self.assertIn(
            "Peer-Identität benötigt öffentlichen Schlüssel und Fingerprint",
            response.get_data(as_text=True),
        )
        self.assertEqual("missing", FederationPairingTokens(
            self.root, app.config["SECRET_KEY"]
        ).status("office-b")["status"])

    def test_pairing_code_can_be_revoked_without_changing_trust(self):
        with app.app_context():
            identity = FederationIdentity(self.root / "remote-identity").public_identity()
        FederationTrustStore(self.root).remember(
            "office-b", "DE", identity["fingerprint"], "qr", identity["public_key"],
        )
        tokens = FederationPairingTokens(self.root, app.config["SECRET_KEY"])
        tokens.issue("office-b")

        response = self.client.post(
            "/admin/federation/peer-discovery/pairing/office-b/revoke",
            follow_redirects=False,
        )

        self.assertEqual(302, response.status_code)
        self.assertEqual("missing", tokens.status("office-b")["status"])
        self.assertIsNone(FederationTrustStore(self.root).get_trust("office-b"))

    def test_claimant_ui_requires_explicit_identity_confirmation(self):
        self._remember_pairing_peer()

        with patch("app.federation_peer_admin.claim_pairing_code") as claim:
            response = self.client.post(
                "/admin/federation/peer-discovery/pairing/office-b/claim",
                data={"code": "2345-6789-ABCD"},
                follow_redirects=True,
            )

        self.assertEqual(200, response.status_code)
        claim.assert_not_called()
        self.assertIn(
            "Fingerprint und Zieladresse müssen ausdrücklich bestätigt werden",
            response.get_data(as_text=True),
        )

    def test_claimant_ui_does_not_grant_access_or_persist_code(self):
        self._remember_pairing_peer()

        with patch(
            "app.federation_peer_admin.claim_pairing_code",
            return_value={"peer_id": "local-peer", "status": "claimed"},
        ) as claim:
            response = self.client.post(
                "/admin/federation/peer-discovery/pairing/office-b/claim",
                data={"code": "2345-6789-ABCD", "confirm_identity": "1"},
                follow_redirects=True,
            )

        self.assertEqual(200, response.status_code)
        claim.assert_called_once_with(str(self.root), claim.call_args.args[1], "2345-6789-ABCD")
        body = response.get_data(as_text=True)
        self.assertIn("Trust, Aktivierung und Datenrechte bleiben unverändert", body)
        self.assertNotIn("2345-6789-ABCD", body)
        peer = FederationStore(self.root).get_peer("office-b")
        self.assertFalse(peer["enabled"])
        self.assertFalse(peer["has_token"])
        self.assertIsNone(FederationTrustStore(self.root).get_trust("office-b"))
        self.assertNotIn("2345-6789-ABCD", json.dumps(FederationStore(self.root).events(20)))

    def test_qr_import_does_not_replace_existing_connection_configuration(self):
        store = FederationStore(self.root)
        with app.app_context():
            store.save_peer(
                "office-b", "Trusted Office B", "https://configured.example.test", "existing-peer-token",
                {"documents": {"send": True}}, True,
            )
        payload = encode_peer({
            "peer_id": "office-b",
            "label": "Unverified replacement",
            "country": "DE",
            "base_url": "https://attacker.example.test",
            "federation": {"name": "SimpleOffice Federation", "min_version": 1, "max_version": 1},
        })

        response = self.client.post(
            "/admin/federation/peer-discovery/qr/import",
            data={"payload": payload},
            follow_redirects=False,
        )

        self.assertEqual(302, response.status_code)
        with app.app_context():
            peer = store.get_peer("office-b")
            self.assertEqual("https://configured.example.test", peer["base_url"])
            self.assertEqual("existing-peer-token", store.peer_token("office-b"))
            self.assertEqual({"documents": {"send": True}}, peer["policy"])
            self.assertTrue(peer["enabled"])
        editor = self.client.get(response.headers["Location"])
        self.assertIn("QR-Adresse (Vorschlag): https://attacker.example.test", editor.get_data(as_text=True))

    def test_qr_import_keeps_identity_on_fingerprint_conflict(self):
        from app.federation_identity import FederationIdentity

        extra = tempfile.TemporaryDirectory()
        self.addCleanup(extra.cleanup)
        with app.app_context():
            forged = FederationIdentity(Path(extra.name)).public_identity()
            store = FederationStore(self.root)
            store.save_peer(
                "office-b", "Trusted Office B", "https://configured.example.test", "tok",
                {"documents": {"send": True}}, True,
            )
            FederationTrustStore(self.root).remember(
                "office-b", "DE", "0" * 64, "qr", "cHViLWtleS1vcmlnaW5hbA==")
        payload = encode_peer({
            "peer_id": "office-b", "label": "Office B", "country": "DE",
            "base_url": "https://office-b.example.test",
            "fingerprint": forged["fingerprint"], "public_key": forged["public_key"],
            "federation": {"name": "SimpleOffice Federation", "min_version": 1, "max_version": 1},
        })
        response = self.client.post(
            "/admin/federation/peer-discovery/qr/import",
            data={"payload": payload}, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("anderer Fingerabdruck", body)
        with app.app_context():
            identity = FederationTrustStore(self.root).identity("office-b")
            self.assertEqual("0" * 64, identity["fingerprint"])
            conflicts = [row for row in FederationStore(self.root).events()
                         if row.get("action") == "peer_identity_conflict"]
            self.assertEqual(1, len(conflicts))
