import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.federation_identity import FederationIdentity
from app.federation_moderation import FederationModerationStore
from app.federation_pairing import FederationPairingTokens
from app.federation_pairing_client import PairingClientError
from app.federation_qr import encode_peer
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore


class FederationBrowserDiagnosticsFrontendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_browser_diagnostics_client_safety_and_statuses(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), "--test", str(root / "tests" / "federation_browser_diagnostics.test.cjs")],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


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
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("federation-ui-user", generate_password_hash("federation-user-password")),
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

    def test_browser_diagnostic_actions_only_render_for_http_peers(self):
        store = FederationStore(self.root)
        store.save_peer("web-peer", "Web Peer", "https://peer.example/base", "", enabled=True)
        store.save_peer("other-peer", "Other Peer", "ftp://peer.example/files", "", enabled=True)

        response = self.client.get("/admin/federation?view=peers")
        body = response.get_data(as_text=True)

        self.assertEqual(200, response.status_code)
        self.assertIn("js/federation_browser_diagnostics.js", body)
        self.assertIn('data-peer-open="web-peer"', body)
        self.assertIn('data-peer-browser-check="web-peer"', body)
        self.assertIn('data-peer-manual-confirm="web-peer"', body)
        self.assertIn('data-peer-url="https://peer.example/base"', body)
        self.assertNotIn('data-peer-open="other-peer"', body)
        self.assertNotIn('data-peer-browser-check="other-peer"', body)

    def test_peer_dashboard_separates_outbound_and_authenticated_inbound_diagnostics(self):
        store = FederationStore(self.root)
        store.save_peer("diagnostic-peer", "Diagnostic Peer", "https://peer.example", "", enabled=True)
        checked_at = int(time.time())
        store.record_network_diagnostic(
            "diagnostic-peer", direction="outbound", source="local", status="failure",
            category="tls_certificate", checked_at=checked_at,
            detail={"stages": {"dns": "ok", "tcp": "ok", "tls": "certificate_failed", "http": "unknown", "authentication": "unknown"}},
        )
        store.record_network_diagnostic(
            "diagnostic-peer", direction="inbound", source="diagnostic-peer", status="success",
            category="authenticated_probe", checked_at=checked_at,
            detail={"stages": {"authentication": "ok", "http": "accepted"}},
        )

        response = self.client.get("/admin/federation?view=peers")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Server → konfigurierte Peer-Adresse: nicht erreichbar", body)
        self.assertIn("TLS: Zertifikat ungültig", body)
        self.assertIn("Authentifizierter Peer → Server: erreichbar", body)
        self.assertIn("zuletzt erfolgreich", body)

    def test_peer_dashboard_marks_old_diagnostic_results_stale(self):
        store = FederationStore(self.root)
        store.save_peer("stale-peer", "Stale Peer", "https://stale.example", "", enabled=True)
        old_timestamp = int(time.time()) - 600
        store.record_network_diagnostic(
            "stale-peer", direction="outbound", source="local", status="success",
            checked_at=old_timestamp, detail={"stages": {"authentication": "ok"}},
        )

        response = self.client.get("/admin/federation?view=peers")
        self.assertEqual(200, response.status_code)
        row = response.get_data(as_text=True).split("stale-peer", 1)[1]
        self.assertIn("Server → konfigurierte Peer-Adresse: veraltet", row)
        self.assertIn("zuletzt erfolgreich", row)

    def test_manual_peer_confirmation_is_persisted_with_actor_and_unverified_label(self):
        FederationStore(self.root).save_peer(
            "manual-peer", "Manual Peer", "https://manual.example", "", enabled=True,
        )
        response = self.client.post(
            "/admin/federation/peers/manual-peer/diagnostics/manual",
            data={"outcome": "reachable"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("federation-ui-admin", response.json["actor"])
        self.assertFalse(response.json["technical_verification"])

        body = self.client.get("/admin/federation?view=peers").get_data(as_text=True)
        self.assertIn("Benutzerbestätigung: Seite geöffnet", body)
        self.assertIn("admin", body)
        self.assertIn("nicht technisch verifiziert", body)

    def test_normal_users_only_see_admin_shared_aggregate_status(self):
        store = FederationStore(self.root)
        with app.app_context():
            store.save_peer(
                "shared-peer", "Shared Office", "https://private.example/base", "secret-token",
                {"network_diagnostics": {"share_status_with_users": True}}, True,
            )
            store.save_peer(
                "hidden-peer", "Hidden Office", "https://hidden.example", "hidden-token",
                {"network_diagnostics": {"share_status_with_users": False}}, True,
            )
        store.record_network_diagnostic(
            "shared-peer", direction="outbound", source="local", status="failure",
            category="tls_certificate", detail={"stages": {"dns": "ok", "tcp": "ok", "tls": "certificate_failed"}},
        )
        user_client = app.test_client()
        login = user_client.post(
            "/auth/login",
            data={"username": "federation-ui-user", "password": "federation-user-password"},
        )
        self.assertLess(login.status_code, 400)

        response = user_client.get("/federation/diagnostics")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Federation-Status", body)
        self.assertIn('href="/federation/diagnostics"', body)
        self.assertIn("Shared Office", body)
        self.assertIn("nicht erreichbar", body)
        self.assertNotIn("Hidden Office", body)
        self.assertNotIn("private.example", body)
        self.assertNotIn("tls_certificate", body)
        self.assertNotIn("certificate_failed", body)
        self.assertEqual(403, user_client.get("/admin/federation?view=peers").status_code)

    def test_peer_form_explicitly_controls_user_status_sharing(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "shared-form-peer",
                "label": "Shared Form Peer",
                "base_url": "https://peer.example",
                "enabled": "1",
                "policy_json": '{"future":{"keep":true}}',
                "_network_diagnostics_policy_form": "1",
                "network_diagnostics_share_status": "1",
            },
        )
        self.assertEqual(302, response.status_code)
        peer = FederationStore(self.root).get_peer("shared-form-peer")
        self.assertEqual({"keep": True}, peer["policy"]["future"])
        self.assertTrue(peer["policy"]["network_diagnostics"]["share_status_with_users"])
        editor = self.client.get("/admin/federation?view=peers&edit_peer=shared-form-peer")
        self.assertRegex(
            editor.get_data(as_text=True),
            r'name="network_diagnostics_share_status" value="1" id="fed-diagnostic-share" checked',
        )

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

    def test_rendezvous_relay_requires_explicit_peer_policy(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "relay-peer",
                "label": "Relay Peer",
                "base_url": "https://relay.example.test",
                "enabled": "1",
                "_rendezvous_relay_policy_form": "1",
                "rendezvous_relay_enabled": "1",
                "policy_json": '{"rendezvous_relay":{"future_option":"keep"}}',
            },
            follow_redirects=False,
        )

        self.assertEqual(302, response.status_code)
        peer = FederationStore(self.root).get_peer("relay-peer")
        self.assertEqual(
            {"enabled": True, "future_option": "keep"},
            peer["policy"]["rendezvous_relay"],
        )

        editor = self.client.get(
            "/admin/federation?view=peers&edit_peer=relay-peer"
        )
        body = editor.get_data(as_text=True)
        self.assertIn("Diesen Peer ausdrücklich als Rendezvous-Relay verwenden", body)
        self.assertIn('name="rendezvous_relay_enabled" value="1" checked', body)
        self.assertIn("Relay freigegeben", body)

    def test_rendezvous_relay_can_be_revoked_without_losing_policy(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "relay-peer",
                "label": "Relay Peer",
                "base_url": "https://relay.example.test",
                "enabled": "1",
                "_rendezvous_relay_policy_form": "1",
                "policy_json": '{"chat":{"send":true},"rendezvous_relay":{"enabled":true,"future_option":"keep"}}',
            },
            follow_redirects=False,
        )

        self.assertEqual(302, response.status_code)
        peer = FederationStore(self.root).get_peer("relay-peer")
        self.assertEqual({"send": True}, peer["policy"]["chat"])
        self.assertEqual(
            {"enabled": False, "future_option": "keep"},
            peer["policy"]["rendezvous_relay"],
        )

        body = self.client.get("/admin/federation?view=peers").get_data(as_text=True)
        self.assertIn("Relay aus", body)
        self.assertNotIn('name="rendezvous_relay_enabled" value="1" checked', body)

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

    def test_pairing_code_issue_is_no_store_and_does_not_grant_access(self):
        with app.app_context():
            identity = FederationIdentity(self.root / "remote-identity").public_identity()
        FederationStore(self.root).save_peer(
            "office-b", "Office B", "https://office-b.example.test", "", enabled=False,
        )
        FederationTrustStore(self.root).remember(
            "office-b", "DE", identity["fingerprint"], "qr", identity["public_key"],
        )

        response = self.client.post(
            "/admin/federation/peer-discovery/pairing/office-b/issue"
        )
        body = response.get_data(as_text=True)
        match = re.search(r"[2-9A-HJ-NP-Z]{4}(?:-[2-9A-HJ-NP-Z]{4}){2}", body)

        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual("no-referrer", response.headers["Referrer-Policy"])
        self.assertIsNotNone(match)
        self.assertNotIn("token_hash", body)
        self.assertEqual("pending", FederationPairingTokens(
            self.root, app.config["SECRET_KEY"]
        ).status("office-b")["status"])
        peer = FederationStore(self.root).get_peer("office-b")
        self.assertFalse(peer["enabled"])
        self.assertFalse(peer["has_token"])
        self.assertIsNone(FederationTrustStore(self.root).get_trust("office-b"))
        with sqlite3.connect(FederationStore(self.root).path) as db:
            token_hash = db.execute(
                "SELECT token_hash FROM federation_pairing_token WHERE peer_id=?",
                ("office-b",),
            ).fetchone()[0]
        self.assertNotIn(match.group(0).replace("-", ""), token_hash)

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

    def _remember_pairing_peer(self):
        with app.app_context():
            identity = FederationIdentity(self.root / "remote-identity").public_identity()
        FederationStore(self.root).save_peer(
            "office-b", "Office B", "https://office-b.example.test", "", enabled=False,
        )
        FederationTrustStore(self.root).remember(
            "office-b", "DE", identity["fingerprint"], "qr", identity["public_key"],
        )

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

    def test_claimant_error_does_not_echo_code(self):
        self._remember_pairing_peer()
        with patch(
            "app.federation_peer_admin.claim_pairing_code",
            side_effect=PairingClientError("Pairing-Code ist ungültig, abgelaufen oder bereits verwendet."),
        ):
            response = self.client.post(
                "/admin/federation/peer-discovery/pairing/office-b/claim",
                data={"code": "2345-6789-ABCD", "confirm_identity": "1"},
                follow_redirects=True,
            )
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("ungültig, abgelaufen oder bereits verwendet", body)
        self.assertNotIn("2345-6789-ABCD", body)

    def test_claimant_remote_status_is_read_only(self):
        self._remember_pairing_peer()
        with patch(
            "app.federation_peer_admin.remote_pairing_status",
            return_value={"peer_id": "local-peer", "status": "claimed"},
        ) as status:
            response = self.client.post(
                "/admin/federation/peer-discovery/pairing/office-b/remote-status",
                follow_redirects=True,
            )

        self.assertEqual(200, response.status_code)
        self.assertIn("Pairing-Status auf der Gegenstelle: beansprucht", response.get_data(as_text=True))
        status.assert_called_once()
        peer = FederationStore(self.root).get_peer("office-b")
        self.assertFalse(peer["enabled"])
        self.assertFalse(peer["has_token"])
        self.assertIsNone(FederationTrustStore(self.root).get_trust("office-b"))

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

        with patch("app.federation_discovery_service.fetch_discovery_profile_auto", return_value=profile):
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
