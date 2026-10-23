"""Rental report generation and downloads enforce authorization and integrity."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db
from app.rental_billing import RentalBillingStore


class RentalReportDownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "documents"
        self.root.mkdir()
        config = ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        previous = {key: app.config.get(key) for key in config}
        self.addCleanup(lambda: app.config.update(previous))
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(self.base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        with app.app_context():
            db.ensure_auth_database()
            connection = db.get_db()
            for username, is_admin in (("admin", 1), ("member", 0)):
                connection.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash("synthetic-rental-password"), is_admin),
                )
            connection.commit()
        self.store = RentalBillingStore(self.root)
        self.store._object = lambda object_id: {
            "object_id": object_id, "name": "Unit", "identifier": "", "location": "", "type": "Wohnung",
        }
        self.store._contact = lambda contact_id: {
            "contact_id": contact_id, "fields": {"display_name": "Tenant", "email": ""},
        }
        self.store.add_tenancy("unit-1", "tenant-1", "2026-01-01", "", "admin")
        settlement = self.store.create_settlement(
            "2026", 2026, "2026-01-01", "2026-12-31", "admin", object_id="unit-1",
        )
        self.settlement_id = settlement["settlement_id"]
        self.store.add_cost(
            self.settlement_id, "Water", "Receipt", "100", "2026-01-01", "2026-12-31",
            "equal", "admin", source_note="synthetic manual cost",
        )
        self.client = app.test_client()
        self.client.post("/auth/login", data={"username": "admin", "password": "synthetic-rental-password"})
        self.member_client = app.test_client()
        self.member_client.post("/auth/login", data={"username": "member", "password": "synthetic-rental-password"})
        self.store.approve(self.settlement_id, "admin")
        self.directory = self.store.approval_directory(self.settlement_id)
        self.file_url = (
            f"/rentals/settlements/{self.settlement_id}/files/"
            "Vermieter-Abrechnungsblatt.pdf"
        )
        self.package_url = (
            f"/rentals/settlements/{self.settlement_id}/tenant/tenant-1/package"
        )

    def request_with_store(self, method, url, **kwargs):
        with patch("app.rentals._store", return_value=self.store):
            return getattr(self.client, method)(url, **kwargs)

    def test_approved_pdf_and_tenant_package_download_with_private_headers(self):
        for url, path in (
            (self.file_url, self.directory / "Vermieter-Abrechnungsblatt.pdf"),
            (self.package_url, self.directory / "Mieterpaket-tenant-1.zip"),
        ):
            with self.subTest(url=url):
                response = self.request_with_store("get", url)
                try:
                    self.assertEqual(200, response.status_code)
                    self.assertEqual(path.read_bytes(), response.data)
                    self.assertIn("attachment;", response.headers["Content-Disposition"])
                    self.assertEqual("private, no-store", response.headers["Cache-Control"])
                finally:
                    response.close()

    def test_modified_approved_artifact_is_not_downloaded(self):
        path = self.directory / "Vermieter-Abrechnungsblatt.pdf"
        original = path.read_bytes()
        path.write_bytes(b"tampered report")
        self.assertEqual(404, self.request_with_store("get", self.file_url).status_code)
        path.write_bytes(original)

        package = self.directory / "Mieterpaket-tenant-1.zip"
        original_package = package.read_bytes()
        package.write_bytes(b"tampered package")
        response = self.request_with_store("get", self.package_url, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Freigegebene Abrechnungsdatei ist beschädigt", response.get_data(as_text=True))
        self.assertNotIn(b"tampered package", response.data)
        package.write_bytes(original_package)

    def test_modified_manifest_or_snapshot_blocks_report_download(self):
        manifest = self.directory / "approval-manifest.json"
        original_manifest = manifest.read_bytes()
        manifest.write_text("{}", encoding="utf-8")
        self.assertEqual(404, self.request_with_store("get", self.file_url).status_code)
        manifest.write_bytes(original_manifest)

        snapshot = self.directory / "snapshot.json"
        original_snapshot = snapshot.read_bytes()
        snapshot.write_bytes(original_snapshot + b" ")
        self.assertEqual(404, self.request_with_store("get", self.file_url).status_code)
        snapshot.write_bytes(original_snapshot)

    def test_approval_files_are_not_visible_before_approval(self):
        draft = self.store.create_settlement(
            "Draft", 2026, "2026-01-01", "2026-12-31", "admin", object_id="unit-1",
        )
        url = f"/rentals/settlements/{draft['settlement_id']}/files/Vermieter-Abrechnungsblatt.pdf"
        self.assertEqual(403, self.request_with_store("get", url).status_code)

    def test_downloads_require_authentication_and_administrator(self):
        anonymous = app.test_client()
        self.assertEqual(302, anonymous.get(self.file_url).status_code)
        with patch("app.rentals._store", return_value=self.store):
            self.assertEqual(403, self.member_client.get(self.file_url).status_code)
            self.assertEqual(403, self.member_client.get(self.package_url).status_code)

    def test_report_generation_error_does_not_expose_filesystem_details(self):
        internal_path = str(self.base / "private" / "report-stage")
        with patch.object(self.store, "approve", side_effect=OSError(internal_path)), \
                patch("app.rentals.flash") as flash:
            response = self.request_with_store(
                "post", f"/rentals/settlements/{self.settlement_id}/approve",
            )
        self.assertEqual(302, response.status_code)
        messages = " ".join(str(call.args[0]) for call in flash.call_args_list)
        self.assertNotIn(internal_path, messages)
        self.assertIn("Freigabe fehlgeschlagen", messages)

    def test_storage_failure_returns_controlled_unavailable_response(self):
        with patch.object(self.store, "tenant_package", side_effect=OSError("private path")):
            response = self.request_with_store("get", self.package_url)
        self.assertEqual(503, response.status_code)
        self.assertNotIn(b"private path", response.data)


if __name__ == "__main__":
    unittest.main()
