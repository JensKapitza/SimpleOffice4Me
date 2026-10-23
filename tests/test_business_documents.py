import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest import mock

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from werkzeug.datastructures import MultiDict
from werkzeug.exceptions import Conflict, NotFound
from app.document_store import CONTROL_DIR, DocumentStore
from app.business_documents import (
    DIN_BOTTOM_RESERVED,
    PAGE_NUMBER_CLEAR_BOTTOM,
    PAGE_NUMBER_CLEAR_TOP,
    _ContentDocTemplate,
    _epc_qr_payload,
    _credit_note_amounts,
    _draft_invoice_number,
    _draft_watermark,
    _build_invoice_lines,
    _parse_free_text_invoice_address,
    _invoice_row_from_form,
    _invoice_task,
    save_invoice_draft,
    _pdfa3_convert,
    _validate_hybrid,
    _zugferd_status,
    _invoice_number,
    _template_directory,
    _store_generated_pdf,
    _validate_project_sources,
    address_labels,
    attach_contact_document,
    customer_account_overview,
    contact_links,
    din5008_template_guide_pdf,
    embed_invoice_xml,
    finalize_invoice,
    inspect_zugferd_pdf,
    invoice,
    invoice_state,
    record_invoice_payment,
    render_business_pdf,
    write_off_invoice,
)
from app.business_document_generation import (
    _cii_xml,
    _invoice_content_pdf,
    _invoice_store_path,
    validated_invoice_reference,
)
from app.customer_credit import CustomerCreditLedger
from app.calendar_store import CalendarStore
from app.contact_store import ContactStore
from app.file_lock import exclusive_file_lock
from app.project_store import ProjectStore
from app.settings_store import TRANSLATIONS


def _three_page_template(root: Path) -> dict:
    directory = _template_directory(root)
    path = directory / "template.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    for label in ("TITLE-TEMPLATE", "FIRST-TEMPLATE", "FOLLOW-TEMPLATE"):
        c.drawString(20, 820, label)
        c.showPage()
    c.save()
    return {"template_id": "test-template", "file": "template.pdf", "name": "Test"}


