"""Capture inbox UI wiring: duplicate linking, upload resume, entries (767)."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CaptureUiWiringTests(unittest.TestCase):
    def test_duplicate_adopt_offers_link_to_existing_object(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("Trotzdem zuordnen", script)
        self.assertIn("linkDuplicate", script)
        self.assertIn("Foto übernommen", script)
        self.assertIn("entry.assign_url", script)

    def test_multi_upload_continues_after_single_failure(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("failures.push", script)
        self.assertIn("von ${list.length} Fotos gespeichert", script)
        self.assertIn("renderEntry(payload.entry)", script)

    def test_library_links_to_photo_inbox(self) -> None:
        library = (ROOT / "templates/library/index.html").read_text(encoding="utf-8")
        self.assertIn("inventory.capture_inbox", library)
        self.assertIn("Fotoeingang", library)
        inventory = (ROOT / "templates/inventory/index.html").read_text(encoding="utf-8")
        self.assertIn("inventory.capture_inbox", inventory)

    def test_photo_role_select_posts_to_role_url(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("Fotorolle", script)
        self.assertIn("ROLE_LABELS", script)
        self.assertIn("entry.role_url", script)
        self.assertIn("Buchrücken", script)

    def test_batch_review_assigns_group_to_existing_book(self) -> None:
        script = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("Vorhandene Buch-ID", script)
        self.assertIn("Ganze Gruppe vorhandenem Buch zuordnen", script)
        self.assertIn("payload.assign_url", script)
        self.assertIn("Fotos, ", script)

    def test_ml_status_uses_shared_formatter_with_text_assignment(self) -> None:
        template = (ROOT / "templates/inventory/capture.html").read_text(encoding="utf-8")
        helper_pos = template.index("js/capture_ml_status.js")
        inbox_pos = template.index("js/capture_inbox.js")
        self.assertLess(helper_pos, inbox_pos)
        inbox = (ROOT / "static/js/capture_inbox.js").read_text(encoding="utf-8")
        self.assertIn("SimpleOfficeCaptureMl.formatMlStatus(payload)", inbox)
        self.assertIn("mlLine.textContent = window.SimpleOfficeCaptureMl", inbox)
        self.assertNotIn("mlLine.innerHTML", inbox)


class CaptureMlStatusNodeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_ml_status_lines_for_meta_and_ollama(self) -> None:
        result = subprocess.run(
            [shutil.which("node"), "--test",
             str(ROOT / "tests" / "capture_ml_status.test.cjs")],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
