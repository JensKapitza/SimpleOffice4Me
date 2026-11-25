"""Integration regressions for the federation peer connection workflow."""

import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from app import app
from app.federation_store import FederationStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationPeerConnectionUxTests(FederationAdminUiFixture, unittest.TestCase):
    def test_setup_explains_two_tokens_and_offers_qr_image_fallback(self):
        for language, marker in (
            ("de", "Das Server-Token ist ein eigener Zugang"),
            ("en", "The server token is a separate credential"),
        ):
            with self.subTest(language=language):
                with self.client.session_transaction() as session:
                    session["simpleoffice_language"] = language
                response = self.client.get("/admin/federation?view=peers")
                self.assertEqual(200, response.status_code)
                body = response.get_data(as_text=True)
                self.assertIn(marker, body)
                self.assertIn('id="token-qr-image-file"', body)
                self.assertIn('id="token-qr-image-button"', body)
                self.assertIn("#local-identity", body)

    def test_probe_401_explains_server_token_mismatch(self):
        with app.app_context():
            FederationStore(self.root).save_peer("office-b", "Office B", "https://b.example", "peer-secret")
        failure = HTTPError("https://b.example/federation/v1/capabilities", 401, "Unauthorized", {}, None)
        with patch("app.federation_admin.peer_capabilities", side_effect=failure):
            response = self.client.post("/admin/federation/peers/office-b/probe")
        self.assertEqual(302, response.status_code)
        body = self.client.get(response.location).get_data(as_text=True)
        self.assertIn("Server-Token abgelehnt", body)
        self.assertIn("Peer-Token bleibt getrennt", body)

    def test_discovery_has_short_qr_and_audio_connection_flow(self):
        response = self.client.get("/admin/federation/peer-discovery")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Peers schnell verbinden", body)
        self.assertIn("#connect-qr", body)
        self.assertIn("#qr-import", body)
        self.assertIn("Die Suche in Gegenrichtung wiederholen", body)
        self.assertIn("keine Tokens", body)


if __name__ == "__main__":
    unittest.main()
