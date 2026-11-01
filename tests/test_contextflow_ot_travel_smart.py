"""Smart OT travel analysis: grounded verdicts, capped proposals, closed gates.

Unit tests cover prompt building, event-text assembly, upcoming-event
selection, evidence-grounded validation (unbacked verdicts/distances
are dropped, proposals stay explicitly unverified and capped),
provider gates (local Meta only, mock refused, cloud refused), the
single text-only model call (stubbed; live inference is NOT verified
here), and the /ot-travel-analyze route (rights, 404s, CSRF, 503).
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import app
from app.calendar_store import CalendarStore
from app.contextflow.ot_travel_smart import (
    build_ot_smart_prompt,
    event_source_text,
    find_analyzable_event,
    suggest_ot_travel_analysis,
    validate_ot_smart_analysis,
)
from app.contextflow.vision import (
    InferenceUnavailable,
    MockBlockedError,
    VisionError,
)
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password

from tests import contextflow_fixtures as fx

SOURCE = "Kundentermin Wartung\nWerkshalle Nord, Anfahrt 120 km\nTags: ot"


def _settings(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "provider": "local",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "llama3.2:1b",
        "ollama_timeout": 60,
        "meta_consent": False,
        "meta_api_key": "",
        "meta_endpoint": "",
        "meta_model": "",
    }
    base.update(overrides)
    return base


class StubProvider:
    """Fake Meta provider: records prompts, answers canned payloads."""

    def __init__(self, payload: object, name: str = "meta") -> None:
        self.name = name
        self.label = "TestMeta"
        self.model = "test-meta-1"
        self.resolved_model: str | None = None
        self.payload = payload
        self.prompts: list[str] = []

    def complete_text(self, prompt: str) -> object:
        self.prompts.append(prompt)
        return self.payload


def _event(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "event_id": "ev-1",
        "contact_id": "c-1",
        "title": "Kundentermin Wartung",
        "location": "Werkshalle Nord",
        "reason": "Anfahrt 120 km",
        "tags": [{"name": "ot"}],
        "status": "active",
        "start": "2027-06-01T09:00:00",
    }
    base.update(overrides)
    return base


class PromptTest(unittest.TestCase):
    def test_prompt_carries_event_and_forbids_invention(self) -> None:
        prompt = build_ot_smart_prompt(SOURCE)
        self.assertIn("Werkshalle Nord", prompt)
        self.assertIn("distance_km", prompt)
        self.assertIn("unclear", prompt)
        self.assertIn("NIEMALS", prompt)

    def test_source_text_joins_fields_and_tags(self) -> None:
        text = event_source_text(_event())
        self.assertIn("Kundentermin Wartung", text)
        self.assertIn("Tags: ot", text)

    def test_source_text_empty_for_non_dict(self) -> None:
        self.assertEqual("", event_source_text(None))
        self.assertEqual("", event_source_text({"title": "", "tags": []}))


class FindEventTest(unittest.TestCase):
    def test_next_upcoming_wins(self) -> None:
        events = [_event(event_id="late", start="2027-07-01T09:00:00"), _event()]
        picked = find_analyzable_event(events, "c-1", today="2027-01-01")
        assert picked is not None
        self.assertEqual("ev-1", picked["event_id"])

    def test_explicit_id_wins_over_date(self) -> None:
        events = [_event(event_id="late", start="2027-07-01T09:00:00"), _event()]
        picked = find_analyzable_event(
            events, "c-1", event_id="late", today="2027-01-01"
        )
        assert picked is not None
        self.assertEqual("late", picked["event_id"])

    def test_skips_foreign_cancelled_and_past(self) -> None:
        events = [
            _event(event_id="foreign", contact_id="c-9"),
            _event(event_id="gone", status="cancelled"),
            _event(event_id="past", start="2026-01-01T09:00:00"),
            _event(),
        ]
        picked = find_analyzable_event(events, "c-1", today="2027-01-01")
        assert picked is not None
        self.assertEqual("ev-1", picked["event_id"])

    def test_unknown_id_and_empty_yield_none(self) -> None:
        self.assertIsNone(find_analyzable_event([_event()], "c-1", event_id="nope"))
        self.assertIsNone(find_analyzable_event([], "c-1", today="2027-01-01"))
        self.assertIsNone(find_analyzable_event("nope", "c-1"))


class ValidateTest(unittest.TestCase):
    def test_grounded_verdict_with_proposal_stays_unverified(self) -> None:
        raw = {
            "verdict": "offsite",
            "confidence": "high",
            "evidence": "Kundentermin Wartung",
            "distance_km": 120,
            "distance_evidence": "Anfahrt 120 km",
        }
        analysis, dropped = validate_ot_smart_analysis(raw, SOURCE)
        self.assertEqual(0, dropped)
        self.assertEqual("offsite", analysis["verdict"])
        self.assertEqual("120", analysis["distance_proposal_km"])
        self.assertTrue(analysis["unverified_proposal"])

    def test_missing_evidence_forces_unclear(self) -> None:
        raw = {
            "verdict": "offsite",
            "confidence": "high",
            "evidence": "erfundenes Zitat",
            "distance_km": None,
            "distance_evidence": None,
        }
        analysis, dropped = validate_ot_smart_analysis(raw, SOURCE)
        self.assertEqual(1, dropped)
        self.assertEqual("unclear", analysis["verdict"])
        self.assertNotIn("distance_proposal_km", analysis)

    def test_invented_distance_is_dropped_verdict_kept(self) -> None:
        raw = {
            "verdict": "offsite",
            "confidence": "medium",
            "evidence": "Kundentermin Wartung",
            "distance_km": 999,
            "distance_evidence": "weite Anfahrt",
        }
        analysis, dropped = validate_ot_smart_analysis(raw, SOURCE)
        self.assertEqual(1, dropped)
        self.assertEqual("offsite", analysis["verdict"])
        self.assertNotIn("distance_proposal_km", analysis)

    def test_overcap_and_garbage_distances_are_dropped(self) -> None:
        for distance in (5000.01, 9000, "weit", -5, 0):
            raw = {
                "verdict": "offsite",
                "confidence": "low",
                "evidence": "Kundentermin Wartung",
                "distance_km": distance,
                "distance_evidence": "Anfahrt 120 km",
            }
            analysis, dropped = validate_ot_smart_analysis(raw, SOURCE)
            self.assertEqual(1, dropped, distance)
            self.assertNotIn("distance_proposal_km", analysis)

    def test_malformed_payloads_raise(self) -> None:
        for raw in (
            None,
            [],
            {"confidence": "high", "evidence": "Kundentermin Wartung"},
            {"verdict": "maybe", "confidence": "high", "evidence": "x"},
            {"verdict": "offsite", "confidence": "sure", "evidence": "x"},
        ):
            with self.assertRaises(VisionError, msg=repr(raw)):
                validate_ot_smart_analysis(raw, SOURCE)


class GatesTest(unittest.TestCase):
    def test_mock_is_refused(self) -> None:
        with self.assertRaises(MockBlockedError):
            suggest_ot_travel_analysis(
                event_text=SOURCE, settings=_settings(provider="mock")
            )

    def test_local_without_model_is_unavailable(self) -> None:
        with self.assertRaises(InferenceUnavailable):
            suggest_ot_travel_analysis(
                event_text=SOURCE, settings=_settings(ollama_model="")
            )

    def test_meta_without_consent_is_refused(self) -> None:
        with self.assertRaises(PermissionError):
            suggest_ot_travel_analysis(
                event_text=SOURCE, settings=_settings(provider="meta")
            )

    def test_empty_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            suggest_ot_travel_analysis(event_text="  ", settings=_settings())


class SuggestTest(unittest.TestCase):
    def test_single_call_returns_grounded_result(self) -> None:
        provider = StubProvider(
            {
                "verdict": "offsite",
                "confidence": "medium",
                "evidence": "Kundentermin Wartung",
                "distance_km": 120,
                "distance_evidence": "Anfahrt 120 km",
            }
        )
        with mock.patch(
            "app.contextflow.ot_travel_smart.select_provider", return_value=provider
        ):
            result = suggest_ot_travel_analysis(
                event_text=SOURCE, settings=_settings()
            )
        self.assertEqual(1, len(provider.prompts))
        self.assertIn("Werkshalle Nord", provider.prompts[0])
        self.assertEqual("offsite", result["verdict"])
        self.assertEqual("TestMeta", result["provider_label"])
        self.assertEqual("test-meta-1", result["model"])
        self.assertTrue(result["unverified_proposal"])

    def test_dropped_claims_warn_without_invention(self) -> None:
        provider = StubProvider(
            {
                "verdict": "offsite",
                "confidence": "medium",
                "evidence": "Kundentermin Wartung",
                "distance_km": 4242,
                "distance_evidence": "weite Strecke",
            }
        )
        with mock.patch(
            "app.contextflow.ot_travel_smart.select_provider", return_value=provider
        ):
            result = suggest_ot_travel_analysis(
                event_text=SOURCE, settings=_settings()
            )
        self.assertNotIn("distance_proposal_km", result)
        self.assertTrue(any("verworfen" in item for item in result["warnings"]))


class OtTravelAnalyzeWebTest(unittest.TestCase):
    password = "contextflow-ot-smart-test-password"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "TESTING",
                "TEST_CSRF_PROTECTION", "CONTEXTFLOW_ENABLED",
                "CONTEXTFLOW_VISION_PROVIDER", "CONTEXTFLOW_OLLAMA_URL",
                "CONTEXTFLOW_OLLAMA_MODEL", "CONTEXTFLOW_OLLAMA_TIMEOUT",
            )
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
            CONTEXTFLOW_VISION_PROVIDER="mock",
            CONTEXTFLOW_OLLAMA_URL="http://127.0.0.1:11434",
            CONTEXTFLOW_OLLAMA_MODEL="llama3.2:1b",
            CONTEXTFLOW_OLLAMA_TIMEOUT=60,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            for username, admin in (("admin", 1), ("berta", 0)):
                db.execute(
                    "INSERT INTO user(username,password,display_name,is_admin,"
                    "is_disabled,auth_version,created_at,updated_at)"
                    " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, hash_password(self.password), username.title(), admin),
                )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.admin = self.login("admin")
        self.berta = self.login("berta")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path="/contextflow/") -> str:
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def login(self, username: str):
        client = app.test_client()
        client.post(
            "/auth/login",
            data={"username": username, "password": self.password,
                  "_csrf_token": self.csrf(client, "/auth/login")},
        )
        return client

    def _contact_and_event(self):
        contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        created = CalendarStore(self.root).add(
            "Besprechung (synthetisch)",
            "Statusrunde ohne Ortsangabe",
            "2027-06-01T09:00:00",
            "2027-06-01T10:00:00",
            contact_id,
            "admin",
            tags=[{"name": "meeting"}],
            metadata={},
        )
        return contact_id, str(created["event_id"])

    def test_analyze_returns_grounded_json(self) -> None:
        contact_id, event_id = self._contact_and_event()
        provider = StubProvider(
            {
                "verdict": "not_offsite",
                "confidence": "high",
                "evidence": "Statusrunde ohne Ortsangabe",
                "distance_km": None,
                "distance_evidence": None,
            }
        )
        with mock.patch(
            "app.contextflow.ot_travel_smart.select_provider", return_value=provider
        ):
            response = self.admin.post(
                "/contextflow/ot-travel-analyze",
                data={
                    "_csrf_token": self.csrf(self.admin),
                    "contact_id": contact_id,
                    "event_id": event_id,
                },
            )
        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(event_id, payload["event_id"])
        self.assertEqual("not_offsite", payload["verdict"])
        self.assertNotIn("distance_proposal_km", payload)

    def test_missing_contact_is_400_and_unknown_is_404(self) -> None:
        token = self.csrf(self.admin)
        response = self.admin.post(
            "/contextflow/ot-travel-analyze", data={"_csrf_token": token}
        )
        self.assertEqual(400, response.status_code)
        response = self.admin.post(
            "/contextflow/ot-travel-analyze",
            data={"_csrf_token": token, "contact_id": "kontakt-404"},
        )
        self.assertEqual(404, response.status_code)
        contact_id, _ = self._contact_and_event()
        response = self.admin.post(
            "/contextflow/ot-travel-analyze",
            data={
                "_csrf_token": token,
                "contact_id": contact_id,
                "event_id": "termin-404",
            },
        )
        self.assertEqual(404, response.status_code)

    def test_foreign_contact_is_404_leckfrei(self) -> None:
        contact_id, event_id = self._contact_and_event()
        token = self.csrf(self.berta)
        response = self.berta.post(
            "/contextflow/ot-travel-analyze",
            data={
                "_csrf_token": token,
                "contact_id": contact_id,
                "event_id": event_id,
            },
        )
        self.assertEqual(404, response.status_code)

    def test_shared_readonly_contact_is_403(self) -> None:
        from app.contact_store import ContactStore

        contact_id, event_id = self._contact_and_event()
        ContactStore(self.root).share(contact_id, [], "admin", readers=["berta"])
        token = self.csrf(self.berta)
        response = self.berta.post(
            "/contextflow/ot-travel-analyze",
            data={
                "_csrf_token": token,
                "contact_id": contact_id,
                "event_id": event_id,
            },
        )
        self.assertEqual(403, response.status_code)

    def test_mock_answers_503_without_invention(self) -> None:
        contact_id, _ = self._contact_and_event()
        response = self.admin.post(
            "/contextflow/ot-travel-analyze",
            data={
                "_csrf_token": self.csrf(self.admin),
                "contact_id": contact_id,
            },
        )
        self.assertEqual(503, response.status_code)


if __name__ == "__main__":
    unittest.main()
