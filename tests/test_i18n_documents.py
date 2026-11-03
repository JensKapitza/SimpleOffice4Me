"""i18n batch 3: documents contacts page renders DE + EN.

Static template strings follow the file-local ternary convention
(documents area); option values and stored labels stay German data.
Live browsers are NOT verified here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app.settings_store import SettingsStore
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class ContactsLanguageWebTest(unittest.TestCase):
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

    def _get(self, language: str = "en") -> str:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language
        response = self.client.get("/documents/contacts")
        self.assertEqual(200, response.status_code)
        return response.data.decode("utf-8")

    def test_contacts_english(self) -> None:
        body = self._get("en")
        self.assertIn("Contact list", body)
        self.assertIn("Create contact", body)
        self.assertIn("Import contact", body)
        self.assertIn("Save contact", body)

    def test_contacts_german(self) -> None:
        body = self._get("de")
        self.assertIn("Kontaktliste", body)
        self.assertIn("Kontakt anlegen", body)
        self.assertIn("Kontakt importieren", body)
        self.assertIn("Kontakt speichern", body)

    def test_dashboard_english(self) -> None:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "en"
        response = self.client.get("/documents/dashboard")
        self.assertEqual(200, response.status_code)
        body = response.data.decode("utf-8")
        self.assertIn("Overview", body)
        self.assertIn("Time clock", body)
        self.assertIn("Site visits", body)
        self.assertIn("Communication", body)
        self.assertIn("Open navigation", body)

    def test_dashboard_german(self) -> None:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"
        response = self.client.get("/documents/dashboard")
        self.assertEqual(200, response.status_code)
        body = response.data.decode("utf-8")
        self.assertIn("Übersicht", body)
        self.assertIn("Stempeluhr", body)
        self.assertIn("Ortstermine", body)
        self.assertIn("Kommunikation", body)
        self.assertIn("Navigation öffnen", body)


class ManagementPagesLanguageTest(unittest.TestCase):
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

    def _get(self, url: str, language: str, anonymous: bool = False) -> str:
        client = app.test_client() if anonymous else self.client
        with client.session_transaction() as session:
            session["simpleoffice_language"] = language
        response = client.get(url)
        self.assertEqual(200, response.status_code)
        return response.data.decode("utf-8")

    def test_notes_english(self) -> None:
        body = self._get("/documents/wiki/notes", "en")
        self.assertIn("Note wiki", body)
        self.assertIn("No notes yet.", body)

    def test_notes_german(self) -> None:
        body = self._get("/documents/wiki/notes", "de")
        self.assertIn("Notiz-Wiki", body)
        self.assertIn("Noch keine Notizen vorhanden.", body)

    def test_retention_english(self) -> None:
        body = self._get("/documents/retention", "en")
        self.assertIn("Deadlines and manual disposal", body)
        self.assertIn("Add rule", body)
        self.assertIn("No inherited rule configured yet.", body)

    def test_retention_german(self) -> None:
        body = self._get("/documents/retention", "de")
        self.assertIn("Fristen und manuelle Aussonderung", body)
        self.assertIn("Regel hinzufügen", body)
        self.assertIn("Noch keine vererbte Regel konfiguriert.", body)

    def test_recovery_english(self) -> None:
        body = self._get("/documents/recovery", "en")
        self.assertIn("Recover files", body)
        self.assertIn("Safe recovery:", body)
        self.assertIn("No own WebDAV deletions available for recovery.", body)

    def test_recovery_german(self) -> None:
        body = self._get("/documents/recovery", "de")
        self.assertIn("Dateien wiederherstellen", body)
        self.assertIn("Sichere Wiederherstellung:", body)
        self.assertIn("Keine eigenen WebDAV-Löschungen", body)

    def test_share_password_page(self) -> None:
        from app.document_store import DocumentStore
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "shared.txt").write_text("shared", encoding="utf-8")
        store = DocumentStore(self.root)
        store.scan()
        document = store.get_document("shared.txt")
        share = store.create_share(
            document["document_id"], "test-only-share-password", 1, "admin")
        url = f"/documents/share/{share['share_id']}"
        en_body = self._get(url, "en", anonymous=True)
        self.assertIn("Protected share", en_body)
        self.assertIn("Open", en_body)
        de_body = self._get(url, "de", anonymous=True)
        self.assertIn("Geschützte Freigabe", de_body)
        self.assertIn("Öffnen", de_body)

    def test_security_english(self) -> None:
        body = self._get("/documents/security", "en")
        self.assertIn("File security", body)
        self.assertIn("Server actions are only visible", body)

    def test_security_german(self) -> None:
        body = self._get("/documents/security", "de")
        self.assertIn("Dateisicherheit", body)
        self.assertIn("ausschließlich für konfigurierte Sicherheitsadministratoren", body)

    def test_logbook_english(self) -> None:
        body = self._get("/documents/logbook", "en")
        self.assertIn("Activity log", body)
        self.assertIn("How to read the logbook:", body)
        self.assertIn("No events yet.", body)

    def test_logbook_german(self) -> None:
        body = self._get("/documents/logbook", "de")
        self.assertIn("Logbuch", body)
        self.assertIn("So liest du das Logbuch:", body)
        self.assertIn("Noch keine Ereignisse.", body)

    def test_local_storage_english(self) -> None:
        body = self._get("/documents/local-storage", "en")
        self.assertIn("Local folders", body)
        self.assertIn("Add local folder", body)

    def test_local_storage_german(self) -> None:
        body = self._get("/documents/local-storage", "de")
        self.assertIn("Lokale Ordner", body)
        self.assertIn("Lokalen Ordner hinzufügen", body)

    def test_ssh_sources_english(self) -> None:
        body = self._get("/documents/sources/ssh", "en")
        self.assertIn("SSH systems", body)
        self.assertIn("Add SSH source", body)
        self.assertIn("No SSH source registered yet.", body)

    def test_ssh_sources_german(self) -> None:
        body = self._get("/documents/sources/ssh", "de")
        self.assertIn("SSH-Systeme", body)
        self.assertIn("SSH-Quelle hinzufügen", body)
        self.assertIn("Noch keine SSH-Quelle registriert.", body)

    def test_archives_english(self) -> None:
        body = self._get("/documents/archives", "en")
        self.assertIn("Additional external archives", body)
        self.assertIn("Register external archive", body)
        self.assertIn("No additional external archives registered.", body)

    def test_archives_german(self) -> None:
        body = self._get("/documents/archives", "de")
        self.assertIn("Zusätzliche externe Archive", body)
        self.assertIn("Externes Archiv registrieren", body)
        self.assertIn("Keine zusätzlichen externen Archive registriert.", body)

    def test_contact_management_english(self) -> None:
        body = self._get("/documents/contacts/manage", "en")
        self.assertIn("Contact management", body)
        self.assertIn("Advanced search", body)
        self.assertIn("CSV import center", body)

    def test_contact_management_german(self) -> None:
        body = self._get("/documents/contacts/manage", "de")
        self.assertIn("Kontaktmanagement", body)
        self.assertIn("Erweiterte Suche", body)
        self.assertIn("CSV-Import-Center", body)

    def _make_contact(self) -> str:
        from app.contact_store import ContactStore
        contact = ContactStore(self.root).upsert(
            {"display_name": "Language Test"}, "admin")
        return contact["contact_id"]

    def test_contact_snapshots_empty(self) -> None:
        contact_id = self._make_contact()
        en_body = self._get(f"/documents/contacts/{contact_id}/snapshots", "en")
        self.assertIn("Contact versions", en_body)
        self.assertIn("No full snapshots yet.", en_body)
        de_body = self._get(f"/documents/contacts/{contact_id}/snapshots", "de")
        self.assertIn("Kontaktversionen", de_body)
        self.assertIn("Noch keine vollständigen Snapshots vorhanden.", de_body)

    def test_contact_snapshot_compare_renders_both_languages(self) -> None:
        from flask import g, render_template
        comparison = {
            "current": {"contact_id": "c1",
                        "fields": {"display_name": "Language Test"}},
            "snapshot": {"created_at": "yesterday", "created_by": "admin",
                         "snapshot_id": "s1"},
            "changed_count": 1,
            "fields": [{"field": "phone", "old": "1", "new": "2",
                        "changed": True}],
            "metadata": [],
        }
        with app.test_request_context("/"):
            g.language = "en"
            g.user = {"username": "admin", "display_name": "Admin",
                      "is_admin": True, "is_disabled": 0, "theme": "system"}
            en_body = render_template(
                "documents/contact_snapshot_compare.html",
                comparison=comparison)
            g.language = "de"
            de_body = render_template(
                "documents/contact_snapshot_compare.html",
                comparison=comparison)
        self.assertIn("Version comparison", en_body)
        self.assertIn("Restore this version", en_body)
        self.assertIn("Versionsvergleich", de_body)
        self.assertIn("Diese Version wiederherstellen", de_body)

    def _post_import_preview(self) -> str:
        from io import BytesIO
        app.config.update(TEST_CSRF_PROTECTION=False)
        try:
            response = self.client.post(
                "/documents/contacts/import-preview",
                data={"contacts_file": (
                    BytesIO("display_name,email\nTest User,test@example.com\n".encode("utf-8")),
                    "import.csv")},
                content_type="multipart/form-data")
        finally:
            app.config.update(TEST_CSRF_PROTECTION=True)
        self.assertEqual(200, response.status_code)
        return response.data.decode("utf-8")

    def test_contact_import_preview_english(self) -> None:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "en"
        body = self._post_import_preview()
        self.assertIn("Import preview", body)
        self.assertIn("Preview only: no contacts were written.", body)

    def test_contact_import_preview_german(self) -> None:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"
        body = self._post_import_preview()
        self.assertIn("Importvorschau", body)
        self.assertIn("Nur Vorschau: Es wurden keine Kontakte geschrieben.", body)

    def test_contact_letter_english(self) -> None:
        contact_id = self._make_contact()
        body = self._get(
            f"/documents/business/contacts/{contact_id}/letter", "en")
        self.assertIn("Create letter", body)
        self.assertIn("No address on file.", body)

    def test_contact_letter_german(self) -> None:
        contact_id = self._make_contact()
        body = self._get(
            f"/documents/business/contacts/{contact_id}/letter", "de")
        self.assertIn("Brief erstellen", body)
        self.assertIn("Keine Adresse vorhanden.", body)

    def _make_form(self) -> str:
        from app.form_store import FormStore
        FormStore(self.root).save_definition({
            "form_id": "language-test", "name": "Language Test",
            "description": "desc", "title_field": "name",
            "fields": [{"key": "name", "label": "Name", "type": "text"}],
        }, "admin")
        return "language-test"

    def test_forms_english(self) -> None:
        body = self._get("/documents/forms", "en")
        self.assertIn("Form assistant", body)
        self.assertIn("New form", body)

    def test_forms_german(self) -> None:
        body = self._get("/documents/forms", "de")
        self.assertIn("Formular-Assistent", body)
        self.assertIn("Neues Formular", body)

    def test_form_records_english(self) -> None:
        form_id = self._make_form()
        body = self._get(f"/documents/forms/{form_id}", "en")
        self.assertIn("Stored records", body)
        self.assertIn("Capture record", body)

    def test_form_records_german(self) -> None:
        form_id = self._make_form()
        body = self._get(f"/documents/forms/{form_id}", "de")
        self.assertIn("Gespeicherte Datensätze", body)
        self.assertIn("Datensatz erfassen", body)

    def test_form_record_detail(self) -> None:
        from app.form_store import FormStore
        form_id = self._make_form()
        record = FormStore(self.root).save_record(
            form_id, {"name": "Test Record"}, "admin")
        record_id = record["record_id"]
        en_body = self._get(
            f"/documents/forms/{form_id}/{record_id}", "en")
        self.assertIn("Save changes", en_body)
        de_body = self._get(
            f"/documents/forms/{form_id}/{record_id}", "de")
        self.assertIn("Änderungen speichern", de_body)

    def _make_project(self) -> str:
        from app.project_store import ProjectStore
        project = ProjectStore(self.root).create_project(
            {"title": "Language Project"}, "admin")
        return project["project_id"]

    def test_contact_history_aria(self) -> None:
        en_body = self._get("/documents/contacts/history", "en")
        self.assertIn("Filter contact history", en_body)
        de_body = self._get("/documents/contacts/history", "de")
        self.assertIn("Kontakthistorie filtern", de_body)

    def test_project_activity(self) -> None:
        project_id = self._make_project()
        en_body = self._get(
            f"/documents/projects/{project_id}/activity", "en")
        self.assertIn("No activity yet", en_body)
        self.assertIn("Project areas", en_body)
        de_body = self._get(
            f"/documents/projects/{project_id}/activity", "de")
        self.assertIn("Noch keine Aktivität", de_body)
        self.assertIn("Projektbereiche", de_body)

    def test_project_code_renders_both_languages(self) -> None:
        from flask import g, render_template
        project = {"project_id": "p1", "title": "Language Project"}
        summary = {"branch": "main", "has_head": False, "dirty": False,
                   "commits": []}
        context = {"project": project, "summary": summary, "tree": [],
                   "current_path": "", "ref": "HEAD",
                   "active_project_tab": "code", "code_available": False}
        with app.test_request_context("/"):
            g.language = "en"
            g.user = {"username": "admin", "display_name": "Admin",
                      "is_admin": True, "is_disabled": 0, "theme": "system"}
            en_body = render_template(
                "documents/project_code.html", **context)
            g.language = "de"
            de_body = render_template(
                "documents/project_code.html", **context)
        self.assertIn("No files in this git tree.", en_body)
        self.assertIn("No commits.", en_body)
        self.assertIn("Keine Dateien in diesem Git-Baum.", de_body)
        self.assertIn("Keine Commits.", de_body)

    def test_project_code_file_renders_both_languages(self) -> None:
        from flask import g, render_template
        context = {
            "project": {"project_id": "p1", "title": "Language Project"},
            "file": {"path": "readme.txt", "ref": "HEAD", "size": 12,
                     "text": "hello"},
            "markdown_html": None,
            "active_project_tab": "code", "code_available": False,
        }
        with app.test_request_context("/"):
            g.language = "en"
            g.user = {"username": "admin", "display_name": "Admin",
                      "is_admin": True, "is_disabled": 0, "theme": "system"}
            en_body = render_template(
                "documents/project_code_file.html", **context)
            g.language = "de"
            de_body = render_template(
                "documents/project_code_file.html", **context)
        self.assertIn("12 bytes", en_body)
        self.assertIn("12 Byte", de_body)

    def _render(self, template: str, language: str, **context) -> str:
        from flask import g, render_template
        with app.test_request_context("/"):
            g.language = language
            g.user = {"username": "admin", "display_name": "Admin",
                      "is_admin": True, "is_disabled": 0, "theme": "system"}
            return render_template(template, **context)

    def test_v3_inbox_disabled_state(self) -> None:
        en_body = self._get("/documents/inbox-v3", "en")
        self.assertIn("is disabled.", en_body)
        de_body = self._get("/documents/inbox-v3", "de")
        self.assertIn("ist deaktiviert.", de_body)

    def test_v3_inbox_detail_renders_both_languages(self) -> None:
        item = {"item_id": "i1", "original_name": "scan.pdf",
                "source": "web", "source_key": "k", "status": "new",
                "sha256": None, "document_id": None, "assignments": []}
        en_body = self._render("documents/v3_inbox_detail.html", "en",
                               item=item, steps=[])
        self.assertIn("Assign", en_body)
        self.assertIn("Contact", en_body)
        de_body = self._render("documents/v3_inbox_detail.html", "de",
                               item=item, steps=[])
        self.assertIn("Zuordnen", de_body)
        self.assertIn("Kontakt", de_body)

    def test_mail_archive_preview_renders_both_languages(self) -> None:
        preview = {"subject": "Hello", "size": 2048, "from": "a@x.de",
                   "to": "b@x.de", "cc": "", "date": "today",
                   "message_id": "", "path": "/archive/1.eml",
                   "text": "body", "attachments": []}
        context = {"preview": preview, "account_id": "a", "query": ""}
        en_body = self._render(
            "documents/mail_archive_preview.html", "en", **context)
        self.assertIn("Archived email", en_body)
        self.assertIn("Back to archive", en_body)
        de_body = self._render(
            "documents/mail_archive_preview.html", "de", **context)
        self.assertIn("Archivierte E-Mail", de_body)
        self.assertIn("Zurück zum Archiv", de_body)

    def test_eml_document_preview_renders_both_languages(self) -> None:
        preview = {"subject": "Hello", "date": "", "from": "a@x.de",
                   "to": "b@x.de", "reply_to": "", "cc": "",
                   "message_id": "", "text": "body", "html": "",
                   "attachment_count": 0, "attachment_bytes": 0,
                   "attachments": [], "has_inline_images": False}
        context = {"preview": preview, "document_id": "d1", "view": "text",
                   "html_srcdoc": ""}
        en_body = self._render(
            "documents/eml_document_preview.html", "en", **context)
        self.assertIn("Readable text", en_body)
        self.assertIn("No attachments.", en_body)
        de_body = self._render(
            "documents/eml_document_preview.html", "de", **context)
        self.assertIn("Lesbarer Text", de_body)
        self.assertIn("Keine Anhänge.", de_body)

    def test_zugferd_details_renders_both_languages(self) -> None:
        document = {"document_id": "d1"}
        en_body = self._render("documents/zugferd_details.html", "en",
                               document=document,
                               details={"detected": False})
        self.assertIn("No embedded ZUGFeRD", en_body)
        de_body = self._render("documents/zugferd_details.html", "de",
                               document=document,
                               details={"detected": True,
                                        "xml_filename": "x.xml",
                                        "invoice_id": "1",
                                        "issue_date": "today",
                                        "profile": "", "validation": "ok",
                                        "raw_xml": "<xml/>"})
        self.assertIn("Eingebettete Rechnungsdaten erkannt", de_body)
        self.assertIn("XML anzeigen", de_body)

    def test_smart_folder_job_renders_both_languages(self) -> None:
        job = {"state": "failed", "attempt": 2,
               "progress": {"total": 4, "completed": 1},
               "last_error": "", "payload": {"folder": "inbox"}}
        en_body = self._render("documents/smart_folder_job.html", "en",
                               job=job)
        self.assertIn("Smart folder batch job", en_body)
        self.assertIn("The batch job failed.", en_body)
        de_body = self._render("documents/smart_folder_job.html", "de",
                               job=job)
        self.assertIn("Sammelordner-Stapelaktion", de_body)
        self.assertIn("Die Stapelaktion ist fehlgeschlagen.", de_body)

    def test_project_issues(self) -> None:
        project_id = self._make_project()
        en_body = self._get(
            f"/documents/projects/{project_id}/issues", "en")
        self.assertIn("New issue", en_body)
        self.assertIn("No matching issues", en_body)
        de_body = self._get(
            f"/documents/projects/{project_id}/issues", "de")
        self.assertIn("Neues Issue", de_body)
        self.assertIn("Keine passenden Issues", de_body)

    def test_project_wiki(self) -> None:
        project_id = self._make_project()
        en_body = self._get(
            f"/documents/projects/{project_id}/wiki", "en")
        self.assertIn("No wiki pages yet.", en_body)
        self.assertIn("Create first wiki page", en_body)
        de_body = self._get(
            f"/documents/projects/{project_id}/wiki", "de")
        self.assertIn("Noch keine Wiki-Seiten.", de_body)
        self.assertIn("Erste Wiki-Seite anlegen", de_body)

    def test_project_issue_detail(self) -> None:
        from app.project_tracker import ProjectTrackerStore
        project_id = self._make_project()
        issue = ProjectTrackerStore(self.root).create_issue(
            project_id, {"title": "Language Issue"}, "admin")
        url = (f"/documents/projects/{project_id}"
               f"/issues/{issue['number']}")
        en_body = self._get(url, "en")
        self.assertIn("Edit issue", en_body)
        self.assertIn("No comments yet.", en_body)
        de_body = self._get(url, "de")
        self.assertIn("Issue bearbeiten", de_body)
        self.assertIn("Noch keine Kommentare.", de_body)

    def test_v3_finance_lifecycle(self) -> None:
        SettingsStore(self.root).save({"v3": {"v3.finance": True}}, "test")
        en_body = self._get("/documents/business/lifecycle-v3", "en")
        self.assertIn("New business document", en_body)
        self.assertIn("No lifecycle documents yet.", en_body)
        de_body = self._get("/documents/business/lifecycle-v3", "de")
        self.assertIn("Neues Geschäftsdokument", de_body)
        self.assertIn("Noch keine Lifecycle-Dokumente.", de_body)

    def test_smart_folder_merge_preview_renders_both_languages(self) -> None:
        impact = {"source": "src", "target": "dst", "documents": 3,
                  "target_documents": 1, "source_preview_ids": ["a"],
                  "target_preview_ids": ["b"],
                  "task_preview_available": True, "source_tasks": 0,
                  "target_tasks": 0, "source_task_rows": [],
                  "target_task_rows": [], "source_tasks_truncated": False,
                  "target_tasks_truncated": False, "source_rules": [],
                  "target_rules": [], "source_rule_definitions": {},
                  "target_rule_definitions": {}, "source_notes": "",
                  "target_notes": "", "conflicts": [],
                  "source_paths": ["src/a.txt"],
                  "source_paths_truncated": False, "fingerprint": "fp"}
        en_body = self._render(
            "documents/smart_folder_merge_preview.html", "en",
            impact=impact)
        self.assertIn("Check folder merge", en_body)
        self.assertIn("Confirm merge", en_body)
        de_body = self._render(
            "documents/smart_folder_merge_preview.html", "de",
            impact=impact)
        self.assertIn("Ordnerfusion prüfen", de_body)
        self.assertIn("Fusion bestätigen", de_body)

    def test_photo_snapshot_renders_both_languages(self) -> None:
        en_body = self._render("documents/_photo_snapshot.html", "en")
        self.assertIn("Capture &amp; save", en_body)
        self.assertIn("New photo", en_body)
        de_body = self._render("documents/_photo_snapshot.html", "de")
        self.assertIn("Aufnehmen &amp; speichern", de_body)
        self.assertIn("Neues Foto", de_body)

    def test_euer_booking_form(self) -> None:
        en_body = self._get("/documents/business/bookkeeping/new", "en")
        self.assertIn("Book receipt", en_body)
        self.assertIn("Book bindingly", en_body)
        de_body = self._get("/documents/business/bookkeeping/new", "de")
        self.assertIn("Beleg buchen", de_body)
        self.assertIn("Verbindlich buchen", de_body)

    def test_video_player_renders_both_languages(self) -> None:
        context = {
            "document": {"document_id": "d1",
                         "last_path": "videos/demo.mp4"},
            "raw_url": "/raw/demo.mp4", "download_url": "/dl/demo.mp4",
            "thumbnail_url": "", "mime": "video/mp4", "video": None,
            "frames": [], "can_manage_video": False,
            "video_preview_frame_count": 5, "variants": [],
            "ffmpeg_available": False,
        }
        en_body = self._render("documents/video_player.html", "en",
                               **context)
        self.assertIn("Video preview and technical variants", en_body)
        self.assertIn("Playback variants", en_body)
        de_body = self._render("documents/video_player.html", "de",
                               **context)
        self.assertIn("Video-Vorschau und technische Varianten", de_body)
        self.assertIn("Wiedergabevarianten", de_body)

    def test_book_calendar_public(self) -> None:
        for language, marker in (("en", "Request appointment"),
                                 ("de", "Termin anfragen")):
            client = app.test_client()
            with client.session_transaction() as session:
                session["simpleoffice_language"] = language
            response = client.get("/documents/calendar/book")
            self.assertIn(response.status_code, (200, 400))
            self.assertIn(marker, response.data.decode("utf-8"))

    def test_published_calendar(self) -> None:
        en_body = self._get("/documents/calendar/published/family", "en",
                            anonymous=True)
        self.assertIn("Family calendar", en_body)
        self.assertIn("No published appointments.", en_body)
        de_body = self._get("/documents/calendar/published/family", "de",
                            anonymous=True)
        self.assertIn("Familienkalender", de_body)
        self.assertIn("Keine veröffentlichten Termine.", de_body)

    def test_shared_note_renders_both_languages(self) -> None:
        from flask import g, render_template
        note = {"text": "note text", "author": "admin", "created_at": "now"}
        document = {"last_path": "note.txt"}
        share = {"expires_at": "tomorrow"}
        with app.test_request_context("/"):
            g.language = "en"
            en_body = render_template(
                "documents/shared_note.html", note=note,
                document=document, share=share)
            g.language = "de"
            de_body = render_template(
                "documents/shared_note.html", note=note,
                document=document, share=share)
        self.assertIn("Shared until", en_body)
        self.assertIn("Freigabe bis", de_body)


if __name__ == "__main__":
    unittest.main()
