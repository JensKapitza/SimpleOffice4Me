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
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from app import app
from app import db as database
from app.federation_catalog import FederationCatalog
from app.federation_identity import FederationIdentity
from app.federation_moderation import FederationModerationStore
from app.federation_pairing import FederationPairingTokens
from app.federation_pairing_client import PairingClientError
from app.federation_qr import encode_peer
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


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


class FederationAdminUiTests(FederationAdminUiFixture, unittest.TestCase):
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

    def test_remote_file_actions_keep_peer_scope_and_search_separate_from_clicked_file(self):
        store = FederationStore(self.root)
        catalog = FederationCatalog(self.root)
        for peer_id, file_name, digest in (
            ("west", "West March + Q2.pdf", "a" * 64),
            ("east", "East March + Q2.pdf", "b" * 64),
        ):
            store.save_peer(peer_id, peer_id.title(), f"https://{peer_id}.example", "")
            catalog.begin_index(peer_id, "g1")
            catalog.ingest(peer_id, "g1", [{
                "document_id": "shared-id", "blob_hash": digest, "path": file_name, "size": 10,
            }])
            catalog.finish_index(peer_id, "g1")
        catalog.ingest("west", "g1", [{
            "document_id": "other", "blob_hash": "c" * 64, "path": "Unrelated.pdf", "size": 5,
        }])
        query = "March + Q2"
        path = "/admin/federation"
        action_url = f"{path}/remote/action"

        def read_action(action, *, scope="", peer="west"):
            response = self.client.get(path, query_string={"view": "files", "remote_q": query, "remote_peer": scope})
            self.assertEqual(200, response.status_code)
            page = BeautifulSoup(response.get_data(as_text=True), "html.parser")
            for form in page.select(f'form[action="{action_url}"]'):
                fields = {element["name"]: element.get("value", "") for element in form.select("input[name]")}
                if (
                    fields.get("peer_id") == peer
                    and fields.get("remote_document_id") == "shared-id"
                    and fields.get("action") == action
                ):
                    return fields
            self.fail(f"Missing {action} form for {peer!r} with scope {scope!r}")

        for action, priority in (("priority", "9"), ("download", "5")):
            with self.subTest(action=action):
                fields = read_action(action)
                fields["priority"] = priority
                result = self.client.post(action_url, data=fields)
                self.assertEqual(302, result.status_code)
                self.assertEqual(path, urlsplit(result.location).path)
                self.assertEqual({"view": ["files"], "remote_q": [query]}, parse_qs(urlsplit(result.location).query))
                html = self.client.get(result.location).get_data(as_text=True)
                self.assertIn("West March + Q2.pdf", html)
                self.assertIn("East March + Q2.pdf", html)
                self.assertNotIn("Unrelated.pdf", html)

        self.assertEqual(9, catalog.get_remote("west", "shared-id")["file_priority"])
        self.assertEqual(0, catalog.get_remote("east", "shared-id")["file_priority"])
        self.assertEqual(
            [("west", "shared-id", 5)],
            [(row["peer_id"], row["remote_document_id"], row["transfer_priority"]) for row in catalog.queue()],
        )

        for scope in ("", "east"):
            with self.subTest(rejected_scope=scope):
                failure_data = read_action("priority", scope=scope, peer="east")
                failure_data["priority"] = "not-a-number"
                failure = self.client.post(action_url, data=failure_data)
                expected = {"view": ["files"], "remote_q": [query]}
                if scope:
                    expected["remote_peer"] = [scope]
                self.assertEqual(expected, parse_qs(urlsplit(failure.location).query))
                html = self.client.get(failure.location).get_data(as_text=True)
                self.assertIn("Remote-Aktion fehlgeschlagen", html)
                self.assertIn("East March + Q2.pdf", html)
                self.assertEqual(not scope, "West March + Q2.pdf" in html)
                self.assertEqual(0, catalog.get_remote("east", "shared-id")["file_priority"])

        legacy = read_action("priority")
        legacy.pop("remote_peer_filter")
        result = self.client.post(action_url, data=legacy)
        self.assertEqual(["west"], parse_qs(urlsplit(result.location).query)["remote_peer"])

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
        self.assertIn(
            "Eingehende authentifizierte Anfrage von Peer → Server (kein aktiver Rücktest): erreichbar",
            body,
        )
        self.assertIn("zuletzt erfolgreich", body)

    def test_signed_network_check_labels_and_feedback_only_describe_outbound_route(self):
        store = FederationStore(self.root)
        store.save_peer("active-peer", "Active Peer", "https://active.example", "", enabled=True)
        store.save_peer("disabled-peer", "Disabled Peer", "https://disabled.example", "", enabled=False)
        expected = {
            "de": (
                "Ausgehend signiert prüfen",
                "Ausgehende Prüfung erfolgreich: Peer authentifiziert, 17 ms.",
                "Ausgehende Prüfung fehlgeschlagen:",
                "Gegenrichtung auf der anderen Instanz prüfen.",
            ),
            "en": (
                "Run signed outbound check",
                "Outbound check succeeded: peer authenticated, 17 ms.",
                "Outbound check failed:",
                "Run the check on the other instance for the reverse direction.",
            ),
        }
        for language, (label, success, failure, reverse) in expected.items():
            with self.subTest(language=language), self.client.session_transaction() as session:
                session["simpleoffice_language"] = language
            path = "/admin/federation/peers/active-peer/diagnostics/probe"
            with self.subTest(language=language):
                page = self.client.get("/admin/federation?view=peers").get_data(as_text=True)
                active = page.split(f'action="{path}">', 1)[1].split("</form>", 1)[0]
                disabled = page.split('action="/admin/federation/peers/disabled-peer/diagnostics/probe">', 1)[1]
                disabled = disabled.split("</form>", 1)[0]
                self.assertIn(label, active)
                self.assertNotIn("disabled", active)
                self.assertIn(label, disabled)
                self.assertIn("disabled", disabled)
                self.assertNotIn("Gegenseitig prüfen", page)
                self.assertNotIn("Check mutually", page)
                with patch("app.federation_admin.run_network_diagnostic", side_effect=[
                    {"status": "success", "latency_ms": 17},
                    {"status": "failure", "category": "policy"},
                ]) as probe:
                    response = self.client.post(path, follow_redirects=True)
                    probe.assert_called_once_with(str(self.root), "active-peer")
                    body = response.get_data(as_text=True)
                    self.assertIn(f"{success} {reverse}", body)
                    probe.reset_mock()
                    response = self.client.post(path, follow_redirects=True)
                    probe.assert_called_once_with(str(self.root), "active-peer")
                    body = response.get_data(as_text=True)
                    self.assertIn(failure, body)
                    self.assertNotIn(success, body)

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
        self.assertIn("Benutzerbestätigung (aktuell): Seite geöffnet", body)
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

    def test_changed_peer_address_does_not_reuse_outbound_success_in_either_view(self):
        store = FederationStore(self.root)
        policy = {"network_diagnostics": {"share_status_with_users": True}}
        store.save_peer("visible-peer", "Visible Office", "https://old.example", "", policy, True)
        for direction, source in (("outbound", "local"), ("inbound", "visible-peer")):
            store.record_network_diagnostic("visible-peer", direction=direction, source=source, status="success")
        user = app.test_client()
        result = user.post("/auth/login", data={
            "username": "federation-ui-user", "password": "federation-user-password",
        })
        self.assertLess(result.status_code, 400)

        def assert_status(outbound):
            admin_body = self.client.get("/admin/federation?view=peers").get_data(as_text=True)
            self.assertIn(f"Server → konfigurierte Peer-Adresse: {outbound}", admin_body)
            self.assertIn("(kein aktiver Rücktest): erreichbar", admin_body)
            user_body = user.get("/federation/diagnostics").get_data(as_text=True)
            self.assertRegex(user_body, rf"Visible Office</th>\s*<td>{outbound}</td>\s*<td>erreichbar</td>")
            self.assertNotIn(".example", user_body.split("Visible Office</th>", 1)[1].split("</tr>", 1)[0])

        assert_status("erreichbar")
        saved = self.client.post("/admin/federation/peers", data={
            "peer_id": "visible-peer", "label": "Visible Office", "base_url": "https://new.example",
            "policy_json": json.dumps(policy), "enabled": "1",
        })
        self.assertEqual(302, saved.status_code)
        assert_status("unbekannt")
        store.record_network_diagnostic("visible-peer", direction="outbound", source="local", status="success")
        assert_status("erreichbar")

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

    def test_mail_case_policy_defaults_to_deny_in_peer_form(self):
        response = self.client.get("/admin/federation?view=peers")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Mail-Vorgang-Federation", body)
        self.assertNotIn('name="mail_cases_send" value="1" checked', body)
        self.assertNotIn('name="mail_cases_receive" value="1" checked', body)
        self.assertNotIn('name="mail_cases_auto_accept" value="1" checked', body)

    def test_chat_rules_save_returns_to_edited_peer_with_local_feedback(self):
        from bs4 import BeautifulSoup

        store = FederationStore(self.root)
        chat_page = "/admin/chat-policies/"
        office_a_policy = {"documents": {"receive": True}}
        office_b_policy = {"documents": {"receive": False}, "inventory": {"receive": True}}
        with app.app_context():
            store.save_peer("office-a", "Office A", "https://office-a.example", "", office_a_policy)
            store.save_peer(
                "office-b", "Office B", "https://office-b.example", "peer-token-fixture", office_b_policy,
            )
        post_path = "/admin/federation/peers/office-b/chat-policy"
        response = self.client.get(chat_page)
        self.assertEqual(200, response.status_code)
        page = BeautifulSoup(response.get_data(as_text=True), "html.parser")
        form = page.find("form", action=post_path)
        data = {item["name"]: item.get("value", "") for item in form.select('input[type="hidden"]')}
        data.update(chat_send="1", chat_poll_send="1")

        result = self.client.post(post_path, data=data)
        self.assertEqual(f"{chat_page}#chat-peer-office-b", result.location)
        returned = BeautifulSoup(self.client.get(result.location).get_data(as_text=True), "html.parser")
        office_a = returned.find("section", id="chat-peer-office-a")
        office_b = returned.find("section", id="chat-peer-office-b")
        self.assertIn("Chat-Regeln für Office B gespeichert.", office_b.select_one('[role="status"]').get_text())
        self.assertIsNone(office_a.select_one('[role="status"]'))
        self.assertTrue(office_b.select_one('[name="chat_send"]').has_attr("checked"))
        self.assertTrue(office_b.select_one('[name="chat_poll_send"]').has_attr("checked"))
        self.assertFalse(office_b.select_one('[name="chat_receive"]').has_attr("checked"))
        self.assertFalse(office_a.select_one('[name="chat_send"]').has_attr("checked"))
        self.assertEqual(office_a_policy, store.get_peer("office-a")["policy"])
        saved_b = store.get_peer("office-b")["policy"]
        self.assertEqual(office_b_policy, {key: saved_b[key] for key in office_b_policy})
        with app.app_context():
            self.assertEqual("peer-token-fixture", store.peer_token("office-b"))
        self.assertNotIn("peer-token-fixture", returned.get_text())

        with patch.object(FederationStore, "save_peer", side_effect=ValueError("Lokale Test-Ablehnung")):
            result = self.client.post(post_path, data={"chat_receive": "1"})
        self.assertEqual(f"{chat_page}#chat-peer-office-b", result.location)
        rejected = BeautifulSoup(self.client.get(result.location).get_data(as_text=True), "html.parser")
        office_b = rejected.find("section", id="chat-peer-office-b")
        self.assertIn("Chat-Regeln konnten nicht gespeichert werden", office_b.select_one('[role="status"]').get_text())
        self.assertEqual(saved_b, store.get_peer("office-b")["policy"])

        missing = self.client.post("/admin/federation/peers/no-such-peer/chat-policy")
        self.assertEqual(chat_page, missing.location)
        self.assertIn("Chat-Regeln konnten nicht gespeichert werden", self.client.get(missing.location).get_data(as_text=True))

        self.client.post(post_path, data=data)
        store.delete_peer("office-b")
        refreshed = BeautifulSoup(self.client.get(chat_page).get_data(as_text=True), "html.parser")
        self.assertIsNone(refreshed.find("section", id="chat-peer-office-b"))
        self.assertIn("Chat-Regeln für Office B gespeichert.", refreshed.select_one('main > [role="status"]').get_text())

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

    def test_peer_discovery_renders_sound_announce_and_listen(self):
        with patch(
            "app.federation_peer_admin._connect_addresses",
            return_value=["192.0.2.10"],
        ):
            response = self.client.get("/admin/federation/peer-discovery")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("data-fed-sound-announce", body)
        self.assertIn('data-endpoint="http://192.0.2.10:', body)
        self.assertIn("data-fed-sound-listen", body)
        self.assertIn('id="direct-endpoint"', body)
        for script in ("js/acoustic_fec.js", "js/acoustic_codec.js", "js/federation_sound.js"):
            self.assertIn(script, body)

    def test_setup_hints_separate_missing_peer_token_from_expired_pairing_code(self):
        store = FederationStore(self.root)
        trust = FederationTrustStore(self.root)
        old_time = int(time.time()) - 3600
        old_pairing = FederationPairingTokens(self.root, app.config["SECRET_KEY"], clock=lambda: old_time)
        with app.app_context():
            store.save_peer("configured", "Configured Office", "https://configured.example", "fixture-token")
            store.save_peer("inactive", "Inactive Office", "https://inactive.example", "", enabled=False)
            store.save_peer("blocked", "Blocked Office", "https://blocked.example", "")
        for peer_id in ("configured", "inactive", "blocked"):
            trust.remember(peer_id, "DE", "", "qr")
        FederationModerationStore(self.root).ban("blocked", "fixture", actor="admin")
        issued = old_pairing.issue("configured")
        old_pairing.claim(issued["code"], "configured")
        path = "/admin/federation/peer-discovery"

        before = self.client.get(path).get_data(as_text=True)
        self.assertNotIn("Einrichtung unvollständig", before)
        self.assertNotIn('id="federation-setup-hints"', before)
        self.assertIn("abgelaufen", before)

        store.save_peer("missing-token", "Office <b>East</b>", "https://missing.example", "")
        trust.remember("missing-token", "DE", "", "qr")
        pending = old_pairing.issue("missing-token")
        for lang, heading, link, expired in (
            ("de", "Fehlende Peer-Token", "Peer-Token einrichten", "abgelaufen"),
            ("en", "Missing peer tokens", "Set up peer token", "expired"),
        ):
            with self.subTest(language=lang):
                with self.client.session_transaction() as session:
                    session["simpleoffice_language"] = lang
                response = self.client.get(path)
                self.assertEqual(200, response.status_code)
                body = response.get_data(as_text=True)
                notice = body.split('id="federation-setup-hints"', 1)[1].split("</div>", 1)[0]
                self.assertIn(heading, notice)
                self.assertIn(link, notice)
                self.assertIn("Office &lt;b&gt;East&lt;/b&gt;", notice)
                self.assertIn("view=peers&amp;edit_peer=missing-token#peer-token", notice)
                for label in ("Configured Office", "Inactive Office", "Blocked Office"):
                    self.assertNotIn(label, notice)
                self.assertIn(expired, body)
                for secret in ("fixture-token", issued["code"], pending["code"]):
                    self.assertNotIn(secret, body)

        tokens = FederationPairingTokens(self.root, app.config["SECRET_KEY"])
        self.assertEqual(old_time, tokens.status("configured")["claimed_at"])
        self.assertEqual("expired", tokens.status("missing-token")["status"])
        self.assertTrue(store.get_peer("configured")["enabled"])
        self.assertFalse(store.get_peer("missing-token")["has_token"])

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
        body = response.get_data(as_text=True)
        self.assertIn("Pairing-Status auf der Gegenstelle: beansprucht", body)
        self.assertIn("Geprüft: diese Instanz → Gegenstelle.", body)
        self.assertIn("Rückrichtung nicht geprüft", body)
        status.assert_called_once()
        peer = FederationStore(self.root).get_peer("office-b")
        self.assertFalse(peer["enabled"])
        self.assertFalse(peer["has_token"])
        self.assertIsNone(FederationTrustStore(self.root).get_trust("office-b"))

    def test_remote_status_distinguishes_missing_code_from_a_refused_identity_in_english(self):
        self._remember_pairing_peer()
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "en"
        path = "/admin/federation/peer-discovery/pairing/office-b/remote-status"
        with patch(
            "app.federation_peer_admin.remote_pairing_status",
            side_effect=[
                {"peer_id": "local-peer", "status": "missing"},
                PairingClientError("internal-only diagnostic", reason="identity_rejected"),
            ],
        ):
            missing = self.client.post(path, follow_redirects=True).get_data(as_text=True)
            rejected = self.client.post(path, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Pairing status on the counterpart: missing.", missing)
        self.assertIn("Reverse direction not checked", missing)
        self.assertIn("did not accept this instance", rejected)
        self.assertIn("check the clocks on both sides", rejected)
        self.assertNotIn("internal-only diagnostic", rejected)

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

    def test_qr_import_verified_ceremony_rebinds_identity(self):
        from app.federation_identity import FederationIdentity

        extra = tempfile.TemporaryDirectory()
        self.addCleanup(extra.cleanup)
        with app.app_context():
            forged = FederationIdentity(Path(extra.name)).public_identity()
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
            data={"payload": payload, "verify_in_person": "1"}, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        with app.app_context():
            identity = FederationTrustStore(self.root).identity("office-b")
            self.assertEqual(forged["fingerprint"], identity["fingerprint"])

    def test_direct_discovery_distinguishes_reached_address_from_saved_target(self):
        store = FederationStore(self.root)
        with app.app_context():
            store.save_peer(
                "office-b", "Trusted Office B", "https://configured.example.test", "fixture-token",
                {"documents": {"send": True}}, True,
            )
        profile = {"peer_id": "office-b", "base_url": "https://advertised.example.test"}
        for lang, reached, saved in (
            ("de", "Discovery-Antwort von:", "Gespeichertes Verbindungsziel:"),
            ("en", "Discovery response from:", "Saved connection target:"),
        ):
            with self.subTest(language=lang):
                with self.client.session_transaction() as session:
                    session["simpleoffice_language"] = lang
                with patch(
                    "app.federation_discovery_endpoint.fetch_discovery_profile",
                    side_effect=[ConnectionRefusedError, profile],
                ) as fetch:
                    response = self.client.post(
                        "/admin/federation/peer-discovery/discover/direct",
                        data={"endpoint": "reachable.example.test/office"},
                        follow_redirects=True,
                    )
                self.assertEqual(200, response.status_code)
                self.assertEqual(
                    ["https://reachable.example.test/office", "http://reachable.example.test/office"],
                    [call.args[0] for call in fetch.call_args_list],
                )
                body = response.get_data(as_text=True)
                self.assertIn(f"{reached} http://reachable.example.test/office", body)
                self.assertIn(f"{saved} https://configured.example.test", body)
                self.assertNotIn("advertised.example.test", body)
                self.assertNotIn("fixture-token", body)
                peer = store.get_peer("office-b")
                self.assertEqual("https://configured.example.test", peer["base_url"])
                self.assertTrue(peer["enabled"])
                self.assertEqual({"documents": {"send": True}}, peer["policy"])
                with app.app_context():
                    self.assertEqual("fixture-token", store.peer_token("office-b"))

    def test_direct_discovery_has_no_mismatch_for_same_address_or_new_peer(self):
        store = FederationStore(self.root)
        store.save_peer("existing", "Existing", "https://same.example.test:443", "", enabled=True)
        for peer_id in ("existing", "fresh"):
            with self.subTest(peer_id=peer_id), patch(
                "app.federation_discovery_endpoint.fetch_discovery_profile",
                return_value={"peer_id": peer_id, "base_url": "https://advertised.example.test"},
            ):
                response = self.client.post(
                    "/admin/federation/peer-discovery/discover/direct",
                    data={"endpoint": "https://same.example.test"},
                    follow_redirects=True,
                )
                self.assertEqual(200, response.status_code)
                body = response.get_data(as_text=True)
                self.assertIn(f"Peer-Profil für {peer_id} gefunden", body)
                self.assertNotIn("Gespeichertes Verbindungsziel:", body)
        self.assertTrue(store.get_peer("existing")["enabled"])
        self.assertFalse(store.get_peer("fresh")["enabled"])

    def test_direct_discovery_does_not_echo_or_replace_an_invalid_saved_target(self):
        store = FederationStore(self.root)
        legacy = "ftp://legacy.example.test/share"
        store.save_peer("legacy", "Legacy", legacy, "", enabled=False)
        with patch(
            "app.federation_discovery_endpoint.fetch_discovery_profile",
            return_value={"peer_id": "legacy", "base_url": "https://advertised.example.test"},
        ):
            response = self.client.post(
                "/admin/federation/peer-discovery/discover/direct",
                data={"endpoint": "https://reachable.example.test"},
            )
        self.assertEqual(302, response.status_code)
        with self.client.session_transaction() as session:
            messages = [message for _, message in session["_flashes"]]
        self.assertTrue(any("Bitte unter „Verbindung einrichten“ prüfen." in message for message in messages))
        self.assertFalse(any(legacy in message for message in messages))
        self.assertEqual(legacy, store.get_peer("legacy")["base_url"])

    def test_failed_peer_edit_returns_to_saved_editor_without_leaking_submission(self):
        store = FederationStore(self.root)
        with app.app_context():
            store.save_peer(
                "office-b", "Office B", "https://saved.example", "saved-token",
                {"documents": {"send": False, "receive": True}}, enabled=False,
            )
        for address, policy in (("", "{}"), ("https://unsaved.example", "[]")):
            with self.subTest(address=address, policy=policy):
                failure = self.client.post("/admin/federation/peers", data={
                    "peer_id": "office-b", "label": "Unsaved name", "token": "unsaved-token",
                    "base_url": address, "policy_json": policy, "enabled": "1", "_editing_peer": "1",
                })
                self.assertEqual(302, failure.status_code)
                self.assertEqual(
                    "/admin/federation?view=peers&edit_peer=office-b#peer-form", failure.location,
                )
                page = self.client.get(failure.location).get_data(as_text=True)
                self.assertIn("Peer konnte nicht gespeichert werden", page)
                self.assertIn("Peer bearbeiten", page)
                self.assertIn('name="_editing_peer" value="1"', page)
                self.assertIn('id="peer-url" name="base_url" value="https://saved.example"', page)
                for value in ("unsaved-token", "saved-token", "unsaved.example", "Unsaved name"):
                    self.assertNotIn(value, page)
                peer = store.get_peer("office-b")
                self.assertFalse(peer["enabled"])
                self.assertEqual({"documents": {"send": False, "receive": True}}, peer["policy"])
                with app.app_context():
                    self.assertEqual("saved-token", store.peer_token("office-b"))

    def test_peer_save_redirect_treats_new_unknown_and_success_as_normal(self):
        store = FederationStore(self.root)
        store.save_peer("office-b", "Office B", "https://saved.example", "", enabled=False)
        add = self.client.get("/admin/federation?view=peers")
        self.assertNotIn('name="_editing_peer"', add.get_data(as_text=True))
        for peer_id, edit, policy in (
            ("new-peer", "", "[]"), ("unknown-peer", "1", "[]"),
            ("??", "1", "[]"), ("office-b?", "1", "[]"),
            ("office-b", "", "[]"), ("office-b", "1", "{}"),
        ):
            with self.subTest(peer_id=peer_id):
                response = self.client.post("/admin/federation/peers", data={
                    "peer_id": peer_id, "base_url": "https://new.example",
                    "policy_json": policy, "_editing_peer": edit,
                })
                self.assertEqual("/admin/federation?view=peers", response.location)
        self.assertEqual("https://new.example", store.get_peer("office-b")["base_url"])
        self.assertIsNone(store.get_peer("new-peer"))
        self.assertIsNone(store.get_peer("unknown-peer"))

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
