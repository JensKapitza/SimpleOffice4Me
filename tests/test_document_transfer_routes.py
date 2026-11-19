"""Authenticated QR transfer pages preserve VFS and storage authorization."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import ensure_auth_database
from app.document_store import DocumentStore
from app.document_transfer_packet import decode_packet


class DocumentTransferRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "documents"
        previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        self.addCleanup(app.config.update, previous)
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "transfer-test", "password": "transfer-test-password"})
        self.client.post("/auth/login", data={"username": "transfer-test", "password": "transfer-test-password"})
        self.store = DocumentStore(self.root)
        self.store.initialize()

    def _document(self, name: str, data: bytes):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.store._scan_file(path, force_hash=True)
        return self.store.get_document(path)

    def test_tiny_file_packet_is_embedded_and_sent_as_audio_payload(self):
        document = self._document("tiny.txt", b"x")
        response = self.client.post(
            f"/documents/{document['document_id']}/transfer",
            data={"mode": "file"},
        )
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        start = html.index('data-qr-payload>') + len('data-qr-payload>')
        packet = html[start:html.index("</pre>", start)].strip()
        decoded = decode_packet(packet)
        self.assertEqual(b"x", decoded["data"])
        self.assertIn(f'>{packet}</textarea>', html)
        self.assertNotIn("authenticated download link instead", html)

    def test_larger_inline_packet_uses_download_link_for_audio_and_shows_notice(self):
        document = self._document("somewhat-larger.txt", b"z" * 48)
        response = self.client.post(
            f"/documents/{document['document_id']}/transfer",
            data={"mode": "file"},
        )
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("The QR contains the file itself. Sound sends its authenticated download link instead.", html)
        self.assertIn("http://localhost", html)
        self.assertNotIn("SO4ME1|somewhat-larger.txt", html.split("data-acoustic-text", 1)[1].split(">", 1)[1].split("</textarea>", 1)[0])

    def test_large_file_qr_is_authenticated_link_and_download_stays_authorized(self):
        document = self._document("large.bin", b"a" * 1200)
        response = self.client.post(
            f"/documents/{document['document_id']}/transfer",
            data={"mode": "file"},
        )
        self.assertEqual(200, response.status_code)
        html = response.get_data(as_text=True)
        self.assertIn("File is too large for a self-contained QR", html)
        self.assertIn(f"http://localhost/documents/{document['document_id']}/transfer/download", html)
        downloaded = self.client.get(f"/documents/{document['document_id']}/transfer/download")
        self.assertEqual(200, downloaded.status_code)
        self.assertEqual(b"a" * 1200, downloaded.data)

    def test_path_mode_checks_virtual_file_read_acl(self):
        document = self._document("restricted/path.txt", b"path")
        with patch("app.documents_routes_transfer.VirtualFileSystem.allows", return_value=False):
            page = self.client.get(f"/documents/{document['document_id']}/transfer")
            download = self.client.get(f"/documents/{document['document_id']}/transfer/download")
        self.assertEqual(404, page.status_code)
        self.assertEqual(404, download.status_code)

    def test_infected_file_is_ineligible_for_all_transfer_modes(self):
        document = self._document("blocked.bin", b"test fixture")
        self.store.set_malware_scan(document["document_id"], {"verdict": "infected"}, "transfer-test")
        address = f"/documents/{document['document_id']}/transfer"
        for mode in ("file", "path", "link"):
            self.assertEqual(404, self.client.post(address, data={"mode": mode}).status_code)
        self.assertEqual(404, self.client.get(address + "/download").status_code)
