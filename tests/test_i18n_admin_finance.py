"""i18n batch 13c: admin/finance/site_visits pages render DE + EN.

Covers federation (overview/files/peers/transfers), moderation,
peer discovery, mini-services hub + network, sftp, software updates,
inventory, integration settings, OSM index, activity, logs, users,
v3 jobs/health/automation, extensions, chat policies, documents media,
network settings, printers, remote, audio output/streamer, connectivity
relay, firewall, litellm, network boot, telephony, s3 overlay, account
requests/invite, finance index and the site_visits cluster.

Static template strings follow the file-local ternary convention;
backend tokens, option values and stored labels stay German data.
Finance FinTS detail pages (balance/transaction/preview/discovery/sync
challenges), federation peer policy/pairing code and the site_visits
credentials POST page need deeper setup and stay parse-checked only.
Live browsers are NOT verified here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class AdminFinanceBatch13cTest(unittest.TestCase):
    password = "test-only-i18n-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = app.test_client()
        import re
        match = re.search(rb'name="_csrf_token" value="([^"]+)"',
                          self.client.get("/auth/login").data)
        assert match is not None
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password,
                  "_csrf_token": match.group(1).decode()},
        )

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def _get(self, url: str, language: str) -> str:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        return response.data.decode("utf-8")

    def test_federation_overview(self) -> None:
        en_body = self._get("/admin/federation", "en")
        self.assertIn("Connect SimpleOffice instances", en_body)
        self.assertIn("What do you want to do?", en_body)
        self.assertNotIn("SimpleOffice-Instanzen verbinden", en_body)
        de_body = self._get("/admin/federation", "de")
        self.assertIn("SimpleOffice-Instanzen verbinden", de_body)
        self.assertIn("Was m\u00f6chtest du tun?", de_body)

    def test_federation_files(self) -> None:
        en_body = self._get("/admin/federation?view=files", "en")
        self.assertIn("Local files", en_body)
        self.assertIn("Files on other servers", en_body)
        self.assertIn("Select file and then send", en_body)
        self.assertNotIn("Lokale Dateien", en_body)
        de_body = self._get("/admin/federation?view=files", "de")
        self.assertIn("Lokale Dateien", de_body)
        self.assertIn("Dateien auf anderen Servern", de_body)

    def test_federation_peers(self) -> None:
        en_body = self._get("/admin/federation?view=peers", "en")
        self.assertIn("Connected peers", en_body)
        self.assertIn("Add peer", en_body)
        self.assertNotIn("Verbundene Peers", en_body)
        de_body = self._get("/admin/federation?view=peers", "de")
        self.assertIn("Verbundene Peers", de_body)
        self.assertIn("Peer hinzuf\u00fcgen", de_body)

    def test_federation_transfers(self) -> None:
        en_body = self._get("/admin/federation?view=transfers", "en")
        self.assertIn("Download queue", en_body)
        self.assertIn("Process queue", en_body)
        self.assertNotIn("Download-Warteschlange", en_body)
        de_body = self._get("/admin/federation?view=transfers", "de")
        self.assertIn("Download-Warteschlange", de_body)
        self.assertIn("Queue abarbeiten", de_body)

    def test_federation_moderation(self) -> None:
        en_body = self._get("/admin/federation/moderation", "en")
        self.assertIn("Peer management", en_body)
        self.assertIn("Ban peer", en_body)
        self.assertNotIn("Peer-Verwaltung", en_body)
        de_body = self._get("/admin/federation/moderation", "de")
        self.assertIn("Peer-Verwaltung", de_body)
        self.assertIn("Peer sperren", de_body)

    def test_federation_peer_discovery(self) -> None:
        en_body = self._get("/admin/federation/peer-discovery", "en")
        self.assertIn("Scan network", en_body)
        self.assertNotIn("Netzwerk scannen", en_body)
        de_body = self._get("/admin/federation/peer-discovery", "de")
        self.assertIn("Netzwerk scannen", de_body)

    def test_mini_services_hub(self) -> None:
        en_body = self._get("/admin/mini-services", "en")
        self.assertIn("Manage local infrastructure", en_body)
        self.assertIn("Control services", en_body)
        self.assertNotIn("Lokale Infrastruktur", en_body)
        de_body = self._get("/admin/mini-services", "de")
        self.assertIn("Lokale Infrastruktur", de_body)
        self.assertIn("Dienste steuern", de_body)

    def test_mini_services_network(self) -> None:
        en_body = self._get("/admin/mini-services/network", "en")
        self.assertIn("DHCP and DNS services for small local networks", en_body)
        self.assertIn("Enable DHCP server", en_body)
        self.assertNotIn("DHCP- und DNS-Dienste", en_body)
        de_body = self._get("/admin/mini-services/network", "de")
        self.assertIn("DHCP- und DNS-Dienste", de_body)
        self.assertIn("DHCP-Server aktivieren", de_body)

    def test_sftp_service(self) -> None:
        en_body = self._get("/admin/mini-services/sftp", "en")
        self.assertIn("The integrated Paramiko server speaks SFTP", en_body)
        self.assertIn("Operating mode", en_body)
        self.assertNotIn("Betriebsart", en_body)
        de_body = self._get("/admin/mini-services/sftp", "de")
        self.assertIn("Betriebsart", de_body)

    def test_software_updates(self) -> None:
        en_body = self._get("/admin/software", "en")
        self.assertIn("Cache git bundles", en_body)
        self.assertIn("Build self-deploy package", en_body)
        self.assertNotIn("Self-Deploy-Paket bauen", en_body)
        de_body = self._get("/admin/software", "de")
        self.assertIn("Self-Deploy-Paket bauen", de_body)
        self.assertIn("Lokaler Build", de_body)

    def test_inventory(self) -> None:
        en_body = self._get("/admin/inventory", "en")
        self.assertIn("Runtime inventory", en_body)
        self.assertIn("Host management", en_body)
        self.assertNotIn("System- und Laufzeitinventar", en_body)
        de_body = self._get("/admin/inventory", "de")
        self.assertIn("System- und Laufzeitinventar", de_body)

    def test_integration_settings(self) -> None:
        en_body = self._get("/admin/settings/integrations", "en")
        self.assertIn("Runtime values for external data sources", en_body)
        self.assertIn("Automatic error reports", en_body)
        self.assertIn("Save setting", en_body)
        self.assertNotIn("Laufzeitwerte f\u00fcr externe Datenquellen", en_body)
        de_body = self._get("/admin/settings/integrations", "de")
        self.assertIn("Laufzeitwerte f\u00fcr externe Datenquellen", de_body)
        self.assertIn("Einstellung speichern", de_body)

    def test_osm_address_index(self) -> None:
        en_body = self._get("/admin/osm-addresses", "en")
        self.assertIn("Local OpenStreetMap address index", en_body)
        self.assertNotIn("Lokaler OpenStreetMap-Adressindex", en_body)
        de_body = self._get("/admin/osm-addresses", "de")
        self.assertIn("Lokaler OpenStreetMap-Adressindex", de_body)

    def test_activity(self) -> None:
        url = "/admin/activity?target_type=contact&target_id=abc123"
        en_body = self._get(url, "en")
        self.assertIn("Activity", en_body)
        self.assertNotIn("Aktivit\u00e4t", en_body)
        de_body = self._get(url, "de")
        self.assertIn("Aktivit\u00e4t", de_body)

    def test_logs(self) -> None:
        en_body = self._get("/admin/logs", "en")
        self.assertIn("Audit and system log", en_body)
        self.assertIn("Application errors", en_body)
        self.assertNotIn("Audit und Systemprotokoll", en_body)
        de_body = self._get("/admin/logs", "de")
        self.assertIn("Audit und Systemprotokoll", de_body)

    def test_users(self) -> None:
        en_body = self._get("/admin/users", "en")
        self.assertIn("User administration", en_body)
        self.assertIn("Accounts", en_body)
        self.assertNotIn("Benutzerverwaltung", en_body)
        de_body = self._get("/admin/users", "de")
        self.assertIn("Benutzerverwaltung", de_body)
        self.assertIn("Konten", de_body)

    def test_v3_jobs(self) -> None:
        en_body = self._get("/admin/v3/jobs", "en")
        self.assertIn("Persistent 3.0 queue", en_body)
        self.assertNotIn("Persistente 3.0-Queue", en_body)
        de_body = self._get("/admin/v3/jobs", "de")
        self.assertIn("Persistente 3.0-Queue", de_body)

    def test_v3_health(self) -> None:
        # Capability v3.health is disabled by default: hint instead of checks.
        en_body = self._get("/admin/v3/health", "en")
        self.assertIn("Limited readiness and diagnostic checks", en_body)
        self.assertIn("is disabled.", en_body)
        self.assertNotIn("Begrenzte Readiness", en_body)
        de_body = self._get("/admin/v3/health", "de")
        self.assertIn("Begrenzte Readiness", de_body)
        self.assertIn("ist deaktiviert.", de_body)
        self.assertNotIn("Limited readiness and diagnostic checks", de_body)

    def test_v3_automation(self) -> None:
        # Capability v3.automation is disabled by default: hint instead of form.
        en_body = self._get("/automation-v3", "en")
        self.assertIn("Declarative rules without free code execution.", en_body)
        self.assertIn("is disabled.", en_body)
        self.assertNotIn("Deklarative Regeln", en_body)
        de_body = self._get("/automation-v3", "de")
        self.assertIn("Deklarative Regeln ohne freie Codeausf\u00fchrung.", de_body)
        self.assertIn("ist deaktiviert.", de_body)
        self.assertNotIn("Declarative rules without free code execution.", de_body)

    def test_v3_extensions(self) -> None:
        en_body = self._get("/admin/extensions-v3", "en")
        self.assertIn("Register external manifest", en_body)
        self.assertNotIn("Externes Manifest registrieren", en_body)
        de_body = self._get("/admin/extensions-v3", "de")
        self.assertIn("Externes Manifest registrieren", de_body)

    def test_chat_policies(self) -> None:
        en_body = self._get("/admin/chat-policies", "en")
        self.assertIn("Chat rules", en_body)
        self.assertIn("Define per federation partner", en_body)
        self.assertNotIn("Chat-Regeln", en_body)
        de_body = self._get("/admin/chat-policies", "de")
        self.assertIn("Chat-Regeln", de_body)

    def test_documents_media_settings(self) -> None:
        en_body = self._get("/admin/settings/documents-media", "en")
        self.assertIn("Preview resource limits", en_body)
        self.assertIn("Maximum size per upload request", en_body)
        self.assertNotIn("Vorschau-Ressourcengrenzen", en_body)
        de_body = self._get("/admin/settings/documents-media", "de")
        self.assertIn("Vorschau-Ressourcengrenzen", de_body)
        self.assertIn("Maximale Gr\u00f6\u00dfe je Upload-Anfrage (MiB)", de_body)
        self.assertNotIn("Preview resource limits", de_body)

    def test_network_settings(self) -> None:
        en_body = self._get("/admin/settings/network", "en")
        self.assertIn("WebDAV storage quota", en_body)
        self.assertNotIn("WebDAV-Speicherkontingent", en_body)
        de_body = self._get("/admin/settings/network", "de")
        self.assertIn("WebDAV-Speicherkontingent", de_body)
        self.assertNotIn("WebDAV storage quota", de_body)

    def test_printers(self) -> None:
        en_body = self._get("/admin/settings/printers", "en")
        self.assertIn("Printer management", en_body)
        self.assertNotIn("Druckerverwaltung", en_body)
        de_body = self._get("/admin/settings/printers", "de")
        self.assertIn("Druckerverwaltung", de_body)
        self.assertNotIn("Printer management", de_body)

    def test_remote(self) -> None:
        en_body = self._get("/admin/remote", "en")
        self.assertIn("Published applications", en_body)
        self.assertNotIn("Ver\u00f6ffentlichte Anwendungen", en_body)
        de_body = self._get("/admin/remote", "de")
        self.assertIn("Ver\u00f6ffentlichte Anwendungen", de_body)

    def test_audio_output(self) -> None:
        en_body = self._get("/admin/mini-services/audio/ui", "en")
        self.assertIn("Announcements and playback", en_body)
        self.assertNotIn("Durchsagen und Wiedergabe", en_body)
        de_body = self._get("/admin/mini-services/audio/ui", "de")
        self.assertIn("Durchsagen und Wiedergabe", de_body)

    def test_audio_streamer(self) -> None:
        en_body = self._get("/admin/mini-services/audio/streamer", "en")
        self.assertIn("Live audio streamer", en_body)
        self.assertNotIn("Live Audio-Streamer", en_body)
        de_body = self._get("/admin/mini-services/audio/streamer", "de")
        self.assertIn("Live Audio-Streamer", de_body)

    def test_connectivity_relay(self) -> None:
        en_body = self._get("/admin/mini-services/connectivity", "en")
        self.assertIn("STUN/TURN for NAT traversal", en_body)
        self.assertIn("No automatic firewall hole", en_body)
        self.assertNotIn("Kein automatisches Firewall-Loch", en_body)
        de_body = self._get("/admin/mini-services/connectivity", "de")
        self.assertIn("Kein automatisches Firewall-Loch", de_body)

    def test_firewall(self) -> None:
        en_body = self._get("/admin/mini-services/firewall", "en")
        self.assertIn("Safe test mode", en_body)
        self.assertIn("Known services", en_body)
        self.assertNotIn("Sicherer Testmodus", en_body)
        de_body = self._get("/admin/mini-services/firewall", "de")
        self.assertIn("Sicherer Testmodus", de_body)

    def test_litellm(self) -> None:
        en_body = self._get("/admin/mini-services/litellm", "en")
        self.assertIn("MCP credentials", en_body)
        self.assertIn("Operating mode", en_body)
        self.assertNotIn("Betriebsart", en_body)
        de_body = self._get("/admin/mini-services/litellm", "de")
        self.assertIn("Betriebsart", de_body)

    def test_network_boot(self) -> None:
        en_body = self._get("/admin/mini-services/network-boot", "en")
        self.assertIn("Network boot", en_body)
        self.assertIn("Boot profiles", en_body)
        self.assertNotIn("Netzwerkboot", en_body)
        de_body = self._get("/admin/mini-services/network-boot", "de")
        self.assertIn("Netzwerkboot", de_body)

    def test_telephony(self) -> None:
        en_body = self._get("/admin/mini-services/telephony", "en")
        self.assertIn("IP telephony", en_body)
        self.assertNotIn("IP-Telefonie", en_body)
        de_body = self._get("/admin/mini-services/telephony", "de")
        self.assertIn("IP-Telefonie", de_body)

    def test_s3_overlay(self) -> None:
        en_body = self._get("/admin/s3-overlay", "en")
        self.assertIn("S3 credentials for documents", en_body)
        self.assertIn("Create S3 credential", en_body)
        self.assertNotIn("S3-Zug\u00e4nge", en_body)
        de_body = self._get("/admin/s3-overlay", "de")
        self.assertIn("S3-Zug\u00e4nge", de_body)

    def test_account_requests(self) -> None:
        en_body = self._get("/auth/account-requests/admin", "en")
        self.assertIn("Account requests", en_body)
        self.assertIn("Open invitations", en_body)
        self.assertNotIn("Kontoantr\u00e4ge", en_body)
        de_body = self._get("/auth/account-requests/admin", "de")
        self.assertIn("Kontoantr\u00e4ge", de_body)

    def test_account_invite(self) -> None:
        en_body = self._get("/auth/account-requests/admin/invite", "en")
        self.assertIn("+ User", en_body)
        self.assertNotIn("+ Benutzer", en_body)
        de_body = self._get("/auth/account-requests/admin/invite", "de")
        self.assertIn("+ Benutzer", de_body)

    def test_finance_index(self) -> None:
        en_body = self._get("/documents/business/bookkeeping/finances", "en")
        self.assertIn("Finances", en_body)
        self.assertIn("Manage accounts and bank bookings centrally", en_body)
        self.assertIn("Import bank statement", en_body)
        self.assertNotIn("Konten und Bankbuchungen zentral verwalten", en_body)
        de_body = self._get("/documents/business/bookkeeping/finances", "de")
        self.assertIn("Finanzen", de_body)
        self.assertIn("Kontoauszug importieren", de_body)


class SiteVisitsBatch13cTest(unittest.TestCase):
    password = "test-only-i18n-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = app.test_client()
        import re
        match = re.search(rb'name="_csrf_token" value="([^"]+)"',
                          self.client.get("/auth/login").data)
        assert match is not None
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password,
                  "_csrf_token": match.group(1).decode()},
        )
        from app.site_visit_store import SiteVisitStore
        store = SiteVisitStore(self.root)
        visit = store.create("admin", {"title": "Sprachbesuch"})
        self.visit_id = visit["visit_id"]
        store.add_asset(self.visit_id, "admin", {"name": "Heizung"})
        record = store.get(self.visit_id, "admin")
        self.asset_id = record["assets"][0]["asset_id"]

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def _get(self, url: str, language: str) -> str:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        return response.data.decode("utf-8")

    def test_site_visits_index(self) -> None:
        en_body = self._get("/site-visits", "en")
        self.assertIn("Sprachbesuch", en_body)
        self.assertIn("Create new site visit", en_body)
        self.assertIn("Recently edited", en_body)
        self.assertNotIn("Neuen Ortstermin anlegen", en_body)
        de_body = self._get("/site-visits", "de")
        self.assertIn("Neuen Ortstermin anlegen", de_body)
        self.assertIn("Zuletzt bearbeitet", de_body)

    def test_site_visit_detail(self) -> None:
        url = f"/site-visits/{self.visit_id}"
        en_body = self._get(url, "en")
        self.assertIn("Sprachbesuch", en_body)
        self.assertIn("Created reports", en_body)
        self.assertIn("Change history", en_body)
        self.assertNotIn("Erstellte Berichte", en_body)
        de_body = self._get(url, "de")
        self.assertIn("Erstellte Berichte", de_body)
        self.assertIn("\u00c4nderungshistorie", de_body)

    def test_site_visit_report(self) -> None:
        url = f"/site-visits/{self.visit_id}/report"
        en_body = self._get(url, "en")
        self.assertIn("Sprachbesuch", en_body)
        self.assertIn("Report", en_body)
        self.assertNotIn("Bericht", en_body)
        de_body = self._get(url, "de")
        self.assertIn("Bericht", de_body)

    def test_site_visit_snapshot_diff(self) -> None:
        url = f"/site-visits/{self.visit_id}/snapshot-diff"
        en_body = self._get(url, "en")
        self.assertIn("Sprachbesuch", en_body)
        self.assertIn("Version comparison", en_body)
        self.assertNotIn("Versionsvergleich", en_body)
        de_body = self._get(url, "de")
        self.assertIn("Versionsvergleich", de_body)

    def test_site_visit_unassigned_findings(self) -> None:
        url = f"/site-visits/{self.visit_id}/findings/assignments"
        en_body = self._get(url, "en")
        self.assertIn("Findings without assignment", en_body)
        self.assertNotIn("Befunde ohne Zuordnung", en_body)
        de_body = self._get(url, "de")
        self.assertIn("Befunde ohne Zuordnung", de_body)

    def test_site_visit_label(self) -> None:
        url = f"/site-visits/{self.visit_id}/labels/{self.asset_id}"
        en_body = self._get(url, "en")
        self.assertIn("Heizung", en_body)
        self.assertIn("Device label", en_body)
        self.assertNotIn("Ger\u00e4teetikett", en_body)
        de_body = self._get(url, "de")
        self.assertIn("Ger\u00e4teetikett", de_body)
