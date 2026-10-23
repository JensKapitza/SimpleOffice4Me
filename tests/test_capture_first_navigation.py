"""Capture-first actions route mobile users to existing document upload flows."""
from pathlib import Path
import unittest


class CaptureFirstNavigationTests(unittest.TestCase):
    def test_documents_navigation_has_a_direct_capture_entry(self):
        navigation = Path("templates/documents/nav.html").read_text(encoding="utf-8")
        images = Path("templates/documents/images.html").read_text(encoding="utf-8")

        self.assertIn("url_for('documents.images', _anchor='capture-first')", navigation)
        self.assertIn('id="capture-first"', images)
        self.assertIn('href="#photo-snapshot-form"', images)
        self.assertIn('href="#mobile-photo-import"', images)
        self.assertIn("url_for('documents.inbox')", images)
        self.assertIn("url_for('v3_inbox.index')", images)

    def test_capture_offers_unclassified_photo_and_document_uploads(self):
        images = Path("templates/documents/images.html").read_text(encoding="utf-8")
        documents = Path("templates/documents/index.html").read_text(encoding="utf-8")
        snapshot = Path("templates/documents/_photo_snapshot.html").read_text(encoding="utf-8")

        self.assertIn('capture="environment"', snapshot)
        self.assertIn('id="capture-files" type="file" name="files" multiple required', documents)
        self.assertIn("url_for('documents.upload')", documents)
        self.assertIn("payload.duplicate", images)
        self.assertIn("identischer Inhalt bereits vorhanden", images)
        self.assertIn("später zuordnen", images)


if __name__ == "__main__":
    unittest.main()
