"""Invoice-position hints: grouping, article proposals, variance, creation route.

Unit tests cover normalization, pattern grouping, the 3-invoice article
threshold, price-spread detection, quarter targets, draft deviations,
and fail-closed rights/data handling. Route tests prove the editor
panel renders and the article POST creates exactly once.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from app import app
from app.business_document_generation import INVOICE_DIR
from app.contextflow.position_hints import (
    article_proposals,
    catalog_names,
    draft_flags,
    find_catalog_duplicate,
    next_quarter_start,
    normalize_position,
    parse_article_request,
    position_hints_for_contact,
    position_patterns,
    variance_hints,
)
from app.db import ensure_auth_database, get_db
from app.document_store import CONTROL_DIR
from app.object_store import ObjectStore
from app.password_security import hash_password

from tests import contextflow_fixtures as fx


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


def _line(description: str, unit: str = "HUR", net: str = "100.00", vat: str = "19") -> dict:
    return {
        "line_id": 1,
        "description": description,
        "quantity": "1",
        "unit": unit,
        "net_unit_price": net,
        "vat_rate": vat,
        "net_total": "0.00",
        "tax_total": "0.00",
        "gross_total": "0.00",
    }


class NormalizeTest(unittest.TestCase):
    def test_case_space_punctuation(self) -> None:
        self.assertEqual("anfahrt pauschal", normalize_position("  Anfahrt   Pauschal. "))
        self.assertEqual("anfahrt pauschal", normalize_position("ANFAHRT PAUSCHAL;"))
        self.assertEqual("", normalize_position(None))
        self.assertEqual("", normalize_position("   "))

    def test_numbers_keep_positions_apart(self) -> None:
        self.assertNotEqual(normalize_position("Wartung 01/26"), normalize_position("Wartung 02/26"))


class QuarterTest(unittest.TestCase):
    def test_next_quarter(self) -> None:
        self.assertEqual(date(2026, 4, 1), next_quarter_start(date(2026, 1, 15)))
        self.assertEqual(date(2026, 4, 1), next_quarter_start(date(2026, 3, 31)))
        self.assertEqual(date(2027, 1, 1), next_quarter_start(date(2026, 12, 31)))


class PatternTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contact_id = str(fx.make_contact(self.root)["contact_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _rows(self) -> list:
        from app.business_document_generation import invoices

        return invoices(self.root)

    def test_groups_by_description_and_unit(self) -> None:
        _write_invoice(self.root, self.contact_id, "a", lines=[_line("Anfahrt")])
        _write_invoice(self.root, self.contact_id, "b", lines=[_line("anfahrt.")])
        _write_invoice(self.root, self.contact_id, "c", lines=[_line("Anfahrt", unit="C62")])
        patterns = position_patterns(self._rows(), self.contact_id, "EUR")
        by_unit = {item["unit"]: item for item in patterns}
        self.assertEqual(2, by_unit["HUR"]["invoice_count"])
        self.assertEqual(1, by_unit["C62"]["invoice_count"])

    def test_counts_distinct_invoices_not_lines(self) -> None:
        _write_invoice(
            self.root, self.contact_id, "a",
            lines=[_line("Anfahrt", net="10.00"), _line("Anfahrt", net="20.00")],
        )
        patterns = position_patterns(self._rows(), self.contact_id, "EUR")
        self.assertEqual(1, patterns[0]["invoice_count"])
        self.assertEqual(2, len(patterns[0]["prices"]))

    def test_skips_drafts_foreign_currency(self) -> None:
        _write_invoice(self.root, self.contact_id, "d", status="draft", lines=[_line("Anfahrt")])
        _write_invoice(self.root, "other", "f", lines=[_line("Anfahrt")])
        _write_invoice(self.root, self.contact_id, "u", currency="USD", lines=[_line("Anfahrt")])
        self.assertEqual([], position_patterns(self._rows(), self.contact_id, "EUR"))

    def test_median_and_spread(self) -> None:
        for index, net in enumerate(("10.00", "30.00", "20.00")):
            _write_invoice(self.root, self.contact_id, f"i{index}", lines=[_line("Anfahrt", net=net)])
        (pattern,) = position_patterns(self._rows(), self.contact_id, "EUR")
        self.assertEqual(Decimal("20.00"), pattern["median_net"])
        self.assertEqual(Decimal("10.00"), pattern["min_net"])
        self.assertEqual(Decimal("30.00"), pattern["max_net"])
        self.assertTrue(pattern["spread"])

    def test_garbage_lines_skipped(self) -> None:
        _write_invoice(
            self.root, self.contact_id, "a",
            lines=[_line("", net="n/a"), "nope", _line("Anfahrt", net="10.00")],
        )
        (pattern,) = position_patterns(self._rows(), self.contact_id, "EUR")
        self.assertEqual(1, len(pattern["prices"]))


class ProposalTest(unittest.TestCase):
    def _pattern(self, count: int = 3, median: Decimal = Decimal("100.00")) -> dict:
        return {
            "key": "anfahrt",
            "unit": "HUR",
            "display": "Anfahrt",
            "invoice_count": count,
            "min_net": Decimal("100.00"),
            "max_net": Decimal("100.00"),
            "median_net": median,
            "spread": False,
            "prices": [{"net": median, "vat": Decimal("19"), "invoice_id": "x"}],
            "invoices": [],
        }

    def test_threshold_and_existing(self) -> None:
        self.assertEqual(1, len(article_proposals([self._pattern(3)], set(), "EUR")))
        self.assertEqual(0, len(article_proposals([self._pattern(2)], set(), "EUR")))
        self.assertEqual(0, len(article_proposals([self._pattern(5)], {"anfahrt"}, "EUR")))

    def test_proposal_fields(self) -> None:
        (proposal,) = article_proposals([self._pattern(4)], set(), "EUR")
        self.assertEqual("Anfahrt", proposal["name"])
        self.assertEqual("HUR", proposal["unit"])
        self.assertEqual("Stunde", proposal["unit_display"])
        self.assertEqual("100.00", proposal["net_price"])
        self.assertEqual("19", proposal["vat_rate"])
        self.assertEqual(4, proposal["invoice_count"])

    def test_proposal_uses_most_common_vat(self) -> None:
        pattern = self._pattern(4)
        pattern["prices"] = [
            {"net": Decimal("100.00"), "vat": Decimal("7"), "invoice_id": "a"},
            {"net": Decimal("100.00"), "vat": Decimal("19"), "invoice_id": "b"},
            {"net": Decimal("100.00"), "vat": Decimal("19"), "invoice_id": "c"},
        ]
        (proposal,) = article_proposals([pattern], set(), "EUR")
        self.assertEqual("19", proposal["vat_rate"])

    def test_variance_only_on_spread(self) -> None:
        spread = self._pattern(3)
        spread.update({"min_net": Decimal("90.00"), "max_net": Decimal("110.00"), "spread": True})
        (hint,) = variance_hints([spread, self._pattern(3)], "EUR", date(2026, 11, 20))
        self.assertEqual("90,00 EUR", hint["min_display"])
        self.assertEqual("110,00 EUR", hint["max_display"])
        self.assertEqual("01.01.2027", hint["quarter_target"])

    def test_draft_flags_deviations_only(self) -> None:
        flags = draft_flags(
            [
                _line("Anfahrt", net="150.00"),
                _line("Anfahrt", net="100.00"),
                _line("Unbekannt", net="1.00"),
            ],
            [self._pattern(3)],
            "EUR",
        )
        self.assertEqual(1, len(flags))
        self.assertEqual(1, flags[0]["line_no"])
        self.assertEqual("teurer", flags[0]["direction"])
        self.assertEqual("100,00 EUR", flags[0]["usual_display"])


class ArticleRequestTest(unittest.TestCase):
    def test_valid(self) -> None:
        parsed = parse_article_request(
            {"description": " Anfahrt ", "unit": "hur", "net_price": "100", "vat_rate": "19,0"}
        )
        self.assertEqual(
            {"description": "Anfahrt", "unit": "HUR", "net_price": "100.00", "vat_rate": "19.00"},
            parsed,
        )

    def test_invalid(self) -> None:
        base = {"description": "Anfahrt", "unit": "HUR", "net_price": "100", "vat_rate": "19"}
        for key, value in (
            ("description", ""),
            ("description", "x" * 241),
            ("unit", "XXX"),
            ("net_price", "n/a"),
            ("net_price", "-1"),
            ("vat_rate", "n/a"),
            ("vat_rate", "-1"),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    parse_article_request({**base, key: value})


class HintStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contact_id = str(fx.make_contact(self.root)["contact_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _seed_three(self, nets=("100.00", "100.00", "120.00")) -> None:
        for index, net in enumerate(nets):
            _write_invoice(
                self.root, self.contact_id, f"i{index}",
                number=f"RE-2026-00{index + 1}", lines=[_line("Anfahrt", net=net)],
            )

    def test_ready_with_all_sections(self) -> None:
        self._seed_three()
        hint = position_hints_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("ready", hint["state"])
        self.assertEqual(1, len(hint["proposals"]))
        self.assertEqual(1, len(hint["variance"]))
        self.assertIn("/crm", hint["crm_url"])

    def test_none_without_patterns(self) -> None:
        hint = position_hints_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("none", hint["state"])

    def test_hidden_for_foreign_and_unknown(self) -> None:
        self._seed_three()
        self.assertEqual(
            "hidden",
            position_hints_for_contact(self.root, fx.FOREIGN, self.contact_id)["state"],
        )
        self.assertEqual(
            "hidden",
            position_hints_for_contact(self.root, fx.OWNER, "nope")["state"],
        )

    def test_invalid_arguments_raise(self) -> None:
        with self.assertRaises(ValueError):
            position_hints_for_contact(self.root, "", self.contact_id)
        with self.assertRaises(ValueError):
            position_hints_for_contact(self.root, fx.OWNER, "  ")

    def test_foreign_draft_lines_ignored(self) -> None:
        self._seed_three()
        hint = position_hints_for_contact(
            self.root,
            fx.OWNER,
            self.contact_id,
            draft={"contact_id": "other", "lines": [_line("Anfahrt", net="999.00")]},
        )
        self.assertEqual("ready", hint["state"])
        self.assertEqual([], hint["draft_flags"])

    def test_duplicate_matches_invoice_description(self) -> None:
        ObjectStore(self.root).create(
            {
                "name": "Wartungspaket",
                "type": "object",
                "description": "x",
                "use_in_invoice": True,
                "invoice_description": "Anfahrt",
                "net_price": "100.00",
                "vat_rate": "19",
            },
            fx.OWNER,
        )
        self.assertIsNotNone(find_catalog_duplicate(self.root, "Anfahrt"))

    def test_existing_article_suppresses_proposal(self) -> None:
        self._seed_three(("100.00", "100.00", "100.00"))
        ObjectStore(self.root).create(
            {
                "name": "Anfahrt",
                "type": "object",
                "description": "Anfahrt",
                "use_in_invoice": True,
                "net_price": "100.00",
                "vat_rate": "19",
            },
            fx.OWNER,
        )
        hint = position_hints_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("ready", hint["state"])
        self.assertEqual([], hint["proposals"])
        self.assertEqual([], hint["variance"])

    def test_duplicate_lookup(self) -> None:
        self.assertIsNone(find_catalog_duplicate(self.root, "Anfahrt"))
        item = ObjectStore(self.root).create(
            {
                "name": "Anfahrt",
                "type": "object",
                "description": "Anfahrt",
                "use_in_invoice": True,
                "net_price": "100.00",
                "vat_rate": "19",
            },
            fx.OWNER,
        )
        self.assertEqual(item["display_id"], find_catalog_duplicate(self.root, "anfahrt."))
        ObjectStore(self.root).update(
            item["object_id"],
            {"name": "Anfahrt", "type": "object", "status": "retired"},
            fx.OWNER,
        )
        self.assertIsNone(find_catalog_duplicate(self.root, "Anfahrt"))


class PositionHintRoutesTest(unittest.TestCase):
    password = "position-hint-test-password"

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
            for username in ("admin", "berta"):
                db.execute(
                    "INSERT INTO user(username,password,display_name,is_admin,"
                    "is_disabled,auth_version,created_at,updated_at)"
                    " VALUES (?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, hash_password(self.password), username.title()),
                )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.admin = self.login("admin")
        self.berta = self.login("berta")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path: str) -> str:
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        assert match is not None
        return match.group(1).decode()

    def login(self, username: str):
        client = app.test_client()
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": self.csrf(client, "/auth/login"),
            },
        )
        return client

    def _seed(self) -> tuple[str, str]:
        contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        for index, net in enumerate(("100.00", "100.00", "120.00")):
            _write_invoice(
                self.root, contact_id, f"i{index}",
                number=f"RE-2026-00{index + 1}", lines=[_line("Anfahrt", net=net)],
            )
        draft = _write_invoice(
            self.root, contact_id, "draft-pos", status="draft", number="DRAFT-1",
            lines=[_line("Anfahrt", net="150.00")], gross="178.50",
        )
        draft.update(
            {"buyer": {"address_id": ""}, "service_date": "2026-10-21", "due_date": "2026-11-04"}
        )
        path = self.root / CONTROL_DIR / INVOICE_DIR / "draft-pos.json"
        path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        return contact_id, "draft-pos"

    def test_editor_panel_renders(self) -> None:
        contact_id, draft_id = self._seed()
        response = self.admin.get(
            f"/documents/business/contacts/{contact_id}/invoice?invoice_id={draft_id}"
        )
        self.assertEqual(200, response.status_code)
        body = response.data.decode("utf-8")
        self.assertIn('id="position-hints"', body)
        self.assertIn("Artikel anlegen", body)
        self.assertIn("unterschiedlich abgerechnet", body)
        self.assertIn("Brief/Notiz beim Kunden prüfen", body)
        self.assertIn("teurer als sonst", body)

    def test_article_post_creates_and_redirects(self) -> None:
        contact_id, draft_id = self._seed()
        editor = f"/documents/business/contacts/{contact_id}/invoice?invoice_id={draft_id}"
        response = self.admin.post(
            "/contextflow/position-article",
            data={
                "_csrf_token": self.csrf(self.admin, editor),
                "contact_id": contact_id,
                "invoice_id": draft_id,
                "description": "Anfahrt",
                "unit": "HUR",
                "net_price": "100.00",
                "vat_rate": "19",
            },
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(draft_id, response.location)
        names = [item["name"] for item in ObjectStore(self.root).objects()]
        self.assertIn("Anfahrt", names)
        created = next(item for item in ObjectStore(self.root).objects() if item["name"] == "Anfahrt")
        self.assertTrue(ObjectStore(self.root).invoice_effective(created)["use_in_invoice"])
        self.assertEqual("HUR", created["fields"].get("invoice_unit"))

    def test_article_post_duplicate_is_409(self) -> None:
        contact_id, draft_id = self._seed()
        ObjectStore(self.root).create(
            {
                "name": "Anfahrt",
                "type": "object",
                "description": "Anfahrt",
                "use_in_invoice": True,
                "net_price": "100.00",
                "vat_rate": "19",
            },
            "admin",
        )
        editor = f"/documents/business/contacts/{contact_id}/invoice?invoice_id={draft_id}"
        response = self.admin.post(
            "/contextflow/position-article",
            data={
                "_csrf_token": self.csrf(self.admin, editor),
                "contact_id": contact_id,
                "invoice_id": draft_id,
                "description": "anfahrt.",
                "unit": "HUR",
                "net_price": "100.00",
                "vat_rate": "19",
            },
        )
        self.assertEqual(409, response.status_code)

    def test_article_post_validates_and_checks_rights(self) -> None:
        contact_id, draft_id = self._seed()
        editor = f"/documents/business/contacts/{contact_id}/invoice?invoice_id={draft_id}"
        bad = self.admin.post(
            "/contextflow/position-article",
            data={
                "_csrf_token": self.csrf(self.admin, editor),
                "contact_id": contact_id,
                "description": "",
                "unit": "XXX",
                "net_price": "n/a",
                "vat_rate": "19",
            },
        )
        self.assertEqual(400, bad.status_code)
        foreign = self.berta.post(
            "/contextflow/position-article",
            data={
                "_csrf_token": self.csrf(self.berta, "/contextflow/"),
                "contact_id": contact_id,
                "description": "Anfahrt",
                "unit": "HUR",
                "net_price": "100.00",
                "vat_rate": "19",
            },
        )
        self.assertEqual(403, foreign.status_code)


if __name__ == "__main__":
    unittest.main()
