from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pypdf import PdfReader
from werkzeug.datastructures import MultiDict

from app.business_customer_documents import (
    invoice_seller_snapshot,
    letter_customer_context,
    preferred_bank_account_id,
    preferred_template_id,
)
from app.business_document_generation import (
    CONTROL_DIR,
    SETTINGS_FILE,
    business_settings,
    save_business_settings,
)
from app.business_document_preferences import (
    company_bank_accounts,
    normalize_company_bank_accounts,
    validate_document_preferences,
)
from app.business_documents import (
    _invoice_row_from_form,
    _invoice_store_path,
    draft_invoice_pdf,
    finalize_invoice,
)
from app.business_template_editor import create_visual_template, save_visual_template, visual_template
from app.business_template_render import render_visual_letter_pdf
from app.contact_extensions import ContactCRMStore
from app.contact_store import ContactStore


ACCOUNT_1 = "1" * 32
ACCOUNT_2 = "2" * 32


def _business_form(default_id: str = ACCOUNT_2) -> MultiDict:
    return MultiDict([
        ("seller_name", "Kapitza GmbH"),
        ("seller_street", "Musterstraße 1"),
        ("seller_postal", "47137"),
        ("seller_city", "Duisburg"),
        ("seller_country", "DE"),
        ("seller_email", "firma@example.test"),
        ("seller_vat_id", "DE123456789"),
        ("seller_tax_number", "12/345/67890"),
        ("payment_terms", "Zahlbar ohne Abzug"),
        ("default_payment_days", "14"),
        ("currency", "EUR"),
        ("zugferd_profile", "EN16931"),
        ("bank_accounts_present", "1"),
        ("default_bank_account_id", default_id),
        ("bank_account_id", ACCOUNT_1),
        ("bank_account_label", "Projektkonto"),
        ("bank_account_holder", "Kapitza GmbH"),
        ("bank_account_iban", "DE89370400440532013000"),
        ("bank_account_blz", "37040044"),
        ("bank_account_bic", "COBADEFFXXX"),
        ("bank_account_bank", "Bank Eins"),
        ("bank_account_currency", "EUR"),
        ("bank_account_id", ACCOUNT_2),
        ("bank_account_label", "Standardkonto"),
        ("bank_account_holder", "Kapitza GmbH"),
        ("bank_account_iban", "DE12500105170648489890"),
        ("bank_account_blz", "50010517"),
        ("bank_account_bic", "INGDDEFFXXX"),
        ("bank_account_bank", "Bank Zwei"),
        ("bank_account_currency", "EUR"),
    ])


