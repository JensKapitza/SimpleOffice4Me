"""Customer-credit hints: deterministic angel math plus route wiring.

Unit tests cover rate extraction, history selection, time conversion,
remaining-credit math, and fail-closed rights/data handling. Route tests
prove the dashboard card block and the invoice-editor panel render.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from app import app
from app.contextflow.credit_hints import (
    credit_hint_for_contact,
    format_eur,
    format_hours,
    hourly_rates_from_lines,
    latest_hourly_rate,
)
from app.customer_credit import CustomerCreditLedger
from app.db import ensure_auth_database, get_db
from app.document_store import CONTROL_DIR
from app.business_document_generation import INVOICE_DIR
from app.password_security import hash_password

from tests import contextflow_fixtures as fx

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=BERLIN)


def _write_invoice(
    root: Path,
    contact_id: str,
    invoice_id: str,
    *,
    status: str = "finalized",
    number: str = "RE-2026-001",
    issue_date: str = "2026-10-21",
    currency: str = "EUR",
    lines: list | None = None,
    gross: str = "119.00",
) -> dict:
    row = {
        "invoice_id": invoice_id,
        "contact_id": contact_id,
        "invoice_number": number,
        "status": status,
        "issue_date": issue_date,
        "currency": currency,
        "lines": lines if lines is not None else [],
        "totals": {"net": gross, "tax": "0.00", "gross": gross, "due": gross},
    }
    directory = Path(root) / CONTROL_DIR / INVOICE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{invoice_id}.json").write_text(
        json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return row


def _hour_line(net: str, vat: str = "19") -> dict:
    return {
        "line_id": 1,
        "description": "Beratung",
        "quantity": "2",
        "unit": "HUR",
        "net_unit_price": net,
        "vat_rate": vat,
        "net_total": "0.00",
        "tax_total": "0.00",
        "gross_total": "0.00",
    }


def _piece_line() -> dict:
    line = _hour_line("10.00")
    line["unit"] = "C62"
    return line


def _topup(root: Path, contact_id: str, amount: str = "100.00") -> None:
    CustomerCreditLedger(root).add(
        contact_id,
        amount,
        kind="topup",
        tax_treatment="outside_scope",
        actor=fx.OWNER,
    )


class FormatHelpersTest(unittest.TestCase):
    def test_format_eur_groups_thousands(self) -> None:
        self.assertEqual("1.234,50 EUR", format_eur(Decimal("1234.5")))

    def test_format_hours_minutes(self) -> None:
        self.assertEqual("45 Minuten", format_hours(Decimal("0.75")))
        self.assertEqual("1 Minute", format_hours(Decimal("0.02")))

    def test_format_hours_singular_and_plural(self) -> None:
        self.assertEqual("1 Stunde", format_hours(Decimal("1")))
        self.assertEqual("2,5 Stunden", format_hours(Decimal("2.5")))


class RateExtractionTest(unittest.TestCase):
    def test_hur_only_with_gross_math(self) -> None:
        rates = hourly_rates_from_lines([_piece_line(), _hour_line("100.00")])
        self.assertEqual(1, len(rates))
        self.assertEqual(Decimal("100.00"), rates[0]["rate_net"])
        self.assertEqual(Decimal("119.00"), rates[0]["rate_gross"])

    def test_garbage_lines_skipped(self) -> None:
        lines = [
            {"unit": "HUR", "net_unit_price": "n/a", "vat_rate": "19"},
            {"unit": "HUR", "net_unit_price": "0.00", "vat_rate": "19"},
            {"unit": "HUR", "net_unit_price": "-5", "vat_rate": "19"},
            {"unit": "HUR", "net_unit_price": "10", "vat_rate": "-1"},
            "not-a-dict",
        ]
        self.assertEqual([], hourly_rates_from_lines(lines))
        self.assertEqual([], hourly_rates_from_lines(None))
        self.assertEqual([], hourly_rates_from_lines("nope"))


class LatestRateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contact_id = str(fx.make_contact(self.root)["contact_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _rows(self) -> list:
        from app.business_document_generation import invoices

        return invoices(self.root)

    def test_newest_finalized_wins(self) -> None:
        _write_invoice(
            self.root, self.contact_id, "old", number="RE-2026-001",
            issue_date="2026-10-01", lines=[_hour_line("50.00")],
        )
        _write_invoice(
            self.root, self.contact_id, "new", number="RE-2026-002",
            issue_date="2026-10-20", lines=[_hour_line("100.00")],
        )
        found = latest_hourly_rate(self._rows(), self.contact_id, "EUR")
        assert found is not None
        self.assertEqual(Decimal("119.00"), found["rate_gross"])
        self.assertEqual("RE-2026-002", found["invoice_number"])
        self.assertIn("new", found["url"])

    def test_drafts_and_foreign_rows_skipped(self) -> None:
        _write_invoice(
            self.root, self.contact_id, "d1", status="draft",
            lines=[_hour_line("999.00")],
        )
        _write_invoice(
            self.root, "other-contact", "f1", number="RE-9",
            lines=[_hour_line("888.00")],
        )
        _write_invoice(
            self.root, self.contact_id, "usd", number="RE-8", currency="USD",
            lines=[_hour_line("777.00")],
        )
        self.assertIsNone(latest_hourly_rate(self._rows(), self.contact_id, "EUR"))

    def test_no_hour_lines_is_none(self) -> None:
        _write_invoice(
            self.root, self.contact_id, "p1", lines=[_piece_line()],
        )
        self.assertIsNone(latest_hourly_rate(self._rows(), self.contact_id, "EUR"))


class CreditHintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contact_id = str(fx.make_contact(self.root)["contact_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ready_with_history_rate(self) -> None:
        _topup(self.root, self.contact_id, "238.00")
        _write_invoice(
            self.root, self.contact_id, "inv1", lines=[_hour_line("100.00")],
        )
        hint = credit_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("ready", hint["state"])
        self.assertEqual("238,00 EUR", hint["balance_display"])
        self.assertEqual("2 Stunden", hint["hours_display"])
        self.assertEqual("119,00 EUR", hint["rate_display"])
        self.assertEqual("brutto", hint["rate_basis"])
        self.assertIn("RE-2026-001", hint["rate_source"])
        self.assertIn("inv1", hint["evidence"]["url"])
        self.assertIn(self.contact_id, hint["billing_url"])

    def test_draft_rate_wins_and_remaining_computed(self) -> None:
        _topup(self.root, self.contact_id, "500.00")
        _write_invoice(
            self.root, self.contact_id, "inv1", lines=[_hour_line("100.00")],
        )
        draft = {
            "invoice_id": "draft-1",
            "contact_id": self.contact_id,
            "status": "draft",
            "lines": [_hour_line("200.00", "19")],
            "totals": {"gross": "238.00"},
        }
        hint = credit_hint_for_contact(
            self.root, fx.OWNER, self.contact_id, draft=draft
        )
        self.assertEqual("ready", hint["state"])
        self.assertEqual("aktuellem Entwurf", hint["rate_source"])
        self.assertEqual("238,00 EUR", hint["rate_display"])
        self.assertEqual("262,00 EUR", hint["remaining_display"])

    def test_money_only_without_any_rate(self) -> None:
        _topup(self.root, self.contact_id, "50.00")
        hint = credit_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("ready", hint["state"])
        self.assertEqual("50,00 EUR", hint["balance_display"])
        self.assertNotIn("hours_display", hint)

    def test_zero_balance_is_none(self) -> None:
        hint = credit_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("none", hint["state"])

    def test_foreign_actor_is_hidden(self) -> None:
        _topup(self.root, self.contact_id, "50.00")
        hint = credit_hint_for_contact(self.root, fx.FOREIGN, self.contact_id)
        self.assertEqual("hidden", hint["state"])

    def test_unknown_contact_is_hidden(self) -> None:
        hint = credit_hint_for_contact(self.root, fx.OWNER, "no-such-contact")
        self.assertEqual("hidden", hint["state"])

    def test_corrupt_ledger_entry_is_hidden(self) -> None:
        _topup(self.root, self.contact_id, "50.00")
        path = self.root / CONTROL_DIR / "customer-credit-ledger.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"].append(
            {"contact_id": self.contact_id, "currency": "EUR", "signed_amount": "n/a"}
        )
        path.write_text(json.dumps(payload), encoding="utf-8")
        hint = credit_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("hidden", hint["state"])

    def test_invalid_arguments_raise(self) -> None:
        with self.assertRaises(ValueError):
            credit_hint_for_contact(self.root, "", self.contact_id)
        with self.assertRaises(ValueError):
            credit_hint_for_contact(self.root, fx.OWNER, "  ")

    def test_nan_ledger_balance_is_hidden(self) -> None:
        _topup(self.root, self.contact_id, "50.00")
        path = self.root / CONTROL_DIR / "customer-credit-ledger.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["entries"].append(
            {"contact_id": self.contact_id, "currency": "EUR", "signed_amount": "nan"}
        )
        path.write_text(json.dumps(payload), encoding="utf-8")
        hint = credit_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("hidden", hint["state"])

    def test_nan_rate_line_skipped(self) -> None:
        line = _hour_line("100.00")
        line["net_unit_price"] = "nan"
        self.assertEqual([], hourly_rates_from_lines([line]))

    def test_draft_currency_scopes_hint(self) -> None:
        _topup(self.root, self.contact_id, "50.00")
        draft = {
            "invoice_id": "draft-usd",
            "contact_id": self.contact_id,
            "status": "draft",
            "currency": "USD",
            "lines": [],
            "totals": {"gross": "10.00"},
        }
        hint = credit_hint_for_contact(
            self.root, fx.OWNER, self.contact_id, currency="USD", draft=draft
        )
        self.assertEqual("none", hint["state"])


class CreditHintRoutesTest(unittest.TestCase):
    password = "credit-hint-test-password"

    def setUp(self) -> None:
        self._now_patch = mock.patch(
            "app.contextflow.web._now", return_value=NOW
        )
        self._now_patch.start()
        self.addCleanup(self._now_patch.stop)
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
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?,?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.client = app.test_client()
        import re

        response = self.client.get("/auth/login")
        token = re.search(
            rb'name="_csrf_token" value="([^"]+)"', response.data
        ).group(1).decode()
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password, "_csrf_token": token},
        )

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def _seeded_contact(self) -> str:
        contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        _topup(self.root, contact_id, "238.00")
        _write_invoice(
            self.root, contact_id, "inv-route", lines=[_hour_line("100.00")],
        )
        return contact_id

    def test_dashboard_card_shows_credit(self) -> None:
        contact_id = self._seeded_contact()
        case_id = fx.make_mail_case(self.root, "admin")
        visit_id = fx.make_visit(self.root, "admin", completed=True)
        fx.confirm_link(
            self.root,
            "admin",
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
        )
        response = self.client.get("/contextflow/")
        self.assertEqual(200, response.status_code)
        body = response.data.decode("utf-8")
        self.assertIn("cf-credit", body)
        self.assertIn("238,00 EUR", body)
        self.assertIn("2 Stunden", body)

    def test_editor_panel_shows_remaining(self) -> None:
        contact_id = self._seeded_contact()
        draft = _write_invoice(
            self.root,
            contact_id,
            "draft-route",
            status="draft",
            number="DRAFT-1",
            lines=[_hour_line("200.00")],
            gross="238.00",
        )
        draft["buyer"] = {"address_id": ""}
        draft["service_date"] = "2026-10-21"
        draft["due_date"] = "2026-11-04"
        path = self.root / CONTROL_DIR / INVOICE_DIR / "draft-route.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        response = self.client.get(
            f"/documents/business/contacts/{contact_id}/invoice?invoice_id=draft-route"
        )
        self.assertEqual(200, response.status_code)
        body = response.data.decode("utf-8")
        self.assertIn('id="credit-hint"', body)
        self.assertIn("238,00 EUR", body)
        self.assertIn("1 Stunde", body)
        self.assertIn("0,00 EUR", body)


if __name__ == "__main__":
    unittest.main()
