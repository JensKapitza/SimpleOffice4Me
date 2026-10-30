"""Capture inbox gap coverage: oversize, 503 routes, orphans, auth matrix.

Issues #767/#658: route-level regression tests for gaps L1-L4 —
oversize rejection per route, 503 on batch-assign/role, no orphaned
media on failed writes, and the login/CSRF matrix for all 12 routes.
"""

from __future__ import annotations

import io
import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.document_store import CONTROL_DIR
from app.inventory import MAX_PHOTO_BYTES
from app.password_security import hash_password
from tests.test_contextflow_inventory_match import PNG_1PX

BATCH_A = "aa" * 16
ENTRY_NONE = "0" * 32


class CaptureGapBase(unittest.TestCase):
    password = "capture-gap-test-password"
    keys = ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in self.keys}
        self.documents = str(Path(self.temp.name) / "documents")
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=self.documents,
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
                ("anna", hash_password(self.password), "Anna", 1),
            )
            db.commit()
        self.client = self.login("anna")
        self.token_counter = 0

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        client = app.test_client()
        token = self.login_page_token(client)
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": token,
            },
        )
        return client

    def login_page_token(self, client) -> str:
        page = client.get("/auth/login")
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group(1).decode()

    def csrf(self) -> str:
        page = self.client.get("/inventory/capture")
        match = re.search(rb'data-csrf="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group(1).decode()

    def next_token(self) -> str:
        self.token_counter += 1
        return f"{self.token_counter:032x}"[-32:]

    def upload(self, data: bytes = PNG_1PX, batch_id: str = ""):
        form = {
            "photo": (io.BytesIO(data), "foto.png"),
            "client_request_id": self.next_token(),
            "_csrf_token": self.csrf(),
        }
        if batch_id:
            form["batch_id"] = batch_id
        return self.client.post(
            "/inventory/capture/upload",
            data=form,
            content_type="multipart/form-data",
        )

    def index_path(self) -> Path:
        return (
            Path(self.documents) / CONTROL_DIR / "inventory"
            / "capture-inbox.json"
        )

    def media_files(self) -> list[str]:
        base = (
            Path(self.documents) / CONTROL_DIR / "inventory"
            / "media" / "inbox"
        )
        if not base.exists():
            return []
        return sorted(
            str(item.relative_to(base))
            for item in base.rglob("*")
            if item.is_file()
        )

    def corrupt_index(self) -> bytes:
        path = self.index_path()
        broken = path.read_bytes() + b"\n{kaputt"
        path.write_bytes(broken)
        return broken


class CaptureOversizeTests(CaptureGapBase):
    """L1: >12 MiB uploads are rejected per route before any write."""

    def test_stateless_analyze_rejects_oversize_photo(self) -> None:
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_PHOTO_BYTES
        response = self.client.post(
            "/inventory/capture/analyze",
            data={
                "photo": (io.BytesIO(big), "gross.png"),
                "photo_id": "photo-big",
                "_csrf_token": self.csrf(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("12 MiB", response.get_json()["error"])

    def test_persisting_upload_rejects_oversize_without_entry(self) -> None:
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_PHOTO_BYTES
        response = self.client.post(
            "/inventory/capture/upload",
            data={
                "photo": (io.BytesIO(big), "gross.png"),
                "client_request_id": self.next_token(),
                "_csrf_token": self.csrf(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("12 MiB", response.get_json()["error"])
        self.assertFalse(self.index_path().exists())
        self.assertEqual([], self.media_files())


class CaptureRoute503Tests(CaptureGapBase):
    """L2: batch-assign and role fail closed with 503 on corrupt index."""

    def test_batch_assign_returns_503_without_mutation(self) -> None:
        from app.object_store import ObjectStore

        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.assertEqual(BATCH_A, entry["batch_id"])
        with app.app_context():
            book = ObjectStore(self.documents).create(
                {"name": "Der Musterroman", "type": "book"}, "anna")
        token = self.csrf()
        broken = self.corrupt_index()
        response = self.client.post(
            f"/inventory/capture/batch/{BATCH_A}/assign",
            data={"object_id": book["object_id"], "_csrf_token": token},
        )
        self.assertEqual(503, response.status_code)
        self.assertIn(
            "vorübergehend nicht verfügbar",
            response.get_json()["error"],
        )
        self.assertEqual(broken, self.index_path().read_bytes())

    def test_role_returns_503_without_mutation(self) -> None:
        entry = self.upload(PNG_1PX).get_json()["entry"]
        token = self.csrf()
        broken = self.corrupt_index()
        response = self.client.post(
            f"/inventory/capture/{entry['entry_id']}/role",
            data={"role": "title", "_csrf_token": token},
        )
        self.assertEqual(503, response.status_code)
        self.assertIn(
            "vorübergehend nicht verfügbar",
            response.get_json()["error"],
        )
        self.assertEqual(broken, self.index_path().read_bytes())


class CaptureOrphanTests(CaptureGapBase):
    """L3: failed writes leave no orphaned media on route level."""

    def test_corrupt_upload_leaves_no_media(self) -> None:
        self.assertEqual([], self.media_files())
        response = self.client.post(
            "/inventory/capture/upload",
            data={
                "photo": (io.BytesIO(b"kein-bild"), "foto.png"),
                "client_request_id": self.next_token(),
                "_csrf_token": self.csrf(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(400, response.status_code)
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual([], self.media_files())
        self.assertFalse(self.index_path().exists())

    def test_batch_adopt_503_leaves_media_untouched(self) -> None:
        entry = self.upload(PNG_1PX, BATCH_A).get_json()["entry"]
        self.assertEqual(BATCH_A, entry["batch_id"])
        before = self.media_files()
        self.assertTrue(before)
        token = self.csrf()
        broken = self.corrupt_index()
        response = self.client.post(
            f"/inventory/capture/batch/{BATCH_A}/adopt",
            data={
                "title": "T",
                "item_type": "object",
                "capture_request_id": self.next_token(),
                "_csrf_token": token,
            },
        )
        self.assertEqual(503, response.status_code)
        self.assertEqual(before, self.media_files())
        self.assertEqual(broken, self.index_path().read_bytes())


class CaptureAuthMatrixTests(CaptureGapBase):
    """L4: login gate on all 12 routes, CSRF gate on all 9 mutations."""

    post_routes = (
        "/inventory/capture/upload",
        f"/inventory/capture/{ENTRY_NONE}/assign",
        f"/inventory/capture/{ENTRY_NONE}/analyze",
        "/inventory/capture/analyze",
        f"/inventory/capture/batch/{BATCH_A}/analyze",
        f"/inventory/capture/batch/{BATCH_A}/adopt",
        f"/inventory/capture/batch/{BATCH_A}/assign",
        f"/inventory/capture/{ENTRY_NONE}/batch",
        f"/inventory/capture/{ENTRY_NONE}/role",
    )
    get_routes = (
        "/inventory/capture",
        f"/inventory/capture/{ENTRY_NONE}/preview",
        f"/inventory/capture/batch/{BATCH_A}",
    )

    def test_anonymous_gets_redirect_to_login(self) -> None:
        anonymous = app.test_client()
        for url in self.get_routes:
            with self.subTest(url=url):
                self.assertEqual(302, anonymous.get(url).status_code)

    def test_anonymous_posts_redirect_behind_csrf(self) -> None:
        # CSRF runs in before_request, login_required in the view:
        # an anonymous session token passes CSRF, then login redirects.
        anonymous = app.test_client()
        token = self.login_page_token(anonymous)
        for url in self.post_routes:
            with self.subTest(url=url):
                response = anonymous.post(
                    url, data={"_csrf_token": token})
                self.assertEqual(302, response.status_code)

    def test_logged_in_posts_without_csrf_are_forbidden(self) -> None:
        for url in self.post_routes:
            with self.subTest(url=url):
                response = self.client.post(url, data={})
                self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