class BusinessDocumentPreferenceTests(unittest.TestCase):
    def test_company_iban_validation_checks_iso_13616_mod97(self):
        valid = normalize_company_bank_accounts([{"label": "Konto", "iban": "DE89 3704 0044 0532 0130 00"}])
        self.assertEqual("DE89370400440532013000", valid[0]["iban"])
        with self.assertRaisesRegex(ValueError, "IBAN ist ungültig"):
            normalize_company_bank_accounts([{"label": "Konto", "iban": "DE88 3704 0044 0532 0130 00"}])

    def test_legacy_bad_checksum_account_remains_editable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings_path = root / CONTROL_DIR / SETTINGS_FILE
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(json.dumps({
                "seller_name": "Kapitza GmbH",
                "seller_bank_accounts": [{
                    "account_id": ACCOUNT_1,
                    "label": "Altes Konto",
                    "iban": "DE88370400440532013000",
                    "default": True,
                }],
            }), encoding="utf-8")

            loaded = business_settings(root)
            self.assertEqual("DE88370400440532013000", loaded["seller_iban"])
            self.assertEqual("Altes Konto", loaded["seller_bank_accounts"][0]["label"])
            with self.assertRaisesRegex(ValueError, "IBAN ist ungültig"):
                invoice_seller_snapshot(loaded, ACCOUNT_1)

            draft_path = _invoice_store_path(root, "legacy-draft")
            draft_path.write_text(json.dumps({
                "invoice_id": "legacy-draft",
                "status": "draft",
                "seller": {
                    "bank_account_label": "Altes Konto",
                    "iban": "DE88370400440532013000",
                },
            }), encoding="utf-8")
            legacy_draft = json.loads(draft_path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "IBAN ist ungültig"):
                draft_invoice_pdf(root, legacy_draft)
            with self.assertRaisesRegex(ValueError, "IBAN ist ungültig"):
                finalize_invoice(root, "legacy-draft", "tester")

            invalid_form = _business_form(default_id=ACCOUNT_1)
            invalid_form.setlist(
                "bank_account_iban",
                ["DE88370400440532013000", "DE12500105170648489890"],
            )
            with mock.patch("app.business_document_generation.DocumentStore"):
                with self.assertRaisesRegex(ValueError, "IBAN ist ungültig"):
                    save_business_settings(root, invalid_form, "tester")
                self.assertEqual(
                    "DE88370400440532013000",
                    json.loads(settings_path.read_text(encoding="utf-8"))["seller_bank_accounts"][0]["iban"],
                )
                corrected = save_business_settings(
                    root,
                    _business_form(default_id=ACCOUNT_1),
                    "tester",
                )
            self.assertEqual("DE89370400440532013000", corrected["seller_iban"])
            self.assertEqual(
                "DE89370400440532013000",
                invoice_seller_snapshot(corrected, ACCOUNT_1)["iban"],
            )

    def test_multiple_company_accounts_are_persisted_and_default_mirrors_legacy_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch("app.business_document_generation.DocumentStore"):
                saved = save_business_settings(root, _business_form(), "tester")

            accounts = saved["seller_bank_accounts"]
            self.assertEqual(2, len(accounts))
            self.assertEqual(ACCOUNT_2, next(row["account_id"] for row in accounts if row["default"]))
            self.assertEqual("DE12500105170648489890", saved["seller_iban"])
            self.assertEqual("50010517", saved["seller_blz"])
            self.assertEqual("INGDDEFFXXX", saved["seller_bic"])
            self.assertEqual("Bank Zwei", saved["seller_bank"])

            reloaded = business_settings(root)
            self.assertEqual([ACCOUNT_1, ACCOUNT_2], [row["account_id"] for row in reloaded["seller_bank_accounts"]])

    def test_legacy_single_bank_fields_can_still_update_existing_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch("app.business_document_generation.DocumentStore"):
                save_business_settings(root, _business_form(), "tester")
                updated = save_business_settings(
                    root,
                    {
                        "seller_name": "Kapitza GmbH",
                        "seller_iban": "DE44500105175407324931",
                        "seller_blz": "50010517",
                        "seller_bic": "INGDDEFFXXX",
                        "seller_bank": "Legacy Update Bank",
                    },
                    "tester",
                )
            self.assertEqual("DE44500105175407324931", updated["seller_iban"])
            self.assertEqual("Legacy Update Bank", updated["seller_bank"])
            self.assertEqual(1, len(updated["seller_bank_accounts"]))
            self.assertEqual("DE44500105175407324931", updated["seller_bank_accounts"][0]["iban"])

    def test_explicit_empty_account_editor_removes_all_company_accounts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch("app.business_document_generation.DocumentStore"):
                save_business_settings(root, _business_form(), "tester")
                cleared = save_business_settings(
                    root,
                    MultiDict([
                        ("seller_name", "Kapitza GmbH"),
                        ("seller_street", "Musterstraße 1"),
                        ("seller_postal", "47137"),
                        ("seller_city", "Duisburg"),
                        ("seller_country", "DE"),
                        ("currency", "EUR"),
                        ("default_payment_days", "14"),
                        ("bank_accounts_present", "1"),
                    ]),
                    "tester",
                )
            self.assertEqual([], cleared["seller_bank_accounts"])
            self.assertEqual("", cleared["seller_iban"])
            self.assertEqual("", cleared["seller_bic"])

    def test_customer_bank_preference_and_manual_override_choose_stable_account_ids(self):
        settings = {
            "seller_name": "Kapitza GmbH",
            "seller_bank_accounts": company_bank_accounts({
                "seller_bank_accounts": [
                    {
                        "account_id": ACCOUNT_1, "label": "Eins", "holder": "Kapitza GmbH",
                        "iban": "DE89370400440532013000", "blz": "37040044",
                        "bic": "COBADEFFXXX", "bank": "Bank Eins", "currency": "EUR",
                        "default": False,
                    },
                    {
                        "account_id": ACCOUNT_2, "label": "Zwei", "holder": "Kapitza GmbH",
                        "iban": "DE12500105170648489890", "blz": "50010517",
                        "bic": "INGDDEFFXXX", "bank": "Bank Zwei", "currency": "EUR",
                        "default": True,
                    },
                ]
            }),
        }
        crm = {"document_preferences": {"invoice_bank_account_id": ACCOUNT_1}}

        selected = preferred_bank_account_id(settings, crm)
        self.assertEqual(ACCOUNT_1, selected)
        self.assertEqual("DE89370400440532013000", invoice_seller_snapshot(settings, selected)["iban"])

        manual = preferred_bank_account_id(settings, crm, explicit_id=ACCOUNT_2)
        self.assertEqual(ACCOUNT_2, manual)

        stale = preferred_bank_account_id(
            settings,
            {"document_preferences": {"invoice_bank_account_id": "f" * 32}},
        )
        self.assertEqual(ACCOUNT_2, stale)

    def test_customer_template_preferences_are_type_specific_and_fall_back(self):
        crm = {
            "document_preferences": {
                "invoice_template_id": "invoice-customer",
                "letter_template_id": "letter-customer",
            }
        }
        invoice_choices = [
            {"template_id": "invoice-global", "active": True},
            {"template_id": "invoice-customer", "active": False},
        ]
        letter_choices = [
            {"template_id": "letter-global", "active": True},
            {"template_id": "letter-customer", "active": False},
        ]
        self.assertEqual(
            "invoice-customer",
            preferred_template_id(crm, "invoice", invoice_choices),
        )
        self.assertEqual(
            "letter-customer",
            preferred_template_id(crm, "letter", letter_choices),
        )
        self.assertEqual(
            "letter-global",
            preferred_template_id(
                {"document_preferences": {"letter_template_id": "deleted-template"}},
                "letter",
                letter_choices,
            ),
        )
        with self.assertRaisesRegex(ValueError, "unknown business document template"):
            preferred_template_id(crm, "letter", letter_choices, explicit_id="invoice-customer")

    def test_crm_store_preserves_document_preferences_on_unrelated_save(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = ContactCRMStore(root)
            first = store.save("customer-1", {
                "customer_number": "K-100",
                "document_preferences": {
                    "invoice_template_id": "invoice-1",
                    "letter_template_id": "letter-1",
                    "invoice_bank_account_id": ACCOUNT_1,
                },
            }, "tester")
            second = store.save("customer-1", {
                "customer_number": "K-101",
            }, "tester")
            self.assertEqual(first["document_preferences"], second["document_preferences"])
            self.assertEqual("K-101", second["customer_number"])

    def test_preference_validation_rejects_cross_type_or_missing_references(self):
        valid = validate_document_preferences(
            {
                "invoice_template_id": "invoice-1",
                "letter_template_id": "letter-1",
                "invoice_bank_account_id": ACCOUNT_1,
            },
            invoice_template_ids={"invoice-1"},
            letter_template_ids={"letter-1"},
            bank_account_ids={ACCOUNT_1},
        )
        self.assertEqual("letter-1", valid["letter_template_id"])
        with self.assertRaisesRegex(ValueError, "letter template"):
            validate_document_preferences(
                {"letter_template_id": "invoice-1"},
                invoice_template_ids={"invoice-1"},
                letter_template_ids={"letter-1"},
                bank_account_ids={ACCOUNT_1},
            )

    def test_invoice_row_snapshots_customer_preferred_company_account(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contact = ContactStore(root).upsert({"display_name": "Kunde GmbH"}, "tester")
            with mock.patch("app.business_document_generation.DocumentStore"):
                save_business_settings(root, _business_form(), "tester")
            ContactCRMStore(root).save(contact["contact_id"], {
                "customer_number": "K-100",
                "document_preferences": {"invoice_bank_account_id": ACCOUNT_1},
            }, "tester")
            form = MultiDict([
                ("address_mode", "manual"),
                ("billing_street", "Kundenstraße 5"),
                ("billing_postal", "47051"),
                ("billing_city", "Duisburg"),
                ("billing_country", "DE"),
                ("recipient_type", "private"),
                ("recipient_contact", "Kunde GmbH"),
                ("issue_date", "2026-11-11"),
                ("service_date", "2026-11-11"),
                ("payment_days", "14"),
                ("currency", "EUR"),
            ])
            with mock.patch("app.business_documents._build_invoice_lines", return_value=[]), \
                 mock.patch("app.business_documents._invoice_totals", return_value={"net": "0.00", "tax": "0.00", "gross": "0.00", "vat_groups": {}}), \
                 mock.patch("app.business_documents._draft_invoice_number", return_value="DRAFT-1"), \
                 mock.patch("app.business_template_dispatch.active_template", return_value={"template_id": "legacy-template"}):
                row = _invoice_row_from_form(root, contact["contact_id"], form, "tester")

            self.assertEqual(ACCOUNT_1, row["seller"]["bank_account_id"])
            self.assertEqual("Projektkonto", row["seller"]["bank_account_label"])
            self.assertEqual("DE89370400440532013000", row["seller"]["iban"])

            with mock.patch("app.business_document_generation.DocumentStore"):
                save_business_settings(root, _business_form(default_id=ACCOUNT_1), "tester")
            self.assertEqual("DE89370400440532013000", row["seller"]["iban"])
            self.assertEqual(ACCOUNT_1, row["seller"]["bank_account_id"])

    def test_visual_letter_resolves_customer_specific_crm_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch("app.business_template_editor.DocumentStore"):
                template = create_visual_template(root, {"name": "Kundenbrief", "document_type": "letter"}, "tester")
                metadata, layout = visual_template(root, template["template_id"])
                layout["elements"].append({
                    "id": "customer-meta",
                    "type": "token",
                    "region": "content",
                    "x_mm": 18,
                    "y_mm": 225,
                    "width_mm": 174,
                    "height_mm": 15,
                    "text": "{{kunde.kundennummer}} · {{kunde.email}} · {{kunde.ust_idnr}}",
                    "asset": "",
                    "style": {
                        "font_size": 9, "font_family": "Helvetica", "bold": False,
                        "italic": False, "underline": False, "color": "#111827", "align": "left",
                    },
                    "z_index": 10,
                    "locked": False,
                    "visible": True,
                })
                save_visual_template(root, template["template_id"], metadata, layout, "tester")

            customer = {
                "customer_number": "K-7788",
                "email": "kunde@example.test",
                "vat_id": "DE987654321",
            }
            pdf = render_visual_letter_pdf(
                root,
                template["template_id"],
                business={
                    "seller_name": "Kapitza GmbH",
                    "seller_street": "Musterstraße 1",
                    "seller_postal": "47137",
                    "seller_city": "Duisburg",
                },
                recipient="Kunde GmbH\nKundenstraße 5\n47051 Duisburg",
                subject="Kundeninformation",
                body="Individueller Brieftext.",
                customer=customer,
            )
            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
            self.assertIn("K-7788", text)
            self.assertIn("kunde@example.test", text)
            self.assertIn("DE987654321", text)

    def test_letter_customer_context_reads_contact_and_crm_fields(self):
        context = letter_customer_context(
            {"fields": {"email": "kunde@example.test", "phone": "0203 123"}},
            {
                "customer_number": "K-9",
                "vat_id": "DE9",
                "tax_number": "T-9",
                "payment_terms": "netto 14",
                "currency": "EUR",
            },
        )
        self.assertEqual("K-9", context["customer_number"])
        self.assertEqual("0203 123", context["phone"])
        self.assertEqual("netto 14", context["payment_terms"])


if __name__ == "__main__":
    unittest.main()
