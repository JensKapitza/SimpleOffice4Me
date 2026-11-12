"""i18n: document detail notes + linking section renders DE + EN.

Covers the notes card and the document/free-text linking card in
templates/documents/detail.html (Issue #977 slice).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database
from app.documents import DocumentStore


class DetailNotesLinkingI18nTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        previous = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        self.addCleanup(app.config.update, previous)
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        credentials = {"username": "notes-tester", "password": "test-notes-password"}
        self.client.post("/auth/register", data=credentials)
        self.client.post("/auth/login", data=credentials)
        self.root = base / "documents"
        self.store = DocumentStore(self.root)
        self.store.initialize()
        source = base / "note.txt"
        source.write_bytes(b"hello")
        target = self.store.import_file(source, actor="notes-tester")
        self.document_id = self.store.get_document(target)["document_id"]

    def _get(self, language: str) -> str:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language
        # link_query renders the match list branch ("no other document found").
        response = self.client.get(
            f"/documents/{self.document_id}?link_query=zzz-no-such-document"
        )
        self.addCleanup(response.close)
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def _get_image(self, document_id: str, language: str) -> str:
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = language
        response = self.client.get(f"/documents/{document_id}")
        self.addCleanup(response.close)
        self.assertEqual(200, response.status_code)
        return response.get_data(as_text=True)

    def _import_png(self) -> str:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is not installed")
        source = Path(self.temp.name) / "pixel.png"
        Image.new("RGB", (4, 2), "white").save(source)
        target = self.store.import_file(source, actor="notes-tester")
        return self.store.get_document(target)["document_id"]

    def test_image_analysis_english(self) -> None:
        body = self._get_image(self._import_png(), "en")
        for expected in (
            "Image analysis",
            "Dimensions",
            "Re-analyze EXIF / OCR",
        ):
            self.assertIn(expected, body)

    def test_image_analysis_german(self) -> None:
        body = self._get_image(self._import_png(), "de")
        for expected in (
            "Bildanalyse",
            "Abmessungen",
            "EXIF / OCR erneut analysieren",
        ):
            self.assertIn(expected, body)

    def _get_image_with_engel(self, document_id: str, language: str) -> str:
        previous = app.config.get("CONTEXTFLOW_ENABLED")
        app.config.update(CONTEXTFLOW_ENABLED=True)
        try:
            return self._get_image(document_id, language)
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=previous)

    def test_engel_section_english(self) -> None:
        body = self._get_image_with_engel(self._import_png(), "en")
        for expected in (
            "Office Angel",
            "Local analysis of the stored image",
            "Analyze image",
        ):
            self.assertIn(expected, body)

    def test_engel_section_german(self) -> None:
        body = self._get_image_with_engel(self._import_png(), "de")
        for expected in (
            "Büroengel",
            "Lokale Analyse des gespeicherten Bildes",
            "Bild analysieren",
        ):
            self.assertIn(expected, body)

    def _import_eml(self) -> str:
        source = Path(self.temp.name) / "mail.eml"
        source.write_bytes(
            b"From: a@example.test\r\nTo: b@example.test\r\n"
            b"Subject: Test\r\nContent-Type: text/plain\r\n\r\nHallo\r\n"
        )
        target = self.store.import_file(source, actor="notes-tester")
        return self.store.get_document(target)["document_id"]

    def test_eml_card_english(self) -> None:
        body = self._get_image(self._import_eml(), "en")
        for expected in (
            "Read e-mail and attachments",
            "E-mail as text",
            "E-mail as HTML",
            "Show and check attachments",
            "No attachments detected.",
            "Decoded access only after ClamAV scan and release.",
        ):
            self.assertIn(expected, body)

    def test_eml_card_german(self) -> None:
        body = self._get_image(self._import_eml(), "de")
        for expected in (
            "E-Mail lesen und Anhänge",
            "E-Mail als Text",
            "E-Mail als HTML",
            "Anhänge anzeigen und prüfen",
            "Keine Anhänge erkannt.",
            "Dekodierter Zugriff erst nach ClamAV-Prüfung und Freigabe.",
        ):
            self.assertIn(expected, body)

    def test_notes_linking_english(self) -> None:
        body = self._get("en")
        for expected in (
            "Notes",
            "Save note",
            "No notes yet.",
            "Link documents and free text",
            "Optional label",
            "No other document found.",
            "Link free text",
            "Record knowledge, a decision or an observation",
            "Organization",
            "Move to folder",
            "Copy to folder",
            "Delete document",
            "Delete safely",
            "Browse smart views / collection folders",
            "Deadlines",
            "Review by",
            "Retain until",
            "deadline missing",
            "Add deadline",
            "Versions",
            "Share file",
            "Create HTTPS link",
            "Share links",
            "No share links yet.",
            "Logbook of this document",
            "Book receipt",
        ):
            self.assertIn(expected, body)
        # The JS translations dict legitimately carries German keys; only the
        # visible markup must be German-free.
        visible = body.split("window.SimpleOfficeTranslations")[0]
        for german in ("Notiz speichern", "Noch keine Notizen.", "Freitext verknüpfen"):
            self.assertNotIn(german, visible)

    def test_notes_linking_german(self) -> None:
        body = self._get("de")
        for expected in (
            "Notizen",
            "Notiz speichern",
            "Noch keine Notizen.",
            "Dokumente und Freitext verknüpfen",
            "Optionale Bezeichnung",
            "Kein anderes Dokument gefunden.",
            "Freitext verknüpfen",
            "Organisation",
            "In Ordner verschieben",
            "In Ordner kopieren",
            "Dokument löschen",
            "Sicher löschen",
            "Smart Views / Sammelordner durchsuchen",
            "Fristen",
            "Bearbeiten bis",
            "Aufbewahren bis",
            "Frist fehlt",
            "Frist hinzufügen",
            "Versionen",
            "Datei freigeben",
            "HTTPS-Link erzeugen",
            "Freigabelinks",
            "Noch keine Freigabelinks.",
            "Logbuch dieses Dokuments",
            "Beleg buchen",
        ):
            self.assertIn(expected, body)


if __name__ == "__main__":
    unittest.main()