class BusinessDocumentTests(unittest.TestCase):
    def test_invoice_rejects_unknown_address_mode_before_contact_access(self):
        with tempfile.TemporaryDirectory() as temp:
            form = MultiDict([("address_mode", "unexpected")])
            with mock.patch("app.business_documents.ContactStore") as contacts:
                with self.assertRaisesRegex(ValueError, "Ungültiger Rechnungsadressmodus"):
                    _invoice_row_from_form(Path(temp), "contact-1", form, "tester")
                contacts.assert_not_called()

    def test_cii_payment_means_precedes_header_vat_tax(self):
        from xml.etree import ElementTree as ET

        xml = _cii_xml({
            "invoice_number": "RE-2026-1", "issue_date": "2026-10-22", "service_date": "2026-10-22",
            "due_date": "2026-11-05", "currency": "EUR", "seller": {
                "name": "Seller", "street": "Seller St 1", "postal": "10117", "city": "Berlin",
                "country": "DE", "iban": "DE89370400440532013000",
            }, "buyer": {"name": "Buyer", "street": "Buyer St 2", "postal": "10115", "city": "Berlin", "country": "DE"}, "lines": [], "totals": {
                "net": "10.00", "tax": "1.90", "gross": "11.90", "due": "11.90",
                "vat_groups": {"19": {"basis": "10.00", "tax": "1.90"}},
            },
        })
        root = ET.fromstring(xml)
        settlement = root.find(".//{*}ApplicableHeaderTradeSettlement")
        child_names = [element.tag.rsplit("}", 1)[-1] for element in settlement]
        self.assertLess(child_names.index("SpecifiedTradeSettlementPaymentMeans"), child_names.index("ApplicableTradeTax"))

    def test_saved_invoice_address_rejects_missing_or_stale_selection(self):
        from app.business_documents import _invoice_row_from_form

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
            ContactStore(root).add_address(
                contact["contact_id"], "Rechnungsadresse", "First Street 1", "tester",
                components={"street": "First Street 1", "postal": "10115", "city": "Berlin", "country": "DE"},
            )
            for selected in ("", "contact-99", "crm-99"):
                with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, "gespeicherte Rechnungsadresse"):
                    _invoice_row_from_form(root, contact["contact_id"], MultiDict([
                        ("address_mode", "existing"), ("address", selected),
                    ]), "tester")

    def test_invoice_address_modes_save_edit_and_preserve_pdf_snapshot(self):
        from app.business_documents import invoice

        for mode in ("existing", "manual", "free_text"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                contact = ContactStore(root).upsert({"display_name": "Customer"}, "tester")
                components = {"street": "Original Street 1", "postal": "10115", "city": "Berlin", "country": "DE"}
                if mode == "existing":
                    ContactStore(root).add_address(
                        contact["contact_id"], "Rechnungsadresse", "Original Street 1\n10115 Berlin", "tester",
                        components=components,
                    )
                form = MultiDict([
                    ("address_mode", f" {mode} "), ("address", "contact-0"),
                    ("billing_street", components["street"]), ("billing_postal", "10115"),
                    ("billing_city", "Berlin"), ("billing_country", "DE"),
                    ("billing_free_text", "Original Street 1\n10115 Berlin\nDE"),
                    ("save_billing_address", "1"), ("recipient_type", "private"),
                    ("recipient_contact", "Customer"), ("line_description", "Consulting"),
                    ("line_quantity", "1"), ("line_net_price", "10"), ("line_vat_rate", "19"),
                ])
                with mock.patch("app.business_documents.business_settings", return_value={
                    "seller_name": "Seller", "seller_street": "Seller Street 2",
                    "seller_postal": "10117", "seller_city": "Berlin", "currency": "EUR",
                }), mock.patch("app.business_documents.active_template", return_value={"template_id": "test"}):
                    row = save_invoice_draft(root, contact["contact_id"], form, "tester")
                    self.assertEqual(components, {key: row["buyer"][key] for key in components})
                    self.assertEqual(0 if mode == "free_text" else 1,
                                     len(ContactStore(root).get(contact["contact_id"], "tester")["addresses"]))
                    form["draft_updated_at"] = row["updated_at"]
                    form["line_quantity"] = "2"
                    edited = save_invoice_draft(root, contact["contact_id"], form, "tester", row["invoice_id"])
                    self.assertEqual(row["invoice_id"], edited["invoice_id"])
                    self.assertEqual("23.80", edited["totals"]["gross"])
                    self.assertEqual(row["buyer"], edited["buyer"])
                    self.assertEqual(0 if mode == "free_text" else 1,
                                     len(ContactStore(root).get(contact["contact_id"], "tester")["addresses"]))
                ContactStore(root).add_address(
                    contact["contact_id"], "Another address", "Changed Street 9", "tester",
                    components={**components, "street": "Changed Street 9"},
                )
                persisted = invoice(root, row["invoice_id"])
                self.assertEqual(row["buyer"], persisted["buyer"])
                pdf_text = "\n".join(page.extract_text() or "" for page in
                                     PdfReader(io.BytesIO(_invoice_content_pdf(persisted))).pages)
                self.assertIn("Original Street 1", pdf_text)
                self.assertNotIn("Changed Street 9", pdf_text)

    def test_invoice_draft_rejects_stale_parallel_editor_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / CONTROL_DIR / "invoices"
            directory.mkdir(parents=True)
            path = directory / "draft-1.json"
            current = {
                "invoice_id": "draft-1",
                "contact_id": "contact-1",
                "status": "draft",
                "updated_at": "2026-10-21T12:00:00+00:00",
                "marker": "saved-by-another-session",
            }
            path.write_text(json.dumps(current), encoding="utf-8")
            form = MultiDict([("draft_updated_at", "2026-10-21T11:59:00+00:00")])
            with mock.patch("app.business_documents._invoice_row_from_form", side_effect=AssertionError("stale draft must not be rebuilt")):
                with self.assertRaises(Conflict):
                    save_invoice_draft(root, "contact-1", form, "tester", "draft-1")
            self.assertEqual(current, json.loads(path.read_text(encoding="utf-8")))

    def test_contact_address_not_added_when_draft_validation_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            form = MultiDict([("address_mode", "manual"), ("save_billing_address", "1")])
            with mock.patch("app.business_documents._invoice_row_from_form", side_effect=ValueError("invalid invoice")), \
                 mock.patch.object(ContactStore, "add_address") as add_address:
                with self.assertRaisesRegex(ValueError, "invalid invoice"):
                    save_invoice_draft(root, "contact-1", form, "tester")
                add_address.assert_not_called()

    def test_contact_address_saved_only_after_invoice_draft_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            form = MultiDict([("address_mode", "manual"), ("save_billing_address", "1")])
            buyer = {"label": "Teststr. 1\\n12345 Teststadt", "street": "Teststr. 1", "postal": "12345", "city": "Teststadt", "country": "DE"}
            row = {"invoice_id": "draft-test", "contact_id": "contact-1", "buyer": buyer, "totals": {}}
            contact = {"addresses": []}
            with mock.patch("app.business_documents._invoice_row_from_form", return_value=row), \
                 mock.patch.object(ContactStore, "get", return_value=contact), \
                 mock.patch.object(ContactStore, "add_address") as add_address, \
                 mock.patch("app.business_documents.DocumentStore"):
                save_invoice_draft(root, "contact-1", form, "tester")
                self.assertTrue((root / CONTROL_DIR / "invoices" / "draft-test.json").exists())
                add_address.assert_called_once()
                self.assertEqual("12345", add_address.call_args.kwargs["components"]["postal"])

    def test_invoice_draft_survives_optional_contact_address_lookup_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            form = MultiDict([("address_mode", "manual"), ("save_billing_address", "1")])
            buyer = {"label": "Teststr. 1\n12345 Teststadt", "street": "Teststr. 1",
                     "postal": "12345", "city": "Teststadt", "country": "DE"}
            row = {"invoice_id": "draft-test", "contact_id": "contact-1", "buyer": buyer, "totals": {}}
            with mock.patch("app.business_documents._invoice_row_from_form", return_value=row), \
                 mock.patch.object(ContactStore, "get", side_effect=ValueError("contact lookup unavailable")), \
                 mock.patch("app.business_documents.DocumentStore"):
                saved = save_invoice_draft(root, "contact-1", form, "tester")
            self.assertEqual("draft-test", saved["invoice_id"])
            self.assertTrue((root / CONTROL_DIR / "invoices" / "draft-test.json").exists())

    def test_invoice_draft_survives_optional_contact_address_write_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            form = MultiDict([("address_mode", "manual"), ("save_billing_address", "1")])
            buyer = {"label": "Teststr. 1\n12345 Teststadt", "street": "Teststr. 1",
                     "postal": "12345", "city": "Teststadt", "country": "DE"}
            row = {"invoice_id": "draft-test", "contact_id": "contact-1", "buyer": buyer, "totals": {}}
            with mock.patch("app.business_documents._invoice_row_from_form", return_value=row), \
                 mock.patch.object(ContactStore, "get", return_value={"addresses": []}), \
                 mock.patch.object(ContactStore, "add_address", side_effect=OSError("contact write unavailable")), \
                 mock.patch("app.business_documents.DocumentStore"):
                saved = save_invoice_draft(root, "contact-1", form, "tester")
            self.assertEqual("draft-test", saved["invoice_id"])
            self.assertTrue((root / CONTROL_DIR / "invoices" / "draft-test.json").exists())

    def test_free_text_invoice_cannot_finalize_without_structured_address(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            invoice_id = "draft-free-text"
            directory = root / CONTROL_DIR / "invoices"
            directory.mkdir(parents=True)
            row = {"invoice_id": invoice_id, "contact_id": "contact-1", "status": "draft",
                   "buyer": {"street": "Teststr. 1", "postal": "", "city": "", "country": "DE"}}
            (directory / (invoice_id + ".json")).write_text(json.dumps(row), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "strukturierte Adressdaten"):
                finalize_invoice(root, invoice_id, "tester")

    def test_free_text_invoice_address_is_parsed_into_structured_fields(self):
        address = _parse_free_text_invoice_address("Musterstraße 1\r\n10115, Berlin\r\nde")

        self.assertEqual("Musterstraße 1", address["street"])
        self.assertEqual("10115", address["postal"])
        self.assertEqual("Berlin", address["city"])
        self.assertEqual("DE", address["country"])
        self.assertEqual("Musterstraße 1\n10115, Berlin\nde", address["label"])

    def test_free_text_invoice_address_allows_two_street_lines_without_country(self):
        address = _parse_free_text_invoice_address("Musterstraße 1\nHinterhaus\n10115 Berlin")
        self.assertEqual("Musterstraße 1 Hinterhaus", address["street"])
        self.assertEqual("10115", address["postal"])
        self.assertEqual("Berlin", address["city"])
        self.assertEqual("DE", address["country"])

    def test_free_text_invoice_address_accepts_multiline_street_and_country(self):
        address = _parse_free_text_invoice_address(
            "Musterstraße 1\nHinterhaus\n10115 Berlin\nAT"
        )
        self.assertEqual("Musterstraße 1 Hinterhaus", address["street"])
        self.assertEqual("10115", address["postal"])
        self.assertEqual("Berlin", address["city"])
        self.assertEqual("AT", address["country"])

    def test_free_text_invoice_address_rejects_missing_fields_and_invalid_country(self):
        for value in ("Musterstraße 1", "Musterstraße 1\nBerlin", "Musterstraße 1\n10115 Berlin\nGermany"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _parse_free_text_invoice_address(value)

    def test_free_text_invoice_address_is_saved_as_a_structured_draft_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Max Muster"}, "tester")
            form = MultiDict([
                ("address_mode", "free_text"),
                ("billing_free_text", "Musterstraße 1\n10115, Berlin\nDE"),
                ("recipient_type", "private"), ("recipient_contact", "Max Muster"),
                ("issue_date", "2026-10-22"), ("service_date", "2026-10-22"),
                ("payment_days", "14"), ("currency", "EUR"),
            ])
            with mock.patch("app.business_documents.business_settings", return_value={
                "seller_name": "Muster GmbH", "seller_street": "Hauptstraße 1",
                "seller_postal": "10117", "seller_city": "Berlin", "currency": "EUR",
            }), mock.patch("app.business_documents._build_invoice_lines", return_value=[]), \
                 mock.patch("app.business_documents._invoice_totals", return_value={
                     "net": "0.00", "tax": "0.00", "gross": "0.00", "vat_groups": {},
                 }), \
                 mock.patch("app.business_documents._draft_invoice_number", return_value="DRAFT-2026-0001"), \
                 mock.patch("app.business_documents.active_template", return_value={"template_id": "template-1"}), \
                 mock.patch("app.business_documents.DocumentStore"):
                row = save_invoice_draft(root, contact["contact_id"], form, "tester")

            self.assertEqual({
                "street": "Musterstraße 1", "postal": "10115", "city": "Berlin", "country": "DE",
            }, {key: row["buyer"][key] for key in ("street", "postal", "city", "country")})
            self.assertEqual("Max Muster\nMusterstraße 1\n10115, Berlin\nDE", row["buyer"]["label"])
            self.assertEqual([], ContactStore(root).get(contact["contact_id"], "tester")["addresses"])
            pdf = _invoice_content_pdf(row)
            pdf_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
            for address_part in ("Musterstraße 1", "10115", "Berlin", "DE"):
                self.assertIn(address_part, pdf_text)

            with mock.patch("app.business_documents._invoice_number", return_value="RE-2026-0001"), \
                 mock.patch("app.business_documents.active_template", return_value={"template_id": "template-1"}), \
                 mock.patch("app.business_documents._validate_project_sources"), \
                 mock.patch("app.business_documents._merge_content_with_template", return_value=b"visual"), \
                 mock.patch("app.business_documents._pdfa3_convert_detailed", return_value=(b"pdfa", {"status": "pdfa3_created"})), \
                 mock.patch("app.business_documents._cii_xml", return_value=b"<Invoice />"), \
                 mock.patch("app.business_documents.embed_invoice_xml", return_value=b"hybrid"), \
                 mock.patch("app.business_documents._validate_hybrid", return_value={"validated": True, "details": []}), \
                 mock.patch("app.business_documents._store_generated_pdf", return_value={"document_id": "stored-pdf-1"}), \
                 mock.patch("app.business_documents.DocumentStore"):
                finalized, document = finalize_invoice(root, row["invoice_id"], "tester")

            self.assertEqual("stored-pdf-1", document["document_id"])
            self.assertEqual("open", finalized["status"])
            self.assertEqual("validated", finalized["zugferd"]["status"])
            ContactStore(root).add_address(
                contact["contact_id"], "Neue Kontaktadresse", "Andere Straße 5", "tester",
                components={"street": "Andere Straße 5", "postal": "20095", "city": "Hamburg", "country": "DE"},
            )
            self.assertEqual("Musterstraße 1", finalized["buyer"]["street"])
            finalized_pdf = _invoice_content_pdf(finalized)
            finalized_text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(finalized_pdf)).pages)
            self.assertIn("Musterstraße 1", finalized_text)
            self.assertNotIn("Andere Straße 5", finalized_text)

    def test_invoice_task_link_requires_visible_task_for_same_customer(self):
        task = {"id": "task-1", "contact_id": "contact-1"}
        with mock.patch("app.todo_store.TodoStore") as todo_store:
            todo_store.return_value.items.return_value = [task]
            self.assertIs(task, _invoice_task(Path("."), "task-1", "tester", "contact-1"))
            with self.assertRaises(Conflict) as caught:
                _invoice_task(Path("."), "task-1", "tester", "contact-2")
            self.assertEqual(409, caught.exception.code)
            todo_store.return_value.items.return_value = []
            with self.assertRaises(NotFound):
                _invoice_task(Path("."), "task-1", "tester", "contact-1")

    def test_reference_invoice_requires_a_finalized_same_customer_invoice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / CONTROL_DIR / "invoices"
            directory.mkdir(parents=True)
            finalized = {"invoice_id": "invoice-1", "invoice_number": "2026-0001", "contact_id": "customer-1", "status": "open"}
            draft = {"invoice_id": "invoice-2", "invoice_number": "DRAFT-2026-0002", "contact_id": "customer-1", "status": "draft"}
            (directory / "invoice-1.json").write_text(json.dumps(finalized), encoding="utf-8")
            (directory / "invoice-2.json").write_text(json.dumps(draft), encoding="utf-8")

            self.assertEqual(("invoice-1", "2026-0001"), validated_invoice_reference(root, "customer-1", "invoice-1"))
            self.assertEqual(("", ""), validated_invoice_reference(root, "customer-1", ""))
            with self.assertRaisesRegex(ValueError, "not available"):
                validated_invoice_reference(root, "customer-2", "invoice-1")
            with self.assertRaisesRegex(ValueError, "not available"):
                validated_invoice_reference(root, "customer-1", "invoice-2")

    def test_finalization_revalidates_reference_before_consuming_invoice_number(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root / CONTROL_DIR / "invoices"
            directory.mkdir(parents=True)
            draft = {
                "invoice_id": "draft-1", "invoice_number": "DRAFT-2026-0001",
                "contact_id": "customer-1", "status": "draft",
                "reference_invoice_id": "reference-1", "history": [],
                "buyer": {"street": "Customer Street 1", "postal": "12345", "city": "Berlin", "country": "DE"},
            }
            reference = {
                "invoice_id": "reference-1", "invoice_number": "DRAFT-2026-0002",
                "contact_id": "customer-1", "status": "draft",
            }
            (directory / "draft-1.json").write_text(json.dumps(draft), encoding="utf-8")
            (directory / "reference-1.json").write_text(json.dumps(reference), encoding="utf-8")

            with mock.patch("app.business_documents.ContactStore.get", return_value={"contact_id": "customer-1"}), \
                 mock.patch("app.business_documents._invoice_number") as sequence:
                with self.assertRaisesRegex(ValueError, "not available"):
                    finalize_invoice(root, "draft-1", "tester")

            sequence.assert_not_called()
            persisted = json.loads((directory / "draft-1.json").read_text(encoding="utf-8"))
            self.assertEqual("draft", persisted["status"])

    def test_invoice_ids_cannot_escape_the_invoice_store_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "invalid invoice identifier"):
                _invoice_store_path(root, "../../outside")
            with self.assertRaisesRegex(ValueError, "invalid invoice identifier"):
                invoice(root, "../../outside")
            self.assertEqual(
                root / CONTROL_DIR / "invoices" / "invoice-1.json",
                _invoice_store_path(root, "invoice-1"),
            )

    def test_pre_finalization_reference_is_in_pdf_and_structured_invoice_note(self):
        row = {
            "invoice_number": "2026-0002",
            "issue_date": "2026-10-20",
            "service_date": "2026-10-20",
            "due_date": "2026-11-03",
            "payment_terms": "14 Tage",
            "currency": "EUR",
            "seller": {"name": "Example Ltd", "street": "Seller Street 1", "postal": "12345", "city": "Berlin", "country": "DE", "iban": ""},
            "buyer": {"name": "Customer", "label": "Customer\nBuyer Street 1", "street": "Buyer Street 1", "postal": "54321", "city": "Hamburg", "country": "DE"},
            "lines": [],
            "totals": {"net": "0.00", "tax": "0.00", "gross": "0.00", "due": "0.00", "vat_groups": {}},
            "reference_invoice_id": "invoice-1",
            "reference_invoice_number": "2026-0001",
        }

        pdf = _invoice_content_pdf(row)
        xml = _cii_xml(row)

        self.assertIn("2026-0001", PdfReader(io.BytesIO(pdf)).pages[0].extract_text())
        root = ET.fromstring(xml)
        notes = root.findall(".//{*}IncludedNote/{*}Content")
        self.assertEqual(["Bezug zu Rechnung 2026-0001"], [item.text for item in notes])

    def test_generated_pdf_indexes_only_new_file_and_batches_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = io.BytesIO()
            pdf = canvas.Canvas(source, pagesize=A4)
            pdf.drawString(20, 820, "Invoice")
            pdf.save()

            with mock.patch.object(DocumentStore, "scan", side_effect=AssertionError("full archive scan")), \
                 mock.patch.object(DocumentStore, "update_metadata", autospec=True, side_effect=DocumentStore.update_metadata) as update:
                document = _store_generated_pdf(
                    root, "contact-1", "Rechnung-1", source.getvalue(), "admin",
                    "invoice", "template-1", metadata={"invoice_id": "invoice-1"},
                )

            self.assertEqual("contact-1", document["attributes"]["contact_id"])
            self.assertEqual("invoice-1", document["attributes"]["invoice_id"])
            self.assertEqual(1, update.call_count)

    def test_plain_invoice_skips_project_and_calendar_store_reads(self):
        row = {"invoice_id": "draft-1", "contact_id": "contact-1", "lines": [{"description": "Freier Posten"}]}
        with mock.patch("app.business_documents._billed_project_sources", side_effect=AssertionError("invoice scan")), \
             mock.patch("app.business_documents._billed_appointment_sources", side_effect=AssertionError("invoice scan")), \
             mock.patch.object(ProjectStore, "billing_projection", side_effect=AssertionError("project read")), \
             mock.patch.object(CalendarStore, "events", side_effect=AssertionError("calendar read")):
            _validate_project_sources(Path("/unused"), row, "admin")

    def test_draft_number_does_not_consume_invoice_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual("DRAFT-2026-0001", _draft_invoice_number(root, date(2026, 8, 27)))
            self.assertRegex(_invoice_number(root), r"^\d{4}-0001$")

    def test_draft_pdf_is_watermarked_without_changing_page_count(self):
        source = io.BytesIO()
        pdf = canvas.Canvas(source, pagesize=A4)
        pdf.drawString(20, 820, "Invoice preview")
        pdf.showPage()
        pdf.save()
        result = _draft_watermark(source.getvalue())
        self.assertEqual(1, len(PdfReader(io.BytesIO(result)).pages))
        self.assertGreater(len(result), len(source.getvalue()))

    def test_partial_credit_note_preserves_gross_and_vat_split(self):
        row = {"totals": {"gross": "119.00", "vat_groups": {"19": {"basis": "100.00", "tax": "19.00"}}}}
        amounts = _credit_note_amounts(row, __import__("decimal").Decimal("59.50"))
        self.assertEqual("50.00", amounts["net"])
        self.assertEqual("9.50", amounts["tax"])
        self.assertEqual("59.50", amounts["gross"])

    def test_invoice_numbers_are_year_scoped_and_atomically_sequential(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = _invoice_number(root)
            second = _invoice_number(root)
            self.assertRegex(first, r"^\d{4}-0001$")
            self.assertEqual(first[:5] + "0002", second)

    def test_epc_qr_contains_invoice_amount_iban_and_reference(self):
        row = {"invoice_number": "2026-0042", "seller": {"name": "Beispiel GmbH", "iban": "DE89 3704 0044 0532 0130 00", "bic": "COBADEFFXXX"}}
        payload = _epc_qr_payload(row, __import__("decimal").Decimal("119.00"))
        self.assertTrue(payload.startswith("BCD\n002\n1\nSCT\n"))
        self.assertIn("DE89370400440532013000", payload)
        self.assertIn("EUR119.00", payload)
        self.assertIn("Rechnung 2026-0042", payload)

    def test_customer_credit_keeps_tax_classification_and_prevents_overdraw(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = CustomerCreditLedger(Path(temp))
            ledger.add("customer-1", "100", kind="topup", tax_treatment="multipurpose_voucher", actor="tester", reference="Bank")
            entry = ledger.apply("customer-1", "invoice-1", "40", actor="tester")
            account = ledger.account("customer-1")
            self.assertEqual("60.00", account["balance"])
            self.assertEqual("invoice_application", entry["kind"])
            self.assertEqual("multipurpose_voucher", account["entries"][-1]["tax_treatment"])
            with self.assertRaisesRegex(ValueError, "exceeds"):
                ledger.apply("customer-1", "invoice-2", "61", actor="tester")

    def test_customer_credit_refund_cannot_overdraw_balance(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = CustomerCreditLedger(Path(temp))
            ledger.add("customer-1", "75", kind="topup", tax_treatment="manual_review", actor="tester")
            refunded = ledger.refund("customer-1", "25", actor="tester", reference="Bank transfer")
            self.assertEqual("-25.00", refunded["signed_amount"])
            self.assertEqual("50.00", ledger.account("customer-1")["balance"])
            with self.assertRaisesRegex(ValueError, "exceeds"):
                ledger.refund("customer-1", "51", actor="tester")

    def test_customer_account_overview_separates_credit_and_open_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Kunde Konto"}, "tester")
            CustomerCreditLedger(root).add(
                contact["contact_id"], "40", kind="topup",
                tax_treatment="outside_scope", actor="tester",
            )
            directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            invoice = {
                "invoice_id": "invoice-1", "invoice_number": "2026-0001",
                "contact_id": contact["contact_id"], "issue_date": "2026-08-01",
                "due_date": "2026-09-01", "status": "open", "currency": "EUR",
                "totals": {"gross": "119.00"}, "payments": [], "document_id": "document-1",
            }
            (directory / "invoice-1.json").write_text(json.dumps(invoice), encoding="utf-8")

            overview = customer_account_overview(root, "tester")

            self.assertEqual("40.00", overview["credit_total"])
            self.assertEqual("119.00", overview["outstanding_total"])
            self.assertEqual("-79.00", overview["rows"][0]["net"])

    def test_appointment_invoice_source_is_snapshotted_and_cannot_be_billed_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = CalendarStore(root).add(
                "Beratung", "Technik", "2026-08-10T10:00", "2026-08-10T11:00",
                "customer-1", "tester", metadata={
                    "appointment_type": "Sonderberatung", "billable": "1",
                    "billing_description": "Technische Beratung", "billing_quantity": "1",
                    "billing_net_price": "100", "billing_vat_rate": "19", "billing_currency": "EUR",
                },
            )
            form = MultiDict([
                ("line_object_id", ""), ("line_project_id", ""),
                ("line_source_type", "calendar_event"), ("line_source_id", event["event_id"]),
                ("line_description", "Technische Beratung"), ("line_category", "Termin"),
                ("line_quantity", "1"), ("line_unit", "HUR"),
                ("line_net_price", "100"), ("line_vat_rate", "19"),
            ])
            lines = _build_invoice_lines(root, form)
            self.assertEqual(event["event_id"], lines[0]["source_id"])
            self.assertEqual("calendar_event", lines[0]["source_type"])
            self.assertEqual("HUR", lines[0]["unit"])
            invalid_unit = MultiDict([
                ("line_description", "Ungültige Einheit"), ("line_quantity", "1"),
                ("line_unit", "NOT-A-UNIT"), ("line_net_price", "1"), ("line_vat_rate", "19"),
            ])
            with self.assertRaisesRegex(ValueError, "invalid unit"):
                _build_invoice_lines(root, invalid_unit)
            _validate_project_sources(root, {"invoice_id": "draft-1", "contact_id": "customer-1", "lines": lines}, "tester")

            directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True, exist_ok=True)
            issued = {
                "invoice_id": "issued-1", "contact_id": "customer-1", "status": "open",
                "issue_date": "2026-08-10", "due_date": "2026-08-24", "currency": "EUR",
                "totals": {"gross": "119.00"}, "payments": [], "document_id": "document-1", "lines": lines,
            }
            (directory / "issued-1.json").write_text(json.dumps(issued), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already been invoiced"):
                _validate_project_sources(root, {"invoice_id": "draft-2", "contact_id": "customer-1", "lines": lines}, "tester")

    def test_referral_is_unique_and_cannot_reference_same_customer(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = CustomerCreditLedger(Path(temp))
            ledger.add_referral("customer-1", "customer-2", "tester")
            self.assertEqual(1, len(ledger.referrals("customer-1")["recruited"]))
            with self.assertRaisesRegex(ValueError, "already"):
                ledger.add_referral("customer-3", "customer-2", "tester")
            with self.assertRaisesRegex(ValueError, "different"):
                ledger.add_referral("customer-1", "customer-1", "tester")

    def test_invoice_state_tracks_open_partial_overdue_and_paid(self):
        row = {"due_date": "2026-08-20", "totals": {"gross": "119.00"}, "payments": []}
        self.assertEqual("open", invoice_state(row, date(2026, 8, 20))["status"])
        self.assertEqual("overdue", invoice_state(row, date(2026, 8, 21))["status"])
        row["payments"] = [{"amount": "19.00"}]
        state = invoice_state(row, date(2026, 8, 20))
        self.assertEqual("partial", state["status"])
        self.assertEqual("100.00", state["outstanding"])
        row["payments"].append({"amount": "100.00"})
        self.assertEqual("paid", invoice_state(row, date(2026, 8, 21))["status"])

    def test_invoice_state_keeps_draft_out_of_receivables(self):
        state = invoice_state({"status": "draft", "totals": {"gross": "119.00"}})
        self.assertEqual("draft", state["status"])
        self.assertEqual("0.00", state["paid"])

    def test_full_write_off_preserves_invoice_totals_and_stops_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            original = {"invoice_id": "invoice-1", "invoice_number": "2026-0001", "contact_id": "contact-1", "issue_date": "2026-08-01", "due_date": "2026-08-15", "status": "overdue", "totals": {"net": "100.00", "tax": "19.00", "gross": "119.00"}, "payments": [], "history": [], "document_id": "pdf-1"}
            path = directory / "invoice-1.json"; path.write_text(json.dumps(original), encoding="utf-8")

            updated = write_off_invoice(root, "invoice-1", {"reason": "insolvency", "amount": "119", "written_off_at": "2026-08-27", "stop_collection": "on", "note": "Aktenzeichen 1"}, "tester")

            self.assertEqual("written_off", updated["payment_state"]["status"])
            self.assertEqual("0.00", updated["payment_state"]["outstanding"])
            self.assertEqual("119.00", updated["payment_state"]["written_off"])
            self.assertEqual(original["totals"], updated["totals"])
            self.assertEqual("pdf-1", updated["document_id"])
            self.assertEqual("tester", updated["write_offs"][0]["recorded_by"])
            self.assertEqual("invoice_written_off", updated["history"][-1]["type"])

    def test_partial_write_off_can_leave_a_collectible_remainder(self):
        row = {"status": "open", "due_date": "2026-12-31", "totals": {"gross": "100.00"}, "payments": [], "write_offs": [{"amount": "40.00", "stop_collection": False}]}
        state = invoice_state(row, date(2026, 8, 27))
        self.assertEqual("partial", state["status"])
        self.assertEqual("60.00", state["outstanding"])
        self.assertEqual("40.00", state["written_off"])

    def test_written_off_invoice_rejects_later_payments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            row = {"invoice_id": "invoice-1", "invoice_number": "2026-0001", "contact_id": "contact-1", "issue_date": "2026-08-01", "due_date": "2026-08-15", "status": "open", "totals": {"gross": "119.00"}, "payments": [], "history": []}
            (directory / "invoice-1.json").write_text(json.dumps(row), encoding="utf-8")
            write_off_invoice(root, "invoice-1", {"reason": "insolvency", "amount": "119", "written_off_at": "2026-08-27", "stop_collection": "on"}, "tester")

            with self.assertRaisesRegex(ValueError, "collection was stopped"):
                record_invoice_payment(root, "invoice-1", {"amount": "1", "paid_at": "2026-08-27"}, "tester")

    def test_write_off_ui_keys_exist_in_german_and_english(self):
        keys = {
            "invoice.status.written_off", "writeoff.title", "writeoff.history",
            "writeoff.reason.customer_deceased", "writeoff.reason.insolvency",
            "writeoff.reason.unknown_address", "writeoff.reason.collection_uneconomical",
            "writeoff.reason.goodwill", "writeoff.reason.other", "writeoff.note",
            "writeoff.date", "writeoff.amount", "writeoff.stop_collection",
            "writeoff.submit", "writeoff.saved", "writeoff.error.stop_requires_full",
        }
        for language in ("de", "en"):
            with self.subTest(language=language):
                self.assertFalse(keys - TRANSLATIONS[language].keys())

    def test_write_off_validates_reason_amount_note_and_date(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            path = directory / "invoice-1.json"
            row = {"invoice_id": "invoice-1", "issue_date": "2026-08-10", "due_date": "2026-08-20", "status": "open", "totals": {"gross": "100.00"}, "payments": [], "history": []}
            for values, message in [
                ({"reason": "invalid", "amount": "10"}, "reason"),
                ({"reason": "other", "amount": "10"}, "note"),
                ({"reason": "goodwill", "amount": "101"}, "amount"),
                ({"reason": "goodwill", "amount": "10", "written_off_at": "2026-08-01"}, "precede"),
                ({"reason": "goodwill", "amount": "10", "written_off_at": "2026-08-27", "stop_collection": "on"}, "full"),
            ]:
                path.write_text(json.dumps(row), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    write_off_invoice(root, "invoice-1", values, "tester")

    def test_payment_cannot_be_recorded_for_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            (directory / "draft-1.json").write_text(json.dumps({"invoice_id": "draft-1", "status": "draft", "totals": {"gross": "119.00"}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "draft"):
                record_invoice_payment(root, "draft-1", {"amount": "119"}, "tester")

    def test_ghostscript_pdfa_conversion_can_read_system_icc_profile(self):
        source = io.BytesIO()
        pdf = canvas.Canvas(source, pagesize=A4)
        pdf.drawString(40, 800, "PDF/A test")
        pdf.save()

        converted, status = _pdfa3_convert(source.getvalue())

        if status in {"ghostscript_unavailable", "icc_profile_unavailable"}:
            self.skipTest(status)
        self.assertEqual("pdfa3_created", status)
        self.assertGreater(len(converted), 0)
        self.assertEqual(1, len(PdfReader(io.BytesIO(converted)).pages))

    def test_missing_validators_is_an_explicit_validation_failure(self):
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        target = io.BytesIO()
        writer.write(target)
        with mock.patch("app.business_documents.shutil.which", return_value=None), \
             mock.patch.dict("os.environ", {}, clear=True):
            validation = _validate_hybrid(target.getvalue(), b"<invoice />")
        self.assertFalse(validation["validated"])
        self.assertIn("verapdf_unavailable", validation["details"])
        self.assertIn("en16931_default_validator_unavailable", validation["details"])
        self.assertEqual("validation_failed", _zugferd_status("pdfa3_created", validation))

    def test_bundled_mustang_is_the_default_validator_without_env_setting(self):
        writer = PdfWriter(); writer.add_blank_page(width=595, height=842); target = io.BytesIO(); writer.write(target)
        with tempfile.TemporaryDirectory() as temp:
            jar = Path(temp) / "Mustang-CLI-2.25.0.jar"; jar.write_bytes(b"jar")
            completed = mock.Mock(returncode=0, stdout='<summary status="valid"/>', stderr="")
            with mock.patch.dict("os.environ", {"SIMPLEOFFICE_MUSTANG_JAR": str(jar)}, clear=True), \
                 mock.patch("app.business_documents.shutil.which", side_effect=lambda name: "/usr/bin/java" if name == "java" else None), \
                 mock.patch("app.business_documents.subprocess.run", return_value=completed) as run:
                validation = _validate_hybrid(target.getvalue(), b"<invoice />")
        self.assertTrue(validation["validated"])
        self.assertEqual("mustang-2.25.0", validation["validator"])
        self.assertIn("--action", run.call_args.args[0])
        self.assertNotIn("verapdf_unavailable", validation["details"])

    def test_pdfa_failure_has_its_own_terminal_status(self):
        self.assertEqual("pdfa_failed", _zugferd_status("ghostscript_pdfa_failed", {"validated": False}))
        self.assertEqual("validated", _zugferd_status("pdfa3_created", {"validated": True}))

    def test_parallel_finalization_is_rejected_before_consuming_a_number(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            path = directory / "draft-1.json"
            path.write_text(json.dumps({"invoice_id": "draft-1", "status": "draft"}), encoding="utf-8")
            with exclusive_file_lock(path.with_suffix(".lock")):
                with mock.patch("app.business_documents._invoice_number") as sequence:
                    with self.assertRaisesRegex(ValueError, "already in progress"):
                        finalize_invoice(root, "draft-1", "tester")
            sequence.assert_not_called()

    def test_interrupted_finalization_leaves_persisted_invoice_as_editable_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            path = directory / "draft-1.json"
            original = {
                "invoice_id": "draft-1", "invoice_number": "DRAFT-2026-0001",
                "status": "draft", "template_id": "tpl", "history": [],
                "buyer": {"street": "Musterstraße 1", "postal": "10115", "city": "Berlin", "country": "DE"},
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            with mock.patch("app.business_documents.business_settings", return_value={}), \
                 mock.patch("app.business_documents._invoice_number", return_value="RE-2026-0001"), \
                 mock.patch("app.business_documents.active_template", return_value={"template_id": "tpl"}), \
                 mock.patch("app.business_documents._invoice_content_pdf", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    finalize_invoice(root, "draft-1", "tester")
            self.assertEqual(original, json.loads(path.read_text(encoding="utf-8")))

    def test_payment_is_persisted_and_audited_without_changing_invoice_lines(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / CONTROL_DIR / "invoices"; directory.mkdir(parents=True)
            original_lines = [{"line_id": 1, "description": "Leistung", "net_total": "100.00"}]
            (directory / "invoice-1.json").write_text(json.dumps({"invoice_id": "invoice-1", "due_date": "2026-08-31", "totals": {"gross": "119.00"}, "lines": original_lines}), encoding="utf-8")

            updated = record_invoice_payment(root, "invoice-1", {"amount": "19", "paid_at": "2026-08-27", "reference": "Bank"}, "tester")

            self.assertEqual("100.00", updated["payment_state"]["outstanding"])
            self.assertEqual(original_lines, updated["lines"])
            self.assertEqual("payment_recorded", updated["history"][-1]["type"])
            with self.assertRaisesRegex(ValueError, "not exceed"):
                record_invoice_payment(root, "invoice-1", {"amount": "101", "paid_at": "2026-08-27"}, "tester")

    def test_address_label_prefers_billing(self):
        contact = {"fields": {"display_name": "Max Muster", "company": "Muster GmbH"}, "addresses": []}
        crm = {"addresses": [
            {"type": "shipping", "street": "Lager 1", "postal": "40000", "city": "Duisburg", "country": "DE"},
            {"type": "billing", "street": "Rechnung 2", "postal": "40200", "city": "Düsseldorf", "country": "DE"},
        ]}
        label, choices = address_labels(contact, crm)
        self.assertIn("Rechnung 2", label)
        self.assertEqual(["Muster GmbH", "Max Muster"], label.splitlines()[:2])
        self.assertEqual(2, len(choices))

    def test_legacy_address_includes_company_and_person_without_duplicates(self):
        contact = {
            "fields": {"display_name": "Max Muster", "company": "Muster GmbH"},
            "addresses": [{"label": "billing", "value": "Muster GmbH\nAltstr. 1\n47137 Duisburg"}],
        }

        label, _choices = address_labels(contact, {})

        self.assertEqual(["Muster GmbH", "Max Muster", "Altstr. 1", "47137 Duisburg"], label.splitlines())

    def test_address_uses_first_and_last_name_when_display_name_is_missing(self):
        contact = {"fields": {"first_name": "Max", "last_name": "Muster"}, "addresses": []}
        crm = {"addresses": [{"type": "billing", "street": "Altstr. 1", "postal": "47137", "city": "Duisburg"}]}

        label, _choices = address_labels(contact, crm)

        self.assertEqual("Max Muster", label.splitlines()[0])

    def test_carddav_company_display_name_does_not_hide_structured_person_name(self):
        contact = {
            "fields": {
                "display_name": "Muster GmbH",
                "company": "Muster GmbH",
                "first_name": "Max",
                "last_name": "Muster",
            },
            "addresses": [],
        }
        crm = {"addresses": [{"type": "billing", "street": "Altstr. 1", "postal": "47137", "city": "Duisburg"}]}

        label, _choices = address_labels(contact, crm)

        self.assertEqual(["Muster GmbH", "Max Muster", "Altstr. 1", "47137 Duisburg"], label.splitlines())

    def test_three_page_template_uses_first_and_follow_background_and_numbers_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = _three_page_template(root)
            body = "\n\n".join(["Absatz " + ("lang " * 80)] * 35)
            pdf = render_business_pdf(root, template, recipient="Max Muster\nMusterstr. 1\n12345 Ort", subject="Mehrseitig", markdown=body)
            reader = PdfReader(io.BytesIO(pdf))
            self.assertGreaterEqual(len(reader.pages), 2)
            first_text = reader.pages[0].extract_text()
            second_text = reader.pages[1].extract_text()
            self.assertIn("FIRST-TEMPLATE", first_text)
            self.assertIn("FOLLOW-TEMPLATE", second_text)
            self.assertIn(f"1 / {len(reader.pages)}", first_text)
            self.assertIn(f"2 / {len(reader.pages)}", second_text)

    def test_optional_cover_uses_title_template(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            template = _three_page_template(root)
            pdf = render_business_pdf(root, template, recipient="Max Muster", subject="Titel", markdown="Inhalt", cover=True)
            reader = PdfReader(io.BytesIO(pdf))
            self.assertEqual(2, len(reader.pages))
            self.assertIn("TITLE-TEMPLATE", reader.pages[0].extract_text())
            self.assertIn("1 / 2", reader.pages[0].extract_text())

    def test_contact_document_link_is_stable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = attach_contact_document(root, "contact-1", "document-1", "admin", relation="letter")
            second = attach_contact_document(root, "contact-1", "document-1", "admin", relation="letter")
            self.assertEqual(first["link_id"], second["link_id"])
            self.assertEqual(1, len(contact_links(root, "contact-1")))

    def test_zugferd_attachment_is_detected_and_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invoice.pdf"
            base = io.BytesIO()
            c = canvas.Canvas(base, pagesize=A4); c.drawString(50, 800, "Invoice"); c.save()
            xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
  <ram:ID>RE-2026-001</ram:ID><ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
  <ram:Name>Lieferant GmbH</ram:Name><ram:Name>Kunde GmbH</ram:Name>
  <ram:GrandTotalAmount>119.00</ram:GrandTotalAmount><ram:TaxTotalAmount>19.00</ram:TaxTotalAmount><ram:DuePayableAmount>119.00</ram:DuePayableAmount>
</rsm:CrossIndustryInvoice>'''
            path.write_bytes(embed_invoice_xml(base.getvalue(), xml))
            result = inspect_zugferd_pdf(path)
            self.assertTrue(result["detected"])
            self.assertEqual("RE-2026-001", result["invoice_id"])
            self.assertEqual("EUR", result["currency"])
            self.assertEqual("119.00", result["grand_total"])
            self.assertEqual("Lieferant GmbH", result["seller"])
            self.assertEqual("Kunde GmbH", result["buyer"])
            self.assertEqual("not_validated", result["validation"])

    def test_invoice_xml_embedding_rejects_invalid_xml(self):
        writer = PdfWriter(); writer.add_blank_page(width=595, height=842); target = io.BytesIO(); writer.write(target)
        with self.assertRaisesRegex(ValueError, "well formed"):
            embed_invoice_xml(target.getvalue(), b"<broken>")

    def test_din5008_template_guide_has_required_three_pages(self):
        reader = PdfReader(io.BytesIO(din5008_template_guide_pdf()))
        self.assertEqual(3, len(reader.pages))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("DYNAMISCHER INHALT", text)
        self.assertEqual(3, text.count("4 cm FREIER FUSSBEREICH"))
        self.assertEqual(3, text.count("SEITENZAHL FREIHALTEN"))

    def test_all_dynamic_content_pages_reserve_four_centimetres_at_bottom(self):
        target = io.BytesIO()
        document = _ContentDocTemplate(target)
        self.assertEqual(40 * mm, DIN_BOTTOM_RESERVED)
        self.assertEqual(DIN_BOTTOM_RESERVED, document.bottomMargin)
        self.assertLess(PAGE_NUMBER_CLEAR_BOTTOM, PAGE_NUMBER_CLEAR_TOP)
        self.assertLess(PAGE_NUMBER_CLEAR_TOP, DIN_BOTTOM_RESERVED)


if __name__ == "__main__":
    unittest.main()
