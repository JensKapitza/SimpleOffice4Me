"""Blackbox: CSV bulk import with preview, duplicates and error reports (Issue #1111)."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database
from app.object_store import ObjectStore

CSV_OK = (
    "name;type;identifier;location;serial_number\n"
    "Hammer;Werkzeug;HAM-1;Werkstatt;\n"
    "PC Büro;PC;PC-1;Büro;SN-100\n"
)
CSV_MIXED = (
    "name,identifier\n"
    "Gut,GOOD-1\n"
    ",NONAME-1\n"
    "Doppelt,GOOD-1\n"
)


class CsvImportHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "importer", "password": "importer-password-123"})
        self.client.post("/auth/login", data={"username": "importer", "password": "importer-password-123"})
        self.root = base / "documents"

    def tearDown(self):
        app.config.update(self.saved)

    def preview(self, payload, accept_json=True):
        headers = {"Accept": "application/json"} if accept_json else {}
        return self.client.post(
            "/inventory/import/preview", headers=headers,
            data={"file": (io.BytesIO(payload.encode("utf-8")), "import.csv")},
            content_type="multipart/form-data",
        )

    def test_preview_maps_columns_and_counts_rows(self):
        response = self.preview(CSV_OK)
        self.assertEqual(200, response.status_code)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(2, body["total"])
        self.assertEqual(2, body["valid"])
        self.assertEqual([], body["problems"])
        self.assertTrue(body["token"])
        self.assertEqual("Hammer", body["preview"][0]["name"])

    def test_preview_flags_errors_and_duplicates(self):
        ObjectStore(self.root).create(
            {"name": "Alt", "type": "object", "identifier": "OLD-9"}, "importer")
        response = self.preview(
            "name;identifier\nGut;GOOD-1\n;NONAME\nDoppelt;GOOD-1\nAltbestand;OLD-9\n")
        body = response.get_json()
        self.assertEqual(1, body["valid"])
        kinds = sorted(row["problem"] for row in body["problems"])
        self.assertEqual(3, len(kinds))
        self.assertTrue(any("Name fehlt" in kind for kind in kinds))
        self.assertTrue(any("doppelt in Datei" in kind for kind in kinds))
        self.assertTrue(any("bereits vorhanden" in kind for kind in kinds))

    def test_commit_creates_and_skips_duplicates(self):
        staged = self.preview(CSV_MIXED).get_json()
        self.assertEqual(1, staged["valid"])
        commit = self.client.post("/inventory/import/commit",
                                  json={"token": staged["token"]})
        self.assertEqual(200, commit.status_code)
        body = commit.get_json()
        self.assertEqual(1, body["counts"]["created"])
        items = ObjectStore(self.root).objects("GOOD-1")
        self.assertEqual(1, len(items))
        self.assertEqual("Gut", items[0]["name"])
        reuse = self.client.post("/inventory/import/commit",
                                 json={"token": staged["token"]})
        self.assertEqual(400, reuse.status_code)

    def test_import_validates_files_and_tokens(self):
        empty = self.preview("")
        self.assertEqual(400, empty.status_code)
        no_header = self.preview("nur-text-ohne-trennzeichen\n")
        self.assertEqual(400, no_header.status_code)
        unknown_token = self.client.post("/inventory/import/commit",
                                         json={"token": "0" * 32})
        self.assertEqual(400, unknown_token.status_code)

    def test_preview_maps_quantity_and_unit_aliases(self):
        staged = self.preview(
            "name;menge;einheit\nSchrauben;50;Stück\nEinzelteil;;\n").get_json()
        self.assertTrue(staged["ok"])
        self.assertEqual(2, staged["valid"])
        self.assertEqual([], staged["problems"])
        first = staged["preview"][0]
        self.assertEqual("50", first["quantity"])
        self.assertEqual("Stück", first["unit"])
        self.assertEqual("", staged["preview"][1]["quantity"])
        commit = self.client.post("/inventory/import/commit",
                                  json={"token": staged["token"]})
        body = commit.get_json()
        self.assertEqual(2, body["counts"]["created"])
        screws = ObjectStore(self.root).objects("Schrauben")
        self.assertEqual(1, len(screws))
        self.assertEqual("50", screws[0]["quantity"])
        self.assertEqual("Stück", screws[0]["unit"])

    def test_preview_rejects_bad_quantity_and_long_unit(self):
        staged = self.preview(
            "name;menge;einheit\nText;viel;Stück\nNegativ;-3;Stück\n"
            "Riesig;2;xaaaaaaaaaaaaaaaaaaaaaaaa\nOk;1,5;kg\n").get_json()
        self.assertEqual(1, staged["valid"])
        kinds = sorted(row["problem"] for row in staged["problems"])
        self.assertEqual(3, len(kinds))
        self.assertEqual(2, sum("Menge" in kind for kind in kinds))
        self.assertTrue(any("Einheit" in kind for kind in kinds))
        self.assertEqual("1.5", staged["preview"][0]["quantity"])

    def test_import_requires_login_and_form_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.post(
            "/inventory/import/preview", follow_redirects=False,
            data={"file": (io.BytesIO(b"name\nx\n"), "import.csv")},
            content_type="multipart/form-data")
        self.assertIn(denied.status_code, (302, 303))
        page = self.client.get("/inventory")
        html = page.get_data(as_text=True)
        self.assertIn("data-csv-import", html)
        self.assertIn("/inventory/import/preview", html)


if __name__ == "__main__":
    unittest.main()
