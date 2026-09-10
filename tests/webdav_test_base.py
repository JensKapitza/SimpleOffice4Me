import base64
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from unittest import mock
from xml.etree import ElementTree

from app import app
from app.attachment_security import ClamAV, ScanResult
from app.db import ensure_auth_database
from app.document_store import CONTROL_DIR, POLICY_FILE, DocumentStore
from app.webdav import MAX_ACTIVE_CREDENTIALS, activate, credentials_for, revoke, rotate
from app.virtual_filesystem import VirtualFileSystem


class WebDavTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "MAX_CONTENT_LENGTH", "WEBDAV_QUOTA_BYTES", "WEBDAV_UPLOAD_SCAN", "WEBDAV_QUARANTINE_BYTES")}
        root = Path(self.temp.name) / "documents"
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(root), MAX_CONTENT_LENGTH=1024 * 1024, WEBDAV_QUOTA_BYTES=0, WEBDAV_UPLOAD_SCAN=False, WEBDAV_QUARANTINE_BYTES=1024 * 1024)
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "jens", "password": "browser-passwort"})
        self.client.post("/auth/login", data={"username": "jens", "password": "browser-passwort"})
        root.mkdir(parents=True, exist_ok=True)
        (root / "angebot.odt").write_bytes(b"first office version")
        self.store = DocumentStore(root)
        self.store.scan()
        self.document = self.store.get_document("angebot.odt")
        with app.test_request_context():
            self.password = activate("jens", "jens", label="LibreOffice Test", expires_days=30)
        token = base64.b64encode(f"jens:{self.password}".encode()).decode()
        self.auth = {"Authorization": f"Basic {token}"}
        self.url = f"/webdav/documents/jens/{self.document['document_id']}--angebot.odt"
        self.files = "/webdav/files/jens"
        self.lock_body = "<d:lockinfo xmlns:d='DAV:'><d:lockscope><d:exclusive/></d:lockscope><d:locktype><d:write/></d:locktype><d:owner>LibreOffice</d:owner></d:lockinfo>"

    def search(self, *, scope=None, depth="infinity", select="<d:displayname/><d:getetag/>", where="", orderby="", limit="", auth=None, content_type="application/xml", endpoint=None):
        body = f'''<d:searchrequest xmlns:d="DAV:" xmlns:t="urn:simpleoffice:test">
          <d:basicsearch>
            <d:select><d:prop>{select}</d:prop></d:select>
            <d:from><d:scope><d:href>{scope or self.files}</d:href><d:depth>{depth}</d:depth></d:scope></d:from>
            {f'<d:where>{where}</d:where>' if where else ''}
            {orderby}
            {f'<d:limit><d:nresults>{limit}</d:nresults></d:limit>' if limit != '' else ''}
          </d:basicsearch>
        </d:searchrequest>'''
        return self.client.open(
            endpoint or self.files, method="SEARCH", data=body,
            headers={**(auth or self.auth), "Content-Type": content_type},
        )

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()


__all__ = [name for name in globals() if not name.startswith("__")]
