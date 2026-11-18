from __future__ import annotations

import io
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import ensure_auth_database, get_db
from app.fakturama_admin import MAX_REQUEST_BYTES
from app.fakturama_preview import (
    MAX_FILE_BYTES, MAX_ROWS, PreviewError, example_csv, preview_invoice_csv,
)


FIXTURE = Path(__file__).parent / "fixtures" / "fakturama" / "invoices-v1.csv"
BASE = "/admin/data-migration/fakturama"
TOKEN = "A" * 43


class FakturamaProjectionTests(unittest.TestCase):
    def test_preview_preserves_source_ids_money_and_invoice_buyer_snapshot(self):
        preview = preview_invoice_csv(FIXTURE.read_bytes())
        self.assertEqual({"recognized": 1, "unmapped": 1, "rejected": 1}, preview.counts)
        first, second, third = preview.rows
        self.assertEqual("000184", first.values["document_id"])
        self.assertEqual("000066", first.values["receiver_id"])
        self.assertEqual("000025", first.values["origin_contact_id"])
        self.assertEqual("000031", first.values["origin_address_id"])
        self.assertEqual("000100", first.values["source_document_id"])
        self.assertEqual("000160", first.values["invoice_reference_id"])
        self.assertEqual("Beispiel <Müller> GmbH", first.values["buyer_name"])
        self.assertEqual("01234", first.values["buyer_postal_code"])
        self.assertEqual(Decimal("119.2500"), first.amounts["total_value"])
        self.assertEqual("119.2500", str(first.amounts["total_value"]))
        self.assertEqual(Decimal("50.00"), first.amounts["paid_value"])
        self.assertEqual(("legacy_class", "buyer_postal_code"), second.unmapped)
        self.assertNotIn("legacy_class", second.values)
        self.assertEqual(("document_date", "total_value"), third.invalid)

    def test_duplicate_ids_reject_each_occurrence(self):
        content = FIXTURE.read_bytes()
        duplicate = content.splitlines(keepends=True)[2]
        preview = preview_invoice_csv(content + duplicate)
        self.assertEqual({"rejected": 3, "unmapped": 1}, preview.counts)
        self.assertTrue(preview.rows[0].duplicate)
        self.assertTrue(preview.rows[-1].duplicate)

    def test_accepts_utf8_bom_and_a_known_subset_of_columns(self):
        payload = "simpleoffice-fakturama-invoices;1;2.2.0\nreceiver_id;document_id\n00009;00003\n"
        result = preview_invoice_csv(payload.encode("utf-8-sig"))
        self.assertEqual({"unmapped": 1}, result.counts)
        self.assertEqual("00009", result.rows[0].values["receiver_id"])

    def test_malformed_rows_and_file_limits(self):
        payload = example_csv().encode()
        result = preview_invoice_csv(payload + b"extra;row\n")
        self.assertEqual(("column_count",), result.rows[0].invalid)
        for content, code in (
            (payload, "empty"),
            (payload + b"\xff", "encoding"),
            (payload + b'"unclosed', "syntax"),
            (payload.replace(b"2.2.0", b"2.1.0"), "profile"),
            (payload.replace(b"receiver_id", b"document_id"), "header"),
            (payload + b"x\n" * (MAX_ROWS + 1), "row_limit"),
            (b"x" * (MAX_FILE_BYTES + 1), "file_size"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(PreviewError, f"^{code}$"):
                preview_invoice_csv(content)


class FakturamaPreviewRouteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.root = base / "documents"
        saved = {name: app.config.get(name) for name in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        self.addCleanup(app.config.update, saved)
        app.config.update(DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(self.root), TESTING=True, TEST_CSRF_PROTECTION=True)
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            ids = {}
            for username, admin in (("migration-admin", 1), ("migration-regular", 0)):
                ids[username] = db.execute(
                    "INSERT INTO user(username,password,is_admin) VALUES (?,?,?)", (username, "unused", admin),
                ).lastrowid
            db.commit()
        self.admin = self._client(ids["migration-admin"])
        self.regular = self._client(ids["migration-regular"])

    @staticmethod
    def _client(user_id):
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(user_id=user_id, _csrf_token=TOKEN, simpleoffice_language="de")
        return client

    @staticmethod
    def _submit(client, content, token=TOKEN, **extra):
        return client.post(BASE, data={"_csrf_token": token, "invoice_csv": (io.BytesIO(content), "original-export.csv"), **extra})

    def test_admin_can_preview_and_download_template_without_persisting_business_data(self):
        page = self.admin.get(BASE)
        self.assertEqual(200, page.status_code)
        self.assertIn(b'invoice_csv', page.data)
        self.assertEqual("private, no-store", page.headers["Cache-Control"])
        template = self.admin.get(BASE + "/profile.csv")
        self.assertEqual("text/csv", template.mimetype)
        self.assertEqual(example_csv().encode(), template.data)
        self.assertIn("attachment", template.headers["Content-Disposition"])
        before = {path.relative_to(self.root) for path in self.root.rglob('*')}
        response = self._submit(self.admin, FIXTURE.read_bytes())
        self.assertEqual(200, response.status_code)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.assertIn(b"000184", response.data)
        self.assertIn(b"119.2500", response.data)
        self.assertIn(b"&lt;M", response.data)
        self.assertNotIn(b"legacy-sample", response.data)
        self.assertNotIn(b"original-export.csv", response.data)
        self.assertEqual(before, {path.relative_to(self.root) for path in self.root.rglob('*')})
        with app.app_context():
            audit = get_db().execute("SELECT detail FROM security_event WHERE target_id=?", ("fakturama_admin.preview",)).fetchall()
        self.assertTrue(audit)
        audited = "".join(row["detail"] for row in audit)
        for value in ("000184", "original-export.csv", "invoice_csv"):
            self.assertNotIn(value, audited)

    def test_access_csrf_and_upload_bounds(self):
        self.assertEqual(302, app.test_client().get(BASE).status_code)
        self.assertEqual(403, self.regular.get(BASE).status_code)
        self.assertEqual(403, self.regular.get(BASE + "/profile.csv").status_code)
        self.assertEqual(403, self._submit(self.regular, FIXTURE.read_bytes()).status_code)
        self.assertEqual(403, self._submit(self.admin, FIXTURE.read_bytes(), token="").status_code)
        self.assertEqual(415, self.admin.post(BASE, data={}, content_type="application/json").status_code)
        self.assertEqual(413, self._submit(self.admin, b"x" * (MAX_FILE_BYTES + 1)).status_code)
        with patch.object(app.request_class, "_load_form_data", side_effect=AssertionError("unexpected form parsing")):
            self.assertEqual(413, self._submit(self.admin, b"x" * MAX_REQUEST_BYTES).status_code)
        self.assertEqual(413, self._submit(self.admin, FIXTURE.read_bytes(), extra="too many parts").status_code)

    def test_body_limit_applies_to_terminated_wsgi_stream_with_short_declared_length(self):
        response = self.admin.post(
            BASE,
            data=b"x" * (MAX_REQUEST_BYTES + 1),
            content_type="multipart/form-data; boundary=preview-boundary",
            environ_overrides={"CONTENT_LENGTH": "100", "wsgi.input_terminated": True},
        )
        self.assertEqual(413, response.status_code)
