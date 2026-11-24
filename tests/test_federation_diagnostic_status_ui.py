"""Administrator and shared federation summaries for synthetic local diagnostics."""

from __future__ import annotations

import ipaddress
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app import app
from app.federation_store import FederationStore
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationDiagnosticStatusUiTests(FederationAdminUiFixture, unittest.TestCase):
    def page(self, path="/admin/federation?view=peers", client=None):
        with patch("app.federation_diagnostics._connect_pinned",
                   side_effect=AssertionError("Viewing a diagnostic must not contact a peer")) as transport:
            reply = (client or self.client).get(path)
        self.assertEqual(200, reply.status_code)
        transport.assert_not_called()
        return BeautifulSoup(reply.data, "html.parser")

    def row(self, document, peer_id):
        form = document.select_one(f'form[action="/admin/federation/peers/{peer_id}/diagnostics/probe"]')
        self.assertIsNotNone(form)
        return form.find_parent("tr")

    def record(self, store, peer_id, category, http="unknown", checked=None, *, direction="outbound"):
        return store.record_network_diagnostic(
            peer_id, direction=direction, source="local" if direction == "outbound" else peer_id,
            status="failure", category=category, checked_at=checked,
            detail={"stages": {"http": http, "authentication": "authenticated_but_forbidden" if http == "403" else "unknown"}},
        )

    def test_current_and_history_distinguish_http_response_from_tcp_timeout_without_claiming_reverse_path(self):
        store = FederationStore(self.root)
        store.save_peer("office", "Office B", "https://b.example.invalid/office", "", enabled=True)
        store.save_peer("stale", "Past result", "https://old.example.invalid", "", enabled=True)
        store.save_peer("not-run", "Local config", "https://config.example.invalid", "", enabled=True)
        now = int(time.time())
        self.record(store, "office", "tcp_timeout", checked=now - 25)
        self.record(store, "office", "permission", "403", now)
        self.record(store, "office", "invalid_request", "invalid_probe", now, direction="inbound")
        self.record(store, "stale", "http", "404", now - 301)
        self.record(store, "not-run", "configuration", checked=now)
        page = self.page()
        current = self.row(page, "office")
        body = current.get_text(" ", strip=True)
        self.assertIn("Server → konfigurierte Peer-Adresse: HTTP-Antwort erhalten (403); Peer nicht verifiziert", body)
        self.assertIn("Eingehende Prüfung nicht bestätigt", body)
        self.assertIn("Antwort verweigert; Identität nicht bestätigt", body)
        self.assertNotIn("nicht erreichbar", body)
        self.assertIn("Auf der Gegenstelle Zugriffsregeln und Reverse-Proxy prüfen",
                      current.select_one("[data-outbound-next-step]").get_text())
        outgoing = next(details for details in current.select("details")
                        if details.summary and details.summary.get_text(strip=True) == "Letzte Serverprüfungen")
        self.assertIn("HTTP-Antwort erhalten (403); Peer nicht verifiziert", outgoing.get_text())
        self.assertIn("Keine HTTP-Antwort nachgewiesen", outgoing.get_text())
        old = self.row(page, "stale")
        self.assertIn("konfigurierte Peer-Adresse: veraltet", old.get_text(" ", strip=True))
        self.assertIsNone(old.select_one("[data-outbound-next-step]"))
        self.assertIn("HTTP-Antwort erhalten (404)", old.find("details").get_text())
        self.assertIn("konfigurierte Peer-Adresse: Prüfung nicht bestätigt", self.row(page, "not-run").get_text(" ", strip=True))
        self.assertEqual(2, len(store.network_diagnostic_history("office", direction="outbound")))

    def test_post_echoes_bounded_http_evidence_and_the_next_step_through_a_real_csrf_protected_form(self):
        store = FederationStore(self.root)
        target = "http://other.example.invalid/base"
        with app.app_context():
            store.save_peer("other", "Other Office", target, "synthetic-unique-peer-token", enabled=True)
        path = "/admin/federation/peers/other/diagnostics/probe"
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "en"
        form = self.page().select_one(f'form[action="{path}"]')
        token = form.select_one('input[name="_csrf_token"]')["value"]
        app.config["TEST_CSRF_PROTECTION"] = True

        address = ipaddress.ip_address("192.0.2.30")
        connection = Mock()
        connection.getresponse.return_value = Mock(status=404)
        with (
            patch("app.federation_diagnostics.resolve_pinned_endpoint",
                  return_value=(target, urlsplit(target), 80, [address])),
            patch("app.federation_diagnostics._connect_pinned", return_value=(address, Mock())) as connect,
            patch("app.federation_diagnostics.http.client.HTTPConnection", return_value=connection),
            patch("app.federation_diagnostics.local_peer_id", return_value="example-local"),
        ):
            self.assertEqual(403, self.client.post(path).status_code)
            connect.assert_not_called()
            reply = self.client.post(path, data={"_csrf_token": token}, follow_redirects=True)
            self.assertEqual(200, reply.status_code)
            connect.assert_called_once()
            self.assertEqual(("POST", "/base/federation/v1/diagnostics/probe"), connection.request.call_args.args)
        record = store.network_diagnostic("other", direction="outbound")
        self.assertEqual("failure", record["status"])
        self.assertEqual("404", record["detail"]["stages"]["http"])
        page = BeautifulSoup(reply.data, "html.parser")
        flash = next(node.get_text(" ", strip=True) for node in page.select(".alert[role=status]")
                     if node.get_text(strip=True).startswith("Outbound check failed:"))
        self.assertIn("Outbound check failed: HTTP response received (404); peer not verified", flash)
        self.assertIn("Check the saved base address and forwarding to the federation endpoint", flash)
        self.assertIn("Run the check on the other instance for the reverse direction.", flash)
        self.assertNotIn("Outbound check succeeded:", flash)
        self.assertIsNone(store.network_diagnostic("other", direction="inbound", source="other"))

    def test_opted_in_shared_status_stays_coarse_even_when_http_answered(self):
        store = FederationStore(self.root)
        share = {"network_diagnostics": {"share_status_with_users": True}}
        store.save_peer("shared", "Shared Office", "https://private.example.invalid", "", share, True)
        self.record(store, "shared", "authentication", "200")
        self.record(store, "shared", "invalid_request", "invalid_probe", direction="inbound")
        member = app.test_client()
        login = member.post("/auth/login", data={
            "username": "federation-ui-user", "password": "federation-user-password",
        })
        self.assertLess(login.status_code, 400)
        page = self.page("/federation/diagnostics", member)
        row = next(node for node in page.select("tr") if node.get_text(strip=True).startswith("Shared Office"))
        self.assertEqual(["Prüfung nicht bestätigt", "Prüfung nicht bestätigt"],
                         [cell.get_text(strip=True) for cell in row.select("td")])
        self.assertNotIn("private.example", page.get_text())
        self.assertNotIn("200", row.get_text())
        self.assertNotIn("authentication", row.get_text())
        self.assertEqual(403, member.get("/admin/federation?view=peers").status_code)
