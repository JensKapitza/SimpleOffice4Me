import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.federation_store import FederationStore
from app.federation_moderation import FederationModerationStore
from app.federation_qr import encode_peer
from app.federation_trust_store import FederationTrustStore


class FederationAdminUiTests(unittest.TestCase):
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
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("federation-ui-admin", generate_password_hash("federation-ui-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "federation-ui-admin", "password": "federation-ui-password"},
        )
        self.assertLess(response.status_code, 400)

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_task_focused_views_render(self):
        expected = {
            "overview": "Was möchtest du tun?",
            "files": "Lokale Dateien",
            "peers": "Verbundene Peers",
            "transfers": "Download-Warteschlange",
        }
        for view, marker in expected.items():
            with self.subTest(view=view):
                response = self.client.get(f"/admin/federation?view={view}")
                self.assertEqual(200, response.status_code)
                self.assertIn(marker, response.get_data(as_text=True))

    def test_moderation_page_renders_and_escapes_reported_content(self):
        moderation = FederationModerationStore(self.root)
        moderation.ban("bad-peer", "<script>probe()</script>", actor="admin")
        moderation.report("reporter", "another-peer", "<script>report()</script>")
        response = self.client.get("/admin/federation/moderation")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Peer-Verwaltung", body)
        self.assertIn("bad-peer", body)
        self.assertNotIn("<script>probe()</script>", body)
        self.assertNotIn("<script>report()</script>", body)
        self.assertIn("&lt;script&gt;probe()&lt;/script&gt;", body)

    def test_peer_edit_keeps_configured_activation_visible_during_ban(self):
        FederationStore(self.root).save_peer("office-b", "Office B", "https://b.example", "", enabled=True)
        FederationModerationStore(self.root).ban("office-b", "abuse", actor="admin")
        response = self.client.get("/admin/federation?view=peers&edit_peer=office-b")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("gesperrt", body)
        self.assertIn('id="fed-enabled" checked', body)

    def test_peer_form_maps_document_permissions_without_losing_advanced_policy(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "office-b",
                "label": "Office B",
                "base_url": "http://10.0.0.2:8080",
                "enabled": "1",
                "_documents_policy_form": "1",
                "documents_send": "1",
                "documents_seed": "1",
                "policy_json": '{"chat":{"send":true,"receive":false}}',
            },
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        self.assertIn("view=peers", response.headers["Location"])

        peer = FederationStore(self.root).get_peer("office-b")
        self.assertIsNotNone(peer)
        policy = peer["policy"]
        self.assertEqual(
            {"send": True, "receive": False, "seed": True},
            policy["documents"],
        )
        self.assertEqual({"send": True, "receive": False}, policy["chat"])

    def test_peer_form_maps_mail_case_policy_without_losing_advanced_policy(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "mail-peer",
                "label": "Mail Peer",
                "base_url": "https://mail-peer.example.test",
                "enabled": "1",
                "_mail_cases_policy_form": "1",
                "mail_cases_send": "1",
                "mail_cases_receive": "1",
                "mail_cases_auto_accept": "1",
                "policy_json": '{"chat":{"send":true,"receive":false},"data_classes":{"mail_cases":{"future_option":"keep"}}}',
            },
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        peer = FederationStore(self.root).get_peer("mail-peer")
        self.assertEqual(
            {
                "future_option": "keep",
                "send": True,
                "receive": True,
                "auto_accept": True,
            },
            peer["policy"]["data_classes"]["mail_cases"],
        )
        self.assertEqual(
            {"send": True, "receive": False},
            peer["policy"]["chat"],
        )

        response = self.client.get(
            "/admin/federation?view=peers&edit_peer=mail-peer"
        )
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Mail-Vorgang-Federation", body)
        self.assertIn('name="mail_cases_send" value="1" checked', body)
        self.assertIn('name="mail_cases_receive" value="1" checked', body)
        self.assertIn('name="mail_cases_auto_accept" value="1" checked', body)

    def test_mail_case_policy_defaults_to_deny_in_peer_form(self):
        response = self.client.get("/admin/federation?view=peers")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Mail-Vorgang-Federation", body)
        self.assertNotIn('name="mail_cases_send" value="1" checked', body)
        self.assertNotIn('name="mail_cases_receive" value="1" checked', body)
        self.assertNotIn('name="mail_cases_auto_accept" value="1" checked', body)

    def test_peer_discovery_exposes_mail_case_mapping_controls(self):
        response = self.client.get("/admin/federation/peer-discovery")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Mail-Vorgänge · Benutzerzuordnung", body)
        self.assertIn('name="remote_user_id"', body)
        self.assertIn('name="local_user_id"', body)
        self.assertIn("<code>mail_cases</code>-Policy", body)

    def test_peer_discovery_links_known_peer_to_existing_connection_editor(self):
        FederationStore(self.root).save_peer(
            "office-b", "Office B", "https://office-b.example.test", "", enabled=False,
        )
        FederationTrustStore(self.root).remember(
            "office-b", "DE", "fingerprint-b", "qr",
        )

        response = self.client.get("/admin/federation/peer-discovery")
        body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("Discovery und QR-Import speichern zunächst nur die Peer-Identität", body)
        self.assertIn("Verbindung einrichten", body)
        self.assertIn("view=peers&amp;edit_peer=office-b", body)
        self.assertIn("Trust jeweils lokal", body)

    def test_qr_import_opens_existing_connection_editor_without_enabling_peer(self):
        payload = encode_peer({
            "peer_id": "office-b",
            "label": "Office B",
            "country": "DE",
            "base_url": "https://office-b.example.test",
            "federation": {"name": "SimpleOffice Federation", "min_version": 1, "max_version": 1},
        })

        response = self.client.post(
            "/admin/federation/peer-discovery/qr/import",
            data={"payload": payload},
            follow_redirects=False,
        )

        self.assertEqual(302, response.status_code)
        self.assertIn("view=peers&edit_peer=office-b", response.headers["Location"])
        peer = FederationStore(self.root).get_peer("office-b")
        self.assertFalse(peer["enabled"])
        self.assertFalse(peer["has_token"])
        self.assertIsNone(FederationTrustStore(self.root).get_trust("office-b"))

        editor = self.client.get(response.headers["Location"])
        body = editor.get_data(as_text=True)
        self.assertEqual(200, editor.status_code)
        self.assertIn("Discovery hat nur Peer-Identität und Fähigkeiten gespeichert", body)
        self.assertIn("Peer-Token", body)

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

    def test_direct_discovery_shows_untrusted_address_as_suggestion(self):
        store = FederationStore(self.root)
        store.save_peer(
            "office-b", "Trusted Office B", "https://configured.example.test", "",
            {"documents": {"send": True}}, True,
        )
        profile = {
            "peer_id": "office-b",
            "base_url": "https://unverified.example.test",
            "compatibility": {"protocol": "unknown", "features": {}},
        }

        with patch("app.federation_peer_admin.discover_direct", return_value=profile):
            response = self.client.post(
                "/admin/federation/peer-discovery/discover/direct",
                data={"endpoint": "https://unverified.example.test"},
                follow_redirects=True,
            )

        self.assertEqual(200, response.status_code)
        self.assertIn("Erkannte Adresse (Vorschlag): https://unverified.example.test", response.get_data(as_text=True))
        self.assertIn("Bestehende Verbindungsziele werden nicht automatisch geändert", response.get_data(as_text=True))
        peer = store.get_peer("office-b")
        self.assertEqual("https://configured.example.test", peer["base_url"])
        self.assertTrue(peer["enabled"])

    def test_edit_peer_prefills_form(self):
        FederationStore(self.root).save_peer(
            "office-b",
            "Office B",
            "http://10.0.0.2:8080",
            "",
            {"documents": {"send": True, "receive": True, "seed": False}},
            True,
        )
        response = self.client.get("/admin/federation?view=peers&edit_peer=office-b")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Peer bearbeiten", body)
        self.assertIn('value="office-b"', body)
        self.assertIn('value="http://10.0.0.2:8080"', body)


if __name__ == "__main__":
    unittest.main()
