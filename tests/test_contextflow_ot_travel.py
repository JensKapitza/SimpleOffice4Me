"""OT travel hints: off-site detection, travel math plus card wiring.

Unit tests cover keyword detection, distance parsing, cost/flat-rate
math, upcoming-event selection, and fail-closed rights/data handling.
Route tests prove the dashboard card block renders.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from app import app
from app.calendar_metadata import (
    metadata_lines,
    normalize_metadata,
    normalize_travel_km,
)
from app.calendar_store import CalendarStore
from app.contextflow.ot_travel_hints import (
    card_ot_travel_hint,
    is_offsite_event,
    next_offsite_event,
    ot_travel_hint_for_contact,
    travel_cost,
    travel_km_from_event,
)
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


def _add_event(
    root: Path,
    actor: str,
    contact_id: str,
    *,
    title: str = "Kundentermin Wartung",
    start: str = "2027-06-01T09:00:00",
    end: str = "2027-06-01T11:00:00",
    tags: list | None = None,
    travel_km: str | None = "100",
    location: str = "",
    reason: str = "Anfahrt und Wartung vor Ort (synthetisch)",
) -> dict:
    metadata: dict = {}
    if travel_km is not None:
        metadata["travel_km"] = travel_km
    if location:
        metadata["location"] = location
    return CalendarStore(root).add(
        title,
        reason,
        start,
        end,
        contact_id,
        actor,
        tags=tags if tags is not None else [{"name": "ot"}],
        metadata=metadata,
    )


class DetectionTest(unittest.TestCase):
    def test_tag_match(self) -> None:
        self.assertTrue(is_offsite_event({"tags": [{"name": "OT"}]}))
        self.assertTrue(is_offsite_event({"tags": [{"name": "vor-ort"}]}))

    def test_title_and_location_match(self) -> None:
        self.assertTrue(is_offsite_event({"title": "Kundentermin Müller"}))
        self.assertTrue(is_offsite_event({"title": "", "location": "Baustelle Nord"}))
        self.assertTrue(is_offsite_event({"title": "Inbetriebnahme Halle 3"}))

    def test_short_keyword_needs_word_boundary(self) -> None:
        self.assertTrue(is_offsite_event({"title": "OT beim Kunden"}))
        self.assertFalse(is_offsite_event({"title": "Brot backen"}))
        self.assertFalse(is_offsite_event({"title": "Motor prüfen"}))

    def test_office_events_and_garbage_miss(self) -> None:
        self.assertFalse(is_offsite_event({"title": "Jour fixe Büro"}))
        self.assertFalse(is_offsite_event({}))
        self.assertFalse(is_offsite_event(None))
        self.assertFalse(is_offsite_event("ot"))


class KmParseTest(unittest.TestCase):
    def test_valid_and_comma(self) -> None:
        self.assertEqual(Decimal("120"), travel_km_from_event({"travel_km": "120"}))
        self.assertEqual(Decimal("12.5"), travel_km_from_event({"travel_km": "12,5"}))

    def test_missing_and_garbage_is_none(self) -> None:
        self.assertIsNone(travel_km_from_event({}))
        self.assertIsNone(travel_km_from_event({"travel_km": ""}))
        self.assertIsNone(travel_km_from_event({"travel_km": "n/a"}))
        self.assertIsNone(travel_km_from_event({"travel_km": "NaN"}))
        self.assertIsNone(travel_km_from_event(None))

    def test_zero_negative_and_huge_is_none(self) -> None:
        self.assertIsNone(travel_km_from_event({"travel_km": "0"}))
        self.assertIsNone(travel_km_from_event({"travel_km": "-30"}))
        self.assertIsNone(travel_km_from_event({"travel_km": "99999"}))


class CostMathTest(unittest.TestCase):
    def test_vehicle_only(self) -> None:
        cost = travel_cost(Decimal("100"))
        self.assertEqual("30.00", cost["vehicle"])
        self.assertEqual("30,00 EUR", cost["vehicle_display"])
        self.assertEqual("2 Stunden", cost["drive_display"])
        self.assertFalse(cost["has_time_value"])
        self.assertEqual("30,00 EUR", cost["total_display"])
        self.assertEqual("30,00 EUR", cost["flat_display"])

    def test_time_value_and_flat_rounding(self) -> None:
        cost = travel_cost(Decimal("100"), hourly_gross=Decimal("119.00"))
        self.assertEqual("238.00", cost["time_value"])
        self.assertEqual("238,00 EUR", cost["time_display"])
        self.assertTrue(cost["has_time_value"])
        self.assertEqual("268.00", cost["total"])
        self.assertEqual("270,00 EUR", cost["flat_display"])

    def test_exact_step_stays(self) -> None:
        cost = travel_cost(Decimal("50"), hourly_gross=Decimal("35.00"))
        # 15.00 vehicle + 35.00 time = 50.00 -> flat stays 50.00
        self.assertEqual("50.00", cost["total"])
        self.assertEqual("50,00 EUR", cost["flat_display"])

    def test_invalid_inputs_raise(self) -> None:
        for bad in (Decimal("0"), Decimal("-5"), Decimal("NaN")):
            with self.assertRaises(ValueError):
                travel_cost(bad)
        with self.assertRaises(ValueError):
            travel_cost(Decimal("10"), per_km=Decimal("0"))
        with self.assertRaises(ValueError):
            travel_cost(Decimal("10"), hourly_gross=Decimal("-1"))


class MetadataTest(unittest.TestCase):
    def test_round_trip_and_comma(self) -> None:
        self.assertEqual("120", normalize_travel_km("120"))
        self.assertEqual("12.5", normalize_travel_km("12,5"))
        self.assertEqual("", normalize_travel_km(""))
        self.assertEqual("", normalize_travel_km(None))

    def test_garbage_raises(self) -> None:
        for bad in ("n/a", "0", "-3", "99999", "NaN"):
            with self.assertRaises(ValueError):
                normalize_travel_km(bad)

    def test_metadata_allowlists_and_preserves(self) -> None:
        merged = normalize_metadata({"travel_km": "80"}, {})
        self.assertEqual("80", merged["travel_km"])
        kept = normalize_metadata({}, {"travel_km": "80"})
        self.assertEqual("80", kept["travel_km"])


class HintTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contact_id = str(fx.make_contact(self.root)["contact_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ready_with_history_rate(self) -> None:
        _add_event(self.root, fx.OWNER, self.contact_id)
        _write_invoice(
            self.root, self.contact_id, "inv1", lines=[_hour_line("100.00")],
        )
        hint = ot_travel_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("ready", hint["state"])
        self.assertEqual("100 km", hint["km_display"])
        self.assertEqual("2 Stunden", hint["drive_display"])
        self.assertEqual("30,00 EUR", hint["vehicle_display"])
        self.assertEqual("270,00 EUR", hint["flat_display"])
        self.assertEqual("Rechnung RE-2026-001 vom 2026-10-21", hint["rate_source"])
        self.assertEqual("2027-06-01", hint["event_start"])

    def test_ready_without_any_rate(self) -> None:
        _add_event(self.root, fx.OWNER, self.contact_id)
        hint = ot_travel_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("ready", hint["state"])
        self.assertFalse(hint["has_time_value"])
        self.assertEqual("30,00 EUR", hint["flat_display"])
        self.assertNotIn("rate_source", hint)

    def test_none_without_event_or_distance(self) -> None:
        hint = ot_travel_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("none", hint["state"])
        _add_event(self.root, fx.OWNER, self.contact_id, travel_km=None)
        hint = ot_travel_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("none", hint["state"])

    def test_none_for_office_and_past(self) -> None:
        _add_event(
            self.root, fx.OWNER, self.contact_id, title="Jour fixe",
            tags=[{"name": "buero"}], reason="Wochenrunde im Büro (synthetisch)",
        )
        _add_event(
            self.root, fx.OWNER, self.contact_id, title="OT alt",
            start="2020-01-06T09:00:00", end="2020-01-06T10:00:00",
        )
        hint = ot_travel_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("none", hint["state"])

    def test_nearest_upcoming_wins(self) -> None:
        _add_event(
            self.root, fx.OWNER, self.contact_id, title="OT spät",
            start="2027-08-01T09:00:00", end="2027-08-01T10:00:00",
            travel_km="200",
        )
        _add_event(
            self.root, fx.OWNER, self.contact_id, title="OT früh",
            start="2027-05-01T09:00:00", end="2027-05-01T10:00:00",
            travel_km="60",
        )
        hint = ot_travel_hint_for_contact(
            self.root, fx.OWNER, self.contact_id, today="2027-01-01"
        )
        self.assertEqual("ready", hint["state"])
        self.assertEqual("OT früh", hint["event_title"])
        self.assertEqual("60 km", hint["km_display"])

    def test_foreign_actor_is_hidden(self) -> None:
        _add_event(self.root, fx.OWNER, self.contact_id)
        hint = ot_travel_hint_for_contact(self.root, "stranger", self.contact_id)
        self.assertEqual("hidden", hint["state"])

    def test_unknown_contact_is_hidden(self) -> None:
        hint = ot_travel_hint_for_contact(self.root, fx.OWNER, "missing-contact")
        self.assertEqual("hidden", hint["state"])

    def test_invalid_arguments_raise(self) -> None:
        with self.assertRaises(ValueError):
            ot_travel_hint_for_contact(self.root, "", self.contact_id)
        with self.assertRaises(ValueError):
            ot_travel_hint_for_contact(self.root, fx.OWNER, " ")

    def test_card_wrapper(self) -> None:
        self.assertIsNone(card_ot_travel_hint(self.root, fx.OWNER, ""))
        self.assertIsNone(
            card_ot_travel_hint(self.root, fx.OWNER, "missing-contact")
        )
        _add_event(self.root, fx.OWNER, self.contact_id)
        card = card_ot_travel_hint(self.root, fx.OWNER, contact_id=self.contact_id)
        assert card is not None
        self.assertEqual("ready", card["state"])


class SelectionTest(unittest.TestCase):
    def test_pure_selection_filters(self) -> None:
        events = [
            {"contact_id": "c1", "status": "cancelled", "travel_km": "50",
             "title": "OT x", "start": "2027-03-01T09:00:00"},
            {"contact_id": "c2", "travel_km": "50", "title": "OT x",
             "start": "2027-03-01T09:00:00"},
            {"contact_id": "c1", "travel_km": "50", "title": "OT x",
             "start": "2020-01-01T09:00:00"},
            {"contact_id": "c1", "travel_km": "50", "title": "OT ok",
             "start": "2027-03-01T09:00:00"},
        ]
        picked = next_offsite_event(events, "c1", "2027-01-01")
        assert picked is not None
        self.assertEqual("OT ok", picked["title"])
        self.assertIsNone(next_offsite_event(events, "c1", "2028-01-01"))
        self.assertIsNone(next_offsite_event(None, "c1", "2027-01-01"))
        self.assertIsNone(next_offsite_event("nope", "c1", "2027-01-01"))


class StoreRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contact_id = str(fx.make_contact(self.root)["contact_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_add_get_and_update_preserve_km(self) -> None:
        created = _add_event(self.root, fx.OWNER, self.contact_id, travel_km="80,5")
        self.assertEqual("80.5", created["travel_km"])
        stored = CalendarStore(self.root).get(created["event_id"], fx.OWNER)
        self.assertEqual("80.5", stored["travel_km"])
        CalendarStore(self.root).update(
            created["event_id"],
            "Kundentermin Wartung",
            "Anfahrt (synthetisch)",
            "2027-06-01T09:00:00",
            "2027-06-01T11:00:00",
            self.contact_id,
            fx.OWNER,
            "private",
            "",
            [{"name": "ot"}],
        )
        kept = CalendarStore(self.root).get(created["event_id"], fx.OWNER)
        self.assertEqual("80.5", kept["travel_km"])

    def test_add_with_garbage_km_raises(self) -> None:
        with self.assertRaises(ValueError):
            _add_event(self.root, fx.OWNER, self.contact_id, travel_km="weit")

    def test_ics_export_ignores_km(self) -> None:
        lines = metadata_lines(
            {"travel_km": "100", "location": "Baustelle"}, lambda value: value
        )
        self.assertIn("LOCATION:Baustelle", lines)
        self.assertFalse(any("100" in line for line in lines))


class EdgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contact_id = str(fx.make_contact(self.root)["contact_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_non_decimal_and_infinite_cost_raise(self) -> None:
        for bad in (100, 12.5, "100", Decimal("Infinity")):
            with self.assertRaises(ValueError):
                travel_cost(bad)  # type: ignore[arg-type]

    def test_foreign_contact_event_is_none(self) -> None:
        other = str(fx.make_contact(self.root)["contact_id"])
        _add_event(self.root, fx.OWNER, other)
        hint = ot_travel_hint_for_contact(self.root, fx.OWNER, self.contact_id)
        self.assertEqual("none", hint["state"])

    def test_travel_km_matches_shared_max(self) -> None:
        from app.calendar_metadata import MAX_TRAVEL_KM

        self.assertIsNone(
            travel_km_from_event({"travel_km": str(MAX_TRAVEL_KM + 1)})
        )
        self.assertEqual(
            MAX_TRAVEL_KM, travel_km_from_event({"travel_km": str(MAX_TRAVEL_KM)})
        )


class TravelHintRoutesTest(unittest.TestCase):
    password = "ot-travel-hint-test-password"

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
        response = self.client.get("/auth/login")
        token = re.search(
            rb'name="_csrf_token" value="([^"]+)"', response.data
        ).group(1).decode()
        self.client.post(
            "/auth/login",
            data={"username": "admin", "password": self.password, "_csrf_token": token},
        )
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_dashboard_card_shows_travel(self) -> None:
        contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        _write_invoice(
            self.root, contact_id, "inv-route", lines=[_hour_line("100.00")],
        )
        _add_event(self.root, "admin", contact_id)
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
        self.assertIn("cf-travel", body)
        self.assertIn("100 km", body)
        self.assertIn("Pauschalvorschlag: 270,00 EUR", body)
