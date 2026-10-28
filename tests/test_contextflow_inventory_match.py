"""Exact per-candidate inventory match and guarded photo attach.

A series candidate carries its own optional identifier. Matching is exact
only (canonical ISBN-13 both sides, else exact stripped barcode/GTIN);
titles are never consulted. Attach revalidates server-side that the
identifier resolves uniquely to the claimed target. No new objects are
created, no external services are called, no peer data is involved.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from app import app
from app.contextflow.inventory_match import (
    IdentifierTooLongError,
    MalformedGtinError,
    find_exact_matches,
    resolve_unique_target,
)
from app.db import ensure_auth_database, get_db
from app.inventory import InventoryEnrichmentStore
from app.object_store import ObjectStore
from app.password_security import hash_password

# Smallest decodable PNG (1x1 transparent); accepted by Pillow validation.
PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class InventoryMatchAttachTest(unittest.TestCase):
    password = "contextflow-inventory-match-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE",
                "DOCUMENT_ROOT",
                "TESTING",
                "TEST_CSRF_PROTECTION",
                "CONTEXTFLOW_ENABLED",
            )
        }
        self.documents = str(Path(self.temp.name) / "documents")
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=self.documents,
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            CONTEXTFLOW_ENABLED=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin)"
                " VALUES (?,?,1)",
                ("owner", hash_password(self.password)),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin)"
                " VALUES (?,?,0)",
                ("berta", hash_password(self.password)),
            )
            berta_id = db.execute(
                "SELECT id FROM user WHERE username = 'berta'"
            ).fetchone()["id"]
            db.execute(
                "INSERT INTO user_permission(user_id,feature,enabled,updated_at)"
                " VALUES (?,?,0,?)",
                (berta_id, "documents", "2026-10-28T00:00:00+00:00"),
            )
            db.commit()
        store = ObjectStore(self.documents)
        self.book = store.create(
            {
                "name": "C Programming Language",
                "type": "book",
                "identifier": "9780131103627",
                "fields": {"isbn": "978-0-13-110362-7", "barcode": "9780131103627"},
            },
            "owner",
        )
        self.cable = store.create(
            {
                "name": "USB-C Kabel",
                "type": "object",
                "identifier": "CABLE-42",
                "fields": {"barcode": "CABLE-42"},
            },
            "owner",
        )
        self.tool_a = store.create(
            {
                "name": "Hammer A",
                "type": "tool",
                "identifier": "TOOL-001",
                "fields": {"barcode": "TOOL-001"},
            },
            "owner",
        )
        self.tool_b = store.create(
            {
                "name": "Hammer B",
                "type": "tool",
                "identifier": "TOOL-001",
                "fields": {"barcode": "TOOL-001"},
            },
            "owner",
        )
        self.upc = store.create(
            {
                "name": "UPC Widget",
                "type": "object",
                "fields": {"barcode": "042100005264"},
            },
            "owner",
        )
        self.gtin8 = store.create(
            {"name": "GTIN-8 Teil", "type": "object", "identifier": "12345670"},
            "owner",
        )
        self.gtin14 = store.create(
            {
                "name": "GTIN-14 Palette",
                "type": "object",
                "identifier": "12345678901231",
            },
            "owner",
        )
        self.owner = self.login("owner")
        self.berta = self.login("berta")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        client = app.test_client()
        client.post(
            "/auth/login",
            data={"username": username, "password": self.password},
        )
        return client

    def csrf(self, client) -> str:
        response = client.get("/inventory")
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group(1).decode()

    def photo_upload(self, data: bytes = PNG_1PX, name: str = "foto.png"):
        return (BytesIO(data), name, "image/png")

    def test_match_unique_isbn_normalizes_both_sides(self) -> None:
        response = self.owner.post(
            "/contextflow/inventory-match", data={"identifier": "0-13-110362-8"}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "unique")
        self.assertEqual(payload["kind"], "isbn")
        self.assertEqual(payload["matches"][0]["object_id"], self.book["object_id"])

    def test_match_unique_barcode_exact(self) -> None:
        response = self.owner.post(
            "/contextflow/inventory-match", data={"identifier": "CABLE-42"}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "unique")
        self.assertEqual(payload["kind"], "barcode")
        self.assertEqual(payload["matches"][0]["object_id"], self.cable["object_id"])

    def test_match_none_unknown(self) -> None:
        response = self.owner.post(
            "/contextflow/inventory-match", data={"identifier": "NOPE-999"}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "none")
        self.assertEqual(payload["matches"], [])

    def test_match_never_fuzzy_title_or_case(self) -> None:
        for query in ("Hammer", "hammer", "CABLE", "cable-42", "C Programming"):
            with self.subTest(query=query):
                response = self.owner.post(
                    "/contextflow/inventory-match", data={"identifier": query}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["status"], "none")

    def test_match_ambiguous_same_barcode(self) -> None:
        response = self.owner.post(
            "/contextflow/inventory-match", data={"identifier": "TOOL-001"}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ambiguous")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(
            {hit["object_id"] for hit in payload["matches"]},
            {self.tool_a["object_id"], self.tool_b["object_id"]},
        )

    def test_match_empty_identifier_400(self) -> None:
        for data in ({"identifier": ""}, {"identifier": "   "}, {}):
            with self.subTest(data=data):
                response = self.owner.post("/contextflow/inventory-match", data=data)
                self.assertEqual(response.status_code, 400)

    def test_routes_404_when_flag_off(self) -> None:
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            match = self.owner.post(
                "/contextflow/inventory-match", data={"identifier": "CABLE-42"}
            )
            attach = self.owner.post(
                "/contextflow/inventory-attach",
                data={"identifier": "CABLE-42", "item_id": self.cable["object_id"]},
                content_type="multipart/form-data",
            )
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)
        self.assertEqual(match.status_code, 404)
        self.assertEqual(attach.status_code, 404)

    def test_routes_denied_without_documents_feature(self) -> None:
        match = self.berta.post(
            "/contextflow/inventory-match", data={"identifier": "CABLE-42"}
        )
        attach = self.berta.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "CABLE-42",
                "item_id": self.cable["object_id"],
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(match.status_code, 403)
        self.assertEqual(attach.status_code, 403)

    def test_routes_redirect_when_anonymous(self) -> None:
        client = app.test_client()
        match = client.post(
            "/contextflow/inventory-match", data={"identifier": "CABLE-42"}
        )
        attach = client.post(
            "/contextflow/inventory-attach",
            data={"identifier": "CABLE-42", "item_id": self.cable["object_id"]},
            content_type="multipart/form-data",
        )
        for response in (match, attach):
            self.assertEqual(response.status_code, 302)
            self.assertIn("login", response.headers["Location"].lower())

    def test_csrf_enforced_and_passing(self) -> None:
        app.config.update(TEST_CSRF_PROTECTION=True)
        try:
            denied_match = self.owner.post(
                "/contextflow/inventory-match", data={"identifier": "CABLE-42"}
            )
            denied_attach = self.owner.post(
                "/contextflow/inventory-attach",
                data={"identifier": "CABLE-42", "item_id": self.cable["object_id"]},
                content_type="multipart/form-data",
            )
            token = self.csrf(self.owner)
            allowed_match = self.owner.post(
                "/contextflow/inventory-match",
                data={"identifier": "CABLE-42", "_csrf_token": token},
            )
            allowed_attach = self.owner.post(
                "/contextflow/inventory-attach",
                data={
                    "identifier": "CABLE-42",
                    "item_id": self.cable["object_id"],
                    "_csrf_token": token,
                    "photo": self.photo_upload(),
                },
                content_type="multipart/form-data",
            )
        finally:
            app.config.update(TEST_CSRF_PROTECTION=False)
        self.assertEqual(denied_match.status_code, 403)
        self.assertEqual(denied_attach.status_code, 403)
        self.assertEqual(allowed_match.status_code, 200)
        self.assertEqual(allowed_match.get_json()["status"], "unique")
        self.assertEqual(allowed_attach.status_code, 200)
        self.assertTrue(allowed_attach.get_json()["ok"])

    def test_attach_success_unique(self) -> None:
        before = len(ObjectStore(self.documents).objects())
        response = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "0-13-110362-8",
                "item_id": self.book["object_id"],
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["object_id"], self.book["object_id"])
        self.assertRegex(payload["filename"], r"^[0-9a-f]{32}\.png$")
        meta = InventoryEnrichmentStore(self.documents).object_meta(
            self.book["object_id"]
        )
        self.assertEqual(len(meta.get("photos", [])), 1)
        self.assertEqual(meta["photos"][0]["filename"], payload["filename"])
        stored = InventoryEnrichmentStore(self.documents).media_path(
            self.book["object_id"], payload["filename"]
        )
        self.assertTrue(stored.is_file())
        after = len(ObjectStore(self.documents).objects())
        self.assertEqual(before, after)

    def test_attach_rejects_zero_match(self) -> None:
        response = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "NOPE-999",
                "item_id": "00000000-0000-0000-0000-000000000000",
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 404)

    def test_attach_rejects_ambiguous_target(self) -> None:
        response = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "TOOL-001",
                "item_id": self.tool_a["object_id"],
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 409)

    def test_attach_rejects_target_mismatch(self) -> None:
        wrong_item = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "9780131103627",
                "item_id": self.cable["object_id"],
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        unknown_item = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "9780131103627",
                "item_id": "00000000-0000-0000-0000-000000000000",
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(wrong_item.status_code, 409)
        self.assertEqual(unknown_item.status_code, 409)

    def test_attach_rejects_missing_or_invalid_photo(self) -> None:
        missing = self.owner.post(
            "/contextflow/inventory-attach",
            data={"identifier": "CABLE-42", "item_id": self.cable["object_id"]},
            content_type="multipart/form-data",
        )
        invalid = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "CABLE-42",
                "item_id": self.cable["object_id"],
                "photo": (BytesIO(b"not-an-image"), "x.txt", "text/plain"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(invalid.status_code, 400)

    def test_attach_requires_identifier(self) -> None:
        response = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "   ",
                "item_id": self.book["object_id"],
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_series_match_attach_ui_wiring(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "static" / "js" / "inventory.js").read_text(encoding="utf-8")
        self.assertIn('data-part="check"', source)
        self.assertIn('data-part="attach"', source)
        self.assertIn('data-part="match"', source)
        self.assertIn("dataset.matchUrl", source)
        self.assertIn("dataset.attachUrl", source)
        self.assertIn("const seriesCheck = async (id) => {", source)
        self.assertIn("const seriesAttach = async (id) => {", source)
        self.assertIn("item.match", source)
        self.assertIn("Im Inventar prüfen", source)
        self.assertIn("Foto anhängen", source)

    def test_match_attach_urls_rendered(self) -> None:
        response = self.owner.get("/inventory")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-match-url="/contextflow/inventory-match"', response.data)
        self.assertIn(b'data-attach-url="/contextflow/inventory-attach"', response.data)

    def test_match_upc_ean_zero_prefix_equivalence(self) -> None:
        for query in ("0042100005264", "042100005264"):
            with self.subTest(query=query):
                response = self.owner.post(
                    "/contextflow/inventory-match", data={"identifier": query}
                )
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["status"], "unique")
                self.assertEqual(payload["kind"], "gtin")
                self.assertEqual(
                    payload["matches"][0]["object_id"], self.upc["object_id"]
                )

    def test_match_valid_gtin8_and_gtin14(self) -> None:
        for query, item in (
            ("12345670", self.gtin8),
            ("12345678901231", self.gtin14),
        ):
            with self.subTest(query=query):
                response = self.owner.post(
                    "/contextflow/inventory-match", data={"identifier": query}
                )
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["status"], "unique")
                self.assertEqual(payload["kind"], "gtin")
                self.assertEqual(
                    payload["matches"][0]["object_id"], item["object_id"]
                )

    def test_match_rejects_bad_gtin_check_digit(self) -> None:
        for query in ("12345671", "4006381333932"):
            with self.subTest(query=query):
                response = self.owner.post(
                    "/contextflow/inventory-match", data={"identifier": query}
                )
                self.assertEqual(response.status_code, 400)

    def test_attach_rejects_bad_gtin_check_digit(self) -> None:
        response = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "12345671",
                "item_id": self.book["object_id"],
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        meta = InventoryEnrichmentStore(self.documents).object_meta(
            self.book["object_id"]
        )
        self.assertEqual(meta.get("photos", []), [])

    def test_match_rejects_overlong_identifier(self) -> None:
        too_long = self.owner.post(
            "/contextflow/inventory-match", data={"identifier": "x" * 121}
        )
        boundary = self.owner.post(
            "/contextflow/inventory-match", data={"identifier": "y" * 120}
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(boundary.status_code, 200)
        self.assertEqual(boundary.get_json()["status"], "none")

    def test_attach_rejects_overlong_identifier(self) -> None:
        too_long = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "x" * 121,
                "item_id": self.book["object_id"],
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        boundary = self.owner.post(
            "/contextflow/inventory-attach",
            data={
                "identifier": "y" * 120,
                "item_id": self.book["object_id"],
                "photo": self.photo_upload(),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(boundary.status_code, 404)
        meta = InventoryEnrichmentStore(self.documents).object_meta(
            self.book["object_id"]
        )
        self.assertEqual(meta.get("photos", []), [])

    def test_custom_barcode_exact_and_whitespace(self) -> None:
        padded = self.owner.post(
            "/contextflow/inventory-match", data={"identifier": "CABLE-42 "}
        )
        self.assertEqual(padded.status_code, 200)
        self.assertEqual(padded.get_json()["status"], "unique")
        self.assertEqual(
            padded.get_json()["matches"][0]["object_id"], self.cable["object_id"]
        )
        doubled = self.owner.post(
            "/contextflow/inventory-match", data={"identifier": "CABLE--42"}
        )
        self.assertEqual(doubled.status_code, 200)
        self.assertEqual(doubled.get_json()["status"], "none")

    def test_malformed_and_overlong_raise_typed_errors(self) -> None:
        with self.assertRaises(MalformedGtinError):
            find_exact_matches(self.documents, "12345671")
        with self.assertRaises(IdentifierTooLongError):
            find_exact_matches(self.documents, "x" * 121)
        with self.assertRaises(MalformedGtinError):
            resolve_unique_target(self.documents, "12345671", self.book["object_id"])
        with self.assertRaises(IdentifierTooLongError):
            resolve_unique_target(self.documents, "x" * 121, self.book["object_id"])


if __name__ == "__main__":
    unittest.main()
