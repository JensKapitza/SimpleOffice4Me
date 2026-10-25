"""Public HTTP contract tests for book-label print jobs."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, g, session

from app.library.routes import bp as library_blueprint
from app.db import close_db
from app.object_store import ObjectStore
from app.security_controls import protect_browser_mutation


class BookLabelHttpContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            DATABASE=str(self.root / "test.sqlite"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="book-label-http-test",
        )
        with sqlite3.connect(self.app.config["DATABASE"]) as database:
            database.execute(
                """CREATE TABLE security_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    actor_id INTEGER,
                    actor_name TEXT,
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT,
                    outcome TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '{}'
                )"""
            )
        self.app.teardown_appcontext(close_db)

        @self.app.before_request
        def authenticate_test_user():
            username = session.get("username")
            g.user = {
                "id": 1, "username": username, "is_admin": False, "is_disabled": False,
            } if username else None

        self.app.add_url_rule(
            "/inventory/item/<object_id>", endpoint="inventory.item_detail", view_func=lambda object_id: "item",
        )

        self.app.before_request(protect_browser_mutation)
        self.app.register_blueprint(library_blueprint)
        self.client = self.app.test_client()
        with self.app.app_context():
            store = ObjectStore(self.root)
            self.book_a = store.create(
                {"name": "The First Book", "type": "book", "location": "Old Shelf",
                 "fields": {"isbn": "9780306406157", "barcode": "COPY-1", "nfc_id": "NFC-1",
                            "purchase_date": "2026-10-01", "serial_number": "SERIAL-1",
                            "library_location_id": "old-shelf", "library_location_code": "SHELF-1"}},
                "reader",
            )
            self.book_b = store.create(
                {"name": "The Second Book", "type": "book", "fields": {"barcode": "SO-BOOK-2"}},
                "reader",
            )
            self.device = store.create(
                {"name": "A device", "type": "equipment", "identifier": "DEV-1"},
                "reader",
            )
        with self.client.session_transaction() as state:
            state["username"] = "reader"
            state["_csrf_token"] = "book-label-csrf-token"

    def tearDown(self):
        self.temp.cleanup()

    def _post(self, object_ids: list[str], *, csrf: bool = True):
        data = {"object_id": object_ids, "barcode_style": "with_text"}
        if csrf:
            data["_csrf_token"] = "book-label-csrf-token"
        return self.client.post("/library/labels/books/print", data=data)

    def test_selected_book_prints_to_configured_device_and_reports_success(self):
        with patch("app.library.routes._label_printer_configuration", return_value=(None, {"backend": "brother", "label": "Brother QL"}, {"label": "62"})), \
             patch("app.library.routes.render_barcode", return_value=object()), \
             patch("app.library.routes.print_image", return_value=128) as print_label:
            response = self._post([self.book_a["object_id"]])

        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True, "printed": 1, "total": 1}, response.json)
        print_label.assert_called_once()

    def test_printer_selection_failure_does_not_expose_exception_details(self):
        secret = "internal-printer-token-and-device-path"
        with patch(
            "app.library.routes._label_printer_configuration",
            side_effect=ValueError(secret),
        ):
            response = self._post([self.book_a["object_id"]])
        self.assertEqual(503, response.status_code)
        self.assertFalse(response.json["ok"])
        self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertIn("Druckerrichtlinie", response.json["error"])

    def test_qr_label_print_uses_the_selected_book_reference(self):
        with patch("app.library.routes._label_printer_configuration", return_value=(None, {"backend": "brother", "label": "Brother QL"}, {"label": "62"})), \
             patch("app.library.routes.print_image", return_value=128):
            response = self.client.post(
                "/library/labels/books/print",
                data={"_csrf_token": "book-label-csrf-token", "barcode_style": "qr_only", "object_id": self.book_a["object_id"]},
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True, "printed": 1, "total": 1}, response.json)

    def test_selected_system_label_printer_uses_no_store_printshare_path(self):
        from unittest.mock import Mock

        share = Mock()
        share.submit.return_value = {"payload_size": 3}
        selected = {
            "backend": "cups", "printer_id": "a" * 24, "label": "CUPS Etikett",
        }
        with patch(
            "app.library.routes._label_printer_configuration",
            return_value=(share, selected, {"label": "62"}),
        ), patch("app.library.routes.render_barcode", return_value=object()), patch(
            "app.library.routes.preview_png", return_value=b"png",
        ):
            response = self._post([self.book_a["object_id"]])

        self.assertEqual(200, response.status_code)
        share.submit.assert_called_once()
        self.assertEqual(selected["printer_id"], share.submit.call_args.args[0])
        self.assertEqual(b"png", share.submit.call_args.args[1])
        self.assertEqual("no_store", share.submit.call_args.kwargs["retention_ceiling"])

    def test_qr_reference_can_be_resolved_by_the_existing_find_api(self):
        response = self.client.get(f"/library/find?q={self.book_a['object_id']}")

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json["found"])
        self.assertEqual(self.book_a["object_id"], response.json["item"]["object_id"])

    def test_manual_barcode_prints_the_requested_bounded_number_of_copies(self):
        library = Mock()
        library.printer_settings.return_value = {"label": "62", "uri": "test://printer"}
        with patch("app.library.printer_routes._label_printer_configuration", return_value=(None, {"backend": "brother", "label": "Brother QL"}, {"label": "62"})), \
             patch("app.library.printer_routes._store", return_value=library), \
             patch("app.library.printer_routes.render_barcode", return_value=object()) as render, \
             patch("app.library.printer_routes._send_label_image", return_value=128) as print_label:
            response = self.client.post(
                "/library/printer/print/barcode",
                data={"_csrf_token": "book-label-csrf-token", "value": "4006381333931", "copies": "3"},
            )

        self.assertEqual(302, response.status_code)
        render.assert_called_once()
        self.assertTrue(render.call_args.kwargs["scan_optimized"])
        self.assertEqual(3, print_label.call_count)
        self.assertEqual(3, library.record_print.call_count)

    def test_manual_barcode_copy_count_outside_limit_never_reaches_printer(self):
        library = Mock()
        with patch("app.library.printer_routes._store", return_value=library), \
             patch("app.library.printer_routes.render_barcode") as render, \
             patch("app.library.printer_routes._send_label_image") as print_label:
            response = self.client.post(
                "/library/printer/print/barcode",
                data={"_csrf_token": "book-label-csrf-token", "value": "4006381333931", "copies": "101"},
            )

        self.assertEqual(302, response.status_code)
        render.assert_not_called()
        print_label.assert_not_called()
        self.assertEqual(1, library.record_print.call_count)

    def test_partial_barcode_copy_failure_reports_how_many_were_sent(self):
        library = Mock()
        library.printer_settings.return_value = {"label": "62", "uri": "test://printer"}
        with patch("app.library.printer_routes._label_printer_configuration", return_value=(None, {"backend": "brother", "label": "Brother QL"}, {"label": "62"})), \
             patch("app.library.printer_routes._store", return_value=library), \
             patch("app.library.printer_routes.render_barcode", return_value=object()), \
             patch("app.library.printer_routes._send_label_image", side_effect=[128, OSError("printer unavailable")]) as print_label:
            response = self.client.post(
                "/library/printer/print/barcode",
                data={"_csrf_token": "book-label-csrf-token", "value": "4006381333931", "copies": "3"},
            )

        self.assertEqual(302, response.status_code)
        self.assertEqual(2, print_label.call_count)
        self.assertEqual(2, library.record_print.call_count)
        with self.client.session_transaction() as state:
            self.assertIn("1 von 3 Etiketten wurden bereits gesendet", state["_flashes"][-1][1])

    def test_non_book_selection_is_rejected_without_printer_output(self):
        response = self._post([self.device["object_id"]])

        self.assertEqual(400, response.status_code)
        self.assertEqual("Die Auswahl enthält einen Nicht-Buch-Datensatz.", response.json["error"])

    def test_browser_post_without_csrf_is_rejected(self):
        with patch("app.library.routes.print_image") as print_label:
            response = self._post([self.book_a["object_id"]], csrf=False)

        self.assertEqual(403, response.status_code)
        print_label.assert_not_called()

    def test_partial_printer_failure_reports_irreversible_progress(self):
        with patch("app.library.routes._label_printer_configuration", return_value=(None, {"backend": "brother", "label": "Brother QL"}, {"label": "62"})), \
             patch("app.library.routes.render_barcode", return_value=object()), \
             patch("app.library.routes.print_image", side_effect=[128, OSError("offline")]) as print_label:
            response = self._post([self.book_a["object_id"], self.book_b["object_id"]])

        self.assertEqual(502, response.status_code)
        self.assertEqual(
            {"ok": False, "printed": 1, "total": 2,
             "error": "Druck fehlgeschlagen; bereits gedruckte Etiketten bleiben ausgegeben."},
            response.json,
        )
        self.assertEqual(2, print_label.call_count)

    def test_additional_copy_shares_work_and_edition_but_has_own_copy_identity(self):
        response = self.client.post(
            f"/library/books/{self.book_a['object_id']}/copies",
            data={"_csrf_token": "book-label-csrf-token"}, headers={"Accept": "application/json"},
        )

        self.assertEqual(201, response.status_code)
        store = ObjectStore(self.root)
        source = store.object(self.book_a["object_id"])
        created = store.object(response.json["object_id"])
        self.assertNotEqual(source["object_id"], created["object_id"])
        self.assertEqual(source["fields"]["isbn"], created["fields"]["isbn"])
        self.assertEqual(source["fields"]["library_work_id"], created["fields"]["library_work_id"])
        self.assertEqual(source["fields"]["library_edition_id"], created["fields"]["library_edition_id"])
        self.assertNotEqual(source["fields"]["library_copy_id"], created["fields"]["library_copy_id"])
        self.assertEqual("", created["location"])

    def test_new_edition_shares_work_but_is_not_grouped_as_same_edition(self):
        response = self.client.post(
            f"/library/books/{self.book_a['object_id']}/editions",
            data={"_csrf_token": "book-label-csrf-token"}, headers={"Accept": "application/json"},
        )

        self.assertEqual(201, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        source_copies = self.client.get(f"/library/books/{self.book_a['object_id']}/copies")
        new_edition_copies = self.client.get(f"/library/books/{response.json['object_id']}/copies")
        self.assertEqual(1, source_copies.json["count"])
        self.assertEqual(1, new_edition_copies.json["count"])
        self.assertEqual(source_copies.json["work_id"], new_edition_copies.json["work_id"])
        self.assertNotEqual(source_copies.json["edition_id"], new_edition_copies.json["edition_id"])
        self.assertEqual(response.json["work_id"], new_edition_copies.json["work_id"])
        self.assertEqual(response.json["edition_id"], new_edition_copies.json["edition_id"])
        self.assertNotEqual(self.book_a["object_id"], response.json["object_id"])
        self.assertNotEqual(response.json["copy_id"], source_copies.json["copies"][0]["copy_id"])
        created = ObjectStore(self.root).object(response.json["object_id"])
        self.assertEqual("9780306406157", created["fields"]["isbn"])
        self.assertEqual("", created["location"])
        for key in ("barcode", "nfc_id", "purchase_date", "serial_number",
                    "library_location_id", "library_location_code"):
            self.assertNotIn(key, created["fields"])

    def test_new_edition_rejects_non_book_without_creating_an_inventory_record(self):
        before = len(ObjectStore(self.root).objects())

        response = self.client.post(
            f"/library/books/{self.device['object_id']}/editions",
            data={"_csrf_token": "book-label-csrf-token"}, headers={"Accept": "application/json"},
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            "Neue Ausgabe konnte nicht angelegt werden. Bitte Ausgangsbuch prüfen.",
            response.json["error"],
        )
        self.assertEqual(before, len(ObjectStore(self.root).objects()))

    def test_new_edition_requires_csrf(self):
        response = self.client.post(
            f"/library/books/{self.book_a['object_id']}/editions", headers={"Accept": "application/json"},
        )

        self.assertEqual(403, response.status_code)

    def test_copy_listing_groups_explicit_edition_and_not_equal_isbn(self):
        created_response = self.client.post(
            f"/library/books/{self.book_a['object_id']}/copies",
            data={"_csrf_token": "book-label-csrf-token"}, headers={"Accept": "application/json"},
        )
        store = ObjectStore(self.root)
        unrelated = store.create(
            {"name": "Unrelated record", "type": "book", "fields": {"isbn": "9780306406157"}},
            "reader",
        )

        response = self.client.get(f"/library/books/{self.book_a['object_id']}/copies")

        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual(2, response.json["count"])
        self.assertEqual(
            {self.book_a["object_id"], created_response.json["object_id"]},
            {row["object_id"] for row in response.json["copies"]},
        )
        self.assertNotIn(unrelated["object_id"], {row["object_id"] for row in response.json["copies"]})
        self.assertTrue(response.json["work_id"])
        self.assertTrue(response.json["edition_id"])
        self.assertEqual(2, len({row["copy_id"] for row in response.json["copies"]}))

    def test_copy_listing_keeps_legacy_book_read_only(self):
        response = self.client.get(f"/library/books/{self.book_b['object_id']}/copies")

        self.assertEqual(200, response.status_code)
        self.assertEqual(1, response.json["count"])
        self.assertEqual("", response.json["edition_id"])
        self.assertEqual(
            {"barcode": "SO-BOOK-2"},
            ObjectStore(self.root).object(self.book_b["object_id"])["fields"],
        )

    def test_copy_listing_rejects_non_book(self):
        response = self.client.get(f"/library/books/{self.device['object_id']}/copies")

        self.assertEqual(404, response.status_code)
        self.assertEqual("Buch oder Ausgabe wurde nicht gefunden.", response.json["error"])

    def test_copy_does_not_reuse_physical_barcode_or_shelf(self):
        store = ObjectStore(self.root)
        source = store.create({
            "name": "Shelf copy", "type": "book", "identifier": "9780306406157",
            "location": "Room / Shelf", "fields": {
                "isbn": "9780306406157", "barcode": "COPY-1",
                "library_location_id": "shelf-id", "library_location_code": "SHELF-1",
            },
        }, "reader")

        response = self.client.post(
            f"/library/books/{source['object_id']}/copies",
            data={"_csrf_token": "book-label-csrf-token"}, headers={"Accept": "application/json"},
        )

        self.assertEqual(201, response.status_code)
        created = store.object(response.json["object_id"])
        self.assertEqual("", created["location"])
        for key in ("barcode", "library_location_id", "library_location_code"):
            self.assertNotIn(key, created["fields"])

    def test_additional_copy_rejects_non_book(self):
        response = self.client.post(
            f"/library/books/{self.device['object_id']}/copies",
            data={"_csrf_token": "book-label-csrf-token"}, headers={"Accept": "application/json"},
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            "Exemplar konnte nicht angelegt werden. Bitte Ausgangsbuch prüfen.",
            response.json["error"],
        )

    def test_additional_copy_requires_csrf(self):
        response = self.client.post(
            f"/library/books/{self.book_a['object_id']}/copies", headers={"Accept": "application/json"},
        )

        self.assertEqual(403, response.status_code)


if __name__ == "__main__":
    unittest.main()
