"""i18n batch 13: remaining documents pages render DE + EN.

Covers EÜR receipts/overview, mail client, mail duplicates, smart folders,
project tasks/detail, objects, company search partial, FRITZ!Box, CRM
workspace, finance detail, WebDAV settings, setup, replication, images,
plus new duplicates.* catalog keys.

Static template strings follow the file-local ternary convention
(documents area); backend tokens, option values and stored labels stay
German data. Live browsers are NOT verified here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class ManagementBatch13Test(unittest.TestCase):
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

    def test_v3_finance_detail(self) -> None:
        import os
        from unittest.mock import patch
        from app.v3_finance import FinanceLifecycleStore
        self.root.mkdir(parents=True, exist_ok=True)
        offer = FinanceLifecycleStore(self.root).create(
            "offer", "admin", title="Sprachangebot",
            working={"amount": "10.00", "description": "Test"})
        url = f"/documents/business/lifecycle-v3/{offer.lifecycle_id}"
        with patch.dict(os.environ,
                        {"SIMPLEOFFICE_V3_FINANCE_ENABLED": "1"}):
            en_body = self._get(url, "en")
            self.assertIn("Sprachangebot", en_body)
            self.assertIn("Save draft", en_body)
            self.assertIn("Snapshot / references", en_body)
            self.assertIn("Immutable history", en_body)
            self.assertIn("created", en_body)
            self.assertNotIn("Entwurf speichern", en_body)
            self.assertNotIn("Unveränderliche Historie", en_body)
            de_body = self._get(url, "de")
            self.assertIn("Entwurf speichern", de_body)
            self.assertIn("Snapshot / Referenzen", de_body)
            self.assertIn("Unveränderliche Historie", de_body)

    def test_eur_receipts(self) -> None:
        en_body = self._get("/documents/accounting/receipts", "en")
        self.assertIn("EÜR receipts", en_body)
        self.assertIn("Enter receipt", en_body)
        self.assertIn("Save and check receipt", en_body)
        self.assertIn("Tax-advisor ZIP", en_body)
        self.assertIn("Recorded receipts", en_body)
        self.assertIn("Receipt date", en_body)
        self.assertNotIn("Beleg eingeben", en_body)
        self.assertNotIn("Steuerberater-ZIP", en_body)
        self.assertNotIn("Erfasste Belege", en_body)
        de_body = self._get("/documents/accounting/receipts", "de")
        self.assertIn("EÜR-Belege", de_body)
        self.assertIn("Beleg eingeben", de_body)
        self.assertIn("Beleg speichern und prüfen", de_body)
        self.assertIn("Steuerberater-ZIP", de_body)
        self.assertIn("Erfasste Belege", de_body)
        self.assertIn("Belegdatum", de_body)

    def test_euer_overview(self) -> None:
        from app.euer_store import EuerStore
        self.root.mkdir(parents=True, exist_ok=True)
        EuerStore(self.root).add({
            "direction": "income", "category": "small_business_income",
            "description": "Honorar August",
            "gross": "500", "booking_date": "2026-08-31",
            "document_date": "2026-08-31", "tax_mode": "small_business",
        }, "admin")
        en_body = self._get("/documents/business/bookkeeping?year=2026", "en")
        self.assertIn("Income statement", en_body)
        self.assertIn("Book receipt", en_body)
        self.assertIn("VAT balance", en_body)
        self.assertIn("Honorar August", en_body)
        self.assertIn("Totals by EÜR category", en_body)
        self.assertNotIn("Beleg buchen", en_body)
        self.assertNotIn("Summen nach EÜR-Kategorie", en_body)
        de_body = self._get("/documents/business/bookkeeping?year=2026", "de")
        self.assertIn("Einnahmenüberschussrechnung", de_body)
        self.assertIn("Beleg buchen", de_body)
        self.assertIn("Umsatzsteuer-Modus", de_body)
        self.assertIn("Honorar August", de_body)
        self.assertIn("Summen nach EÜR-Kategorie", de_body)

    def test_euer_overview_empty(self) -> None:
        en_body = self._get("/documents/business/bookkeeping?year=2026", "en")
        self.assertIn("No bookings for this selection yet.", en_body)
        de_body = self._get("/documents/business/bookkeeping?year=2026", "de")
        self.assertIn("Für diese Auswahl gibt es noch keine Buchungen.",
                      de_body)

    def test_mail_client(self) -> None:
        en_body = self._get("/documents/mail", "en")
        self.assertIn("IMAP, SMTP and email archive", en_body)
        self.assertIn("Server configuration", en_body)
        self.assertIn(">Detect server automatically</button>", en_body)
        self.assertIn("Display name", en_body)
        self.assertIn("Advanced settings", en_body)
        self.assertIn("Save account", en_body)
        self.assertIn("Accounts and archive run", en_body)
        self.assertIn("No IMAP account configured yet.", en_body)
        self.assertIn("const english = true", en_body)
        self.assertNotIn("Serverkonfiguration", en_body)
        self.assertNotIn("Konto speichern", en_body)
        self.assertNotIn("Konten und Archivlauf", en_body)
        self.assertNotIn("Noch kein IMAP-Konto konfiguriert", en_body)
        de_body = self._get("/documents/mail", "de")
        self.assertIn("IMAP, SMTP und E-Mail-Archiv", de_body)
        self.assertIn("Serverkonfiguration", de_body)
        self.assertIn(">Server automatisch ermitteln</button>", de_body)
        self.assertIn("Konto speichern", de_body)
        self.assertIn("Konten und Archivlauf", de_body)
        self.assertIn("Noch kein IMAP-Konto konfiguriert.", de_body)
        self.assertIn("const english = false", de_body)

    def test_smart_folders(self) -> None:
        en_body = self._get("/documents/smart-folders", "en")
        self.assertIn("Smart views and collection folders", en_body)
        self.assertIn("No folders with documents yet.", en_body)
        self.assertIn("Select a folder to show its documents dynamically.",
                      en_body)
        self.assertNotIn("Sammelordner", en_body)
        self.assertNotIn("Keine Ordner mit Dokumenten vorhanden.", en_body)
        de_body = self._get("/documents/smart-folders", "de")
        self.assertIn("Smart Views und Sammelordner", de_body)
        self.assertIn("Keine Ordner mit Dokumenten vorhanden.", de_body)
        self.assertIn("Wähle einen Ordner, um seine Dokumente dynamisch "
                      "anzuzeigen.", de_body)

    def test_project_tasks(self) -> None:
        from app.project_store import ProjectStore
        self.root.mkdir(parents=True, exist_ok=True)
        project = ProjectStore(self.root).create_project(
            {"title": "Sprachprojekt"}, "admin")
        url = f"/documents/projects/{project['project_id']}/tasks"
        en_body = self._get(url, "en")
        self.assertIn("Create task", en_body)
        self.assertIn("No tasks yet", en_body)
        self.assertIn("Create the first work step", en_body)
        self.assertIn("tasks visible", en_body)
        self.assertNotIn("Aufgabe anlegen", en_body)
        self.assertNotIn("Noch keine Aufgaben", en_body)
        de_body = self._get(url, "de")
        self.assertIn("Aufgabe anlegen", de_body)
        self.assertIn("Noch keine Aufgaben", de_body)
        self.assertIn("Aufgaben sichtbar", de_body)

    def test_project_detail(self) -> None:
        from app.project_store import ProjectStore
        self.root.mkdir(parents=True, exist_ok=True)
        project = ProjectStore(self.root).create_project(
            {"title": "Sprachprojekt"}, "admin")
        url = f"/documents/projects/{project['project_id']}"
        en_body = self._get(url, "en")
        self.assertIn("Project progress", en_body)
        self.assertIn("Project info", en_body)
        self.assertIn("Save project", en_body)
        self.assertIn("Quick actions", en_body)
        self.assertIn("Time groups and billing", en_body)
        self.assertNotIn("Projektfortschritt", en_body)
        self.assertNotIn("Schnellaktionen", en_body)
        de_body = self._get(url, "de")
        self.assertIn("Projektfortschritt", de_body)
        self.assertIn("Projektinfos", de_body)
        self.assertIn("Projekt speichern", de_body)
        self.assertIn("Schnellaktionen", de_body)
        self.assertIn("Zeitgruppen und Abrechnung", de_body)

    def test_objects(self) -> None:
        from app.object_store import ObjectStore
        self.root.mkdir(parents=True, exist_ok=True)
        item = ObjectStore(self.root).create(
            {"name": "Beratung Stunde", "type": "Leistung"}, "admin")
        en_body = self._get("/documents/objects", "en")
        self.assertIn("Objects", en_body)
        self.assertIn("Create object", en_body)
        self.assertIn("Beratung Stunde", en_body)
        self.assertNotIn("Objekt anlegen", en_body)
        self.assertNotIn("Keine Objekte gefunden.", en_body)
        de_body = self._get("/documents/objects", "de")
        self.assertIn("Objekte", de_body)
        self.assertIn("Objekt anlegen", de_body)
        self.assertIn("Beratung Stunde", de_body)
        detail_url = f"/documents/objects/{item['object_id']}"
        en_detail = self._get(detail_url, "en")
        self.assertIn("Master data", en_detail)
        self.assertIn("All objects", en_detail)
        self.assertIn("Add note", en_detail)
        self.assertIn("Save", en_detail)
        self.assertNotIn("Stammdaten", en_detail)
        self.assertNotIn("Notiz hinzufügen", en_detail)
        de_detail = self._get(detail_url, "de")
        self.assertIn("Stammdaten", de_detail)
        self.assertIn("Alle Objekte", de_detail)
        self.assertIn("Notiz hinzufügen", de_detail)
        self.assertIn("Speichern", de_detail)

    def test_company_data_search_partial(self) -> None:
        # Partial renders inside the contacts page.
        en_body = self._get("/documents/contacts", "en")
        self.assertIn("Search company data and adopt manually", en_body)
        self.assertIn("Local duplicates", en_body)
        self.assertIn("Assign labeled lines", en_body)
        self.assertIn("Adopt only empty fields", en_body)
        self.assertNotIn("Firmendaten suchen und manuell übernehmen", en_body)
        self.assertNotIn("Beschriftete Zeilen zuordnen", en_body)
        de_body = self._get("/documents/contacts", "de")
        self.assertIn("Firmendaten suchen und manuell übernehmen", de_body)
        self.assertIn("Lokale Dubletten", de_body)
        self.assertIn("Beschriftete Zeilen zuordnen", de_body)
        self.assertIn("Nur leere Felder übernehmen", de_body)

    def test_fritzbox_contacts(self) -> None:
        en_body = self._get("/contacts/fritzbox", "en")
        self.assertIn("1. Connection", en_body)
        self.assertIn("Test connection", en_body)
        self.assertIn("phone books reported by the FRITZ!Box", en_body)
        self.assertNotIn("1. Verbindung", en_body)
        self.assertNotIn("Telefonbücher angezeigt", en_body)
        de_body = self._get("/contacts/fritzbox", "de")
        self.assertIn("1. Verbindung", de_body)
        self.assertIn("Verbindung testen", de_body)
        self.assertIn("Telefonbücher angezeigt", de_body)

    def test_v3_crm_workspace(self) -> None:
        import gc
        import os
        from unittest.mock import patch
        from app.contact_store import ContactStore
        self.root.mkdir(parents=True, exist_ok=True)
        contact = ContactStore(self.root).upsert(
            {"display_name": "Sprach Kontakt"}, "admin")
        url = f"/crm-v3/contacts/{contact['contact_id']}"
        with patch.dict(os.environ,
                        {"SIMPLEOFFICE_V3_CRM_ENABLED": "1"}):
            en_body = self._get(url, "en")
            self.assertIn("Sprach Kontakt", en_body)
            self.assertIn("Roles and relationships", en_body)
            self.assertIn("No 3.0 relationships yet.", en_body)
            self.assertIn("Add relationship", en_body)
            self.assertIn("Save relationship", en_body)
            self.assertNotIn("Rollen und Beziehungen", en_body)
            self.assertNotIn("Beziehung speichern", en_body)
            de_body = self._get(url, "de")
            self.assertIn("Rollen und Beziehungen", de_body)
            self.assertIn("Noch keine 3.0-Beziehungen.", de_body)
            self.assertIn("Beziehung hinzufügen", de_body)
            self.assertIn("Beziehung speichern", de_body)
        # Windows-only: repeated workspace requests leave a cyclic-garbage
        # sqlite handle on v3-relations.sqlite3 until collection; collect
        # here so tearDown can remove the temp dir. No-op elsewhere.
        gc.collect()

    def test_webdav_settings(self) -> None:
        # Route resolves DOCUMENT_ROOT strictly; it must exist.
        self.root.mkdir(parents=True, exist_ok=True)
        en_body = self._get("/settings/webdav", "en")
        self.assertIn("WebDAV, SSHFS and folder rights", en_body)
        self.assertIn("Create general access", en_body)
        self.assertIn("Folder rights for WebDAV and SFTP", en_body)
        self.assertNotIn("Allgemeinen Zugang erzeugen", en_body)
        self.assertNotIn("Ordnerrechte für WebDAV und SFTP", en_body)
        de_body = self._get("/settings/webdav", "de")
        self.assertIn("WebDAV, SSHFS und Ordnerrechte", de_body)
        self.assertIn("Allgemeinen Zugang erzeugen", de_body)
        self.assertIn("Ordnerrechte für WebDAV und SFTP", de_body)

    def test_setup(self) -> None:
        en_body = self._get("/documents/setup", "en")
        self.assertIn("Set up SimpleOffice", en_body)
        self.assertIn("1. Services and rights", en_body)
        self.assertIn("Mark setup as completed", en_body)
        self.assertNotIn("SimpleOffice einrichten", en_body)
        self.assertNotIn("Dienste und Rechte", en_body)
        de_body = self._get("/documents/setup", "de")
        self.assertIn("SimpleOffice einrichten", de_body)
        self.assertIn("1. Dienste und Rechte", de_body)
        self.assertIn("Einrichtung als abgeschlossen markieren", de_body)

    def test_mail_duplicates(self) -> None:
        from app.mail_client import MailStore
        self.root.mkdir(parents=True, exist_ok=True)
        secret = app.config["SECRET_KEY"]
        key = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        MailStore(self.root, key).save_account(
            "admin",
            {"host": "imap.example.test", "username": "admin@example.test",
             "smtp_host": "smtp.example.test",
             "smtp_from": "admin@example.test"},
            "secret-password",
            True,
        )
        en_body = self._get("/documents/mail/index", "en")
        self.assertIn("Spam &amp; duplicates", en_body)
        self.assertIn("Content-based duplicate detection", en_body)
        self.assertIn("Index entries", en_body)
        self.assertIn("Mail federation", en_body)
        self.assertIn("Detected content groups", en_body)
        self.assertIn("No duplicate groups detected yet.", en_body)
        self.assertNotIn("Dublettenerkennung trotz", en_body)
        self.assertNotIn("Erkannte Inhaltsgruppen", en_body)
        de_body = self._get("/documents/mail/index", "de")
        self.assertIn("Spam &amp; Dubletten", de_body)
        self.assertIn("Indexeinträge", de_body)
        self.assertIn("Mail-Federation", de_body)
        self.assertIn("Erkannte Inhaltsgruppen", de_body)
        self.assertIn("Noch keine Dublettengruppen erkannt.", de_body)

    def test_replication(self) -> None:
        # Route resolves DOCUMENT_ROOT strictly; it must exist.
        self.root.mkdir(parents=True, exist_ok=True)
        en_body = self._get("/documents/replication", "en")
        self.assertIn("Storage, mirroring and backup", en_body)
        self.assertIn("Create storage target", en_body)
        self.assertIn("Mirror rules", en_body)
        self.assertIn("No mirror rules yet.", en_body)
        self.assertIn("Restic actions", en_body)
        self.assertIn("const english=true", en_body)
        self.assertNotIn("Spiegelungsregeln", en_body)
        self.assertNotIn("Speicherziel anlegen", en_body)
        de_body = self._get("/documents/replication", "de")
        self.assertIn("Speicher, Spiegelung und Sicherung", de_body)
        self.assertIn("Speicherziel anlegen", de_body)
        self.assertIn("Spiegelungsregeln", de_body)
        self.assertIn("Noch keine Spiegelungsregeln.", de_body)
        self.assertIn("const english=false", de_body)

    def test_images(self) -> None:
        en_body = self._get("/documents/images", "en")
        self.assertIn("Images", en_body)
        self.assertIn("Capture, save, assign later", en_body)
        self.assertIn("Bulk-import phone photos", en_body)
        self.assertIn("No images in the document stock.", en_body)
        self.assertIn("const english = true", en_body)
        self.assertNotIn("Bilder bleiben normale", en_body)
        self.assertNotIn("Keine Bilder im Dokumentbestand.", en_body)
        de_body = self._get("/documents/images", "de")
        self.assertIn("Erfassen, speichern, später zuordnen", de_body)
        self.assertIn("Handy-Fotos massenweise importieren", de_body)
        self.assertIn("Keine Bilder im Dokumentbestand.", de_body)
        self.assertIn("const english = false", de_body)


if __name__ == "__main__":
    unittest.main()
