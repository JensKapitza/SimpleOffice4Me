"""Exercise private SMS archive UI using synthetic messages only."""
from io import BytesIO
from pathlib import Path

from app import app
from app.db import get_db
from app.sms_archive_store import SmsArchive

from tests.http_api_fixture import PublicHttpTestCase


class SmsArchiveRoutesTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(app.config["DOCUMENT_ROOT"])
        self.base = "/communication/sms"
        self.source = "a" * 32
        self.first = {
            "source": self.source, "provider_id": 1, "date_ms": 1750000001000,
            "direction": "inbox", "address": "Synthetic sender", "body": "Privat <b>Text</b>",
        }
        self.second = {
            **self.first, "provider_id": 2, "date_ms": 1750000002000,
            "direction": "sent", "body": "Other synthetic message",
        }
        SmsArchive(self.root, "owner").append([self.first, self.second])
        self.csrf = self._csrf_headers(self.client, self.base)

    def _download(self):
        return self.client.post(self.base + "/export", data={"selected": f"{self.source}:1"}, headers=self.csrf)

    def _upload(self, client, csrf, data, *, confirmed=True):
        form = {"backup": (BytesIO(data), "example.json")}
        if confirmed:
            form["confirm"] = "1"
        return client.post(self.base + "/restore", data=form, headers=csrf, follow_redirects=confirmed)

    def test_private_search_and_chosen_recovery_file_restore_without_duplicates(self):
        self.assertIn(self.base, self.client.get("/tasks/").get_data(as_text=True))
        response = self.client.post(self.base, data={"query": "pRIVAT"}, headers=self.csrf)
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual("no-referrer", response.headers["Referrer-Policy"])
        self.assertIn("Privat &lt;b&gt;Text&lt;/b&gt;", html)
        self.assertNotIn("Other synthetic message", html)
        self.assertNotIn("?", response.request.path)
        export = self._download()
        self.assertEqual(export.status_code, 200)
        self.assertIn("attachment", export.headers["Content-Disposition"])
        self.assertIn("no-store", export.headers["Cache-Control"])
        self.assertFalse(b"Other synthetic message" in export.data)

        restored_root = self.root.parent / "replacement-root"
        app.config["DOCUMENT_ROOT"] = str(restored_root)
        self.assertEqual(self._upload(self.client, self.csrf, export.data, confirmed=False).status_code, 400)
        before = SmsArchive(restored_root, "owner").page()
        self.assertEqual([], before)
        for _ in range(2):
            self.assertEqual(self._upload(self.client, self.csrf, export.data).status_code, 200)
        self.assertEqual([self.first], SmsArchive(restored_root, "owner").page())
        self.assertEqual(2, len(SmsArchive(self.root, "owner").page()))

    def test_other_account_cannot_list_export_or_restore_owner_messages(self):
        backup = self._download().data
        self._register(self.client, "other")
        other = app.test_client()
        self._login(other, "other")
        csrf = self._csrf_headers(other, self.base)
        self.assertNotIn("Synthetic sender", other.get(self.base).get_data(as_text=True))
        response = other.post(self.base + "/export", data={"selected": f"{self.source}:1"}, headers=csrf)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self._upload(other, csrf, backup).status_code, 200)
        self.assertEqual([], SmsArchive(self.root, "other").page())
        self.assertEqual(2, len(SmsArchive(self.root, "owner").page()))

        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO user_permission(user_id,feature,enabled,updated_at) "
                "SELECT id,'mail',0,CURRENT_TIMESTAMP FROM user WHERE username=?", ("other",),
            )
            db.commit()
        self.assertEqual(other.get(self.base).status_code, 403)
        self.assertEqual(other.post(self.base + "/export", data={"selected": f"{self.source}:1"}, headers=csrf).status_code, 403)

    def test_csrf_required_bounds_and_signed_out_do_not_return_data(self):
        self.assertEqual(app.test_client().get(self.base).status_code, 302)
        bad = self.client.post(self.base + "/export", data={"selected": f"{self.source}:1"})
        self.assertEqual(bad.status_code, 403)
        big = self.client.post(self.base, data={"query": "a" * 13000}, headers=self.csrf)
        self.assertEqual(big.status_code, 413)
        unsupported = self.client.post(self.base, json={"query": "a"}, headers=self.csrf)
        self.assertEqual(unsupported.status_code, 415)
        invalid = self.client.post(self.base + "/export", data={"selected": f"{self.source}:9999999999999999999"}, headers=self.csrf)
        self.assertEqual(invalid.status_code, 400)

    def test_pagination_preserves_search_without_url_parameter(self):
        records = [
            {**self.first, "provider_id": n, "date_ms": self.first["date_ms"] + n, "body": f"paging {n:02d}"}
            for n in range(10, 37)
        ]
        SmsArchive(self.root, "owner").append(records)
        first = self.client.post(self.base, data={"query": "paging", "page": 0}, headers=self.csrf).get_data(as_text=True)
        tail = self.client.post(self.base, data={"query": "paging", "page": 1}, headers=self.csrf).get_data(as_text=True)
        self.assertIn("paging 36", first)
        self.assertNotIn("paging 10", first)
        self.assertIn("paging 10", tail)
        self.assertNotIn("paging 36", tail)
