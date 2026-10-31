"""ContextFlow P0-G: Meta runtime agent with a real tool loop.

Run with the project venv (``.venv``) and TEMP/TMP pointing to a short
local path, e.g. ``C:\\Users\\Jens Kapitza\\AppData\\Local\\Temp``. Live
Meta calls are never made here: every transport test uses a scripted
fake, and the suite asserts the key and private canary never appear in
recorded request bodies.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from app.contextflow.demo import DEMO_MARKER, DEMO_TOOL_TAG, seed_demo
from app.contextflow.meta_agent import (
    AGENT_RUN_LIMIT,
    AGENT_RUN_WINDOW_SECONDS,
    MAX_AGENT_STEPS,
    META_MAX_OUTPUT_TOKENS,
    MetaAgentError,
    _default_transport,
    release_agent_run,
    reserve_agent_run,
    reset_agent_run_limits,
    run_agent,
)
from app.contextflow.vision import META_MODEL_API_ENDPOINT, META_MODEL_API_MODEL
from app.mail_case_store import MailCaseStore
from app.site_visit_store import SiteVisitStore
from app.todo_store import TodoStore

from tests import contextflow_fixtures as fx

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=BERLIN)
CANARY = "CF_PRIVATE_SENTINEL_DO_NOT_EXFILTRATE"


def _write_marker(root: Path, *, synthetic: bool = True) -> None:
    (root / DEMO_MARKER).write_text(
        json.dumps({"synthetic": synthetic, "tool": DEMO_TOOL_TAG}) + "\n",
        encoding="utf-8",
    )


def _settings(**overrides):
    params = {
        "provider": "meta",
        "meta_consent": True,
        "meta_api_key": "test-only-dummy-key",
        "meta_endpoint": META_MODEL_API_ENDPOINT,
        "meta_model": META_MODEL_API_MODEL,
    }
    params.update(overrides)
    return params


def _call_payload(*calls) -> dict:
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            }
            for call_id, name, args in calls
        ],
    }


def _text_payload(obj) -> dict:
    return {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(obj)}],
            }
        ],
    }


class FakeTransport:
    """Scripted Responses transport; records every request body."""

    def __init__(self, payloads: list) -> None:
        self.payloads = list(payloads)
        self.bodies: list = []

    def __call__(self, body: dict) -> dict:
        self.bodies.append(json.loads(json.dumps(body)))
        assert self.payloads, "transport called more often than scripted"
        return self.payloads.pop(0)


class MetaAgentLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _write_marker(self.root)
        self.contact_id = str(fx.make_contact(self.root)["contact_id"])
        self.case_id = fx.make_mail_case(self.root)
        self.visit_id = fx.make_visit(self.root, completed=True)
        SiteVisitStore(self.root).add_finding(
            self.visit_id,
            fx.OWNER,
            {"title": "Zweiter Befund (synthetisch)", "status": "unreviewed"},
        )
        fx.make_invoice_row(
            self.root, self.contact_id, "CF-INV-1",
            status="open", invoice_number="RE-2026-008",
        )
        fx.make_event(
            self.root,
            start=(NOW - timedelta(days=1)).isoformat(),
            end=(NOW + timedelta(hours=1)).isoformat(),
            contact_id=self.contact_id,
        )
        from app.contextflow.adapters import ContextflowReads

        items = ContextflowReads(self.root).read_calendar(
            fx.OWNER, NOW - timedelta(days=7), NOW + timedelta(days=30)
        )["items"]
        assert items, "fixture event must be visible in the tool window"
        self.event_id = str(items[0]["event_id"])
        self.link = fx.confirm_link(
            self.root,
            fx.OWNER,
            case_id=self.case_id,
            contact_id=self.contact_id,
            visit_id=self.visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            invoice_id="CF-INV-1",
            now=NOW,
        )
        self.link_id = str(self.link["link_id"])
        record = SiteVisitStore(self.root).get(self.visit_id, fx.OWNER)
        self.finding_ids = [
            str(item["finding_id"]) for item in record.get("findings", [])
        ]
        assert len(self.finding_ids) == 2

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _run(self, transport, **overrides):
        params = {
            "actor": fx.OWNER,
            "link_id": self.link_id,
            "consent_meta": True,
            "settings": _settings(),
            "root": self.root,
            "now": NOW,
            "transport": transport,
        }
        params.update(overrides)
        return run_agent(**params)

    def _final(self, **overrides):
        proposal = {
            "summary": "Zwei Befunde offen, Rechnung erstellt (synthetisch).",
            "findings": [
                {
                    "title": "Offene Befunde",
                    "detail": "Zwei Befunde warten auf Prüfung.",
                    "severity": "medium",
                }
            ],
            "evidence_ref_ids": [self.case_id, self.finding_ids[0]],
            "unknowns": ["Ersatzteilnummer unbekannt."],
            "proposed_actions": [
                {
                    "type": "task.create",
                    "title": "Befunde nachtragen",
                    "target": self.finding_ids[0],
                    "why": "Nacharbeit liegt vor.",
                }
            ],
            "requires_approval": True,
        }
        proposal.update(overrides)
        return proposal

    def test_happy_path_two_tools_then_proposal(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                    ("c2", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["ok"])
        self.assertEqual("meta", result["provider"])
        self.assertEqual(META_MODEL_API_MODEL, result["model"])
        self.assertTrue(result["requires_approval"])
        self.assertEqual(2, len(result["tool_calls"]))
        self.assertTrue(all(call["ok"] for call in result["tool_calls"]))
        self.assertEqual(2, result["steps"])
        first = transport.bodies[0]
        self.assertEqual(META_MODEL_API_MODEL, first["model"])
        self.assertEqual(META_MAX_OUTPUT_TOKENS, first["max_output_tokens"])
        self.assertEqual({"effort": "minimal"}, first["reasoning"])
        self.assertEqual(
            {
                "get_case_context",
                "get_visit_findings",
                "get_invoice_state",
                "get_calendar_context",
            },
            {tool["name"] for tool in first["tools"]},
        )
        first_text = json.dumps(first, ensure_ascii=False)
        self.assertIn(self.link_id, first_text)
        self.assertNotIn("Papierstau", first_text)
        self.assertNotIn(fx.CONTACT_EMAIL, first_text)

    def test_response_model_echo_is_captured(self) -> None:
        final = _text_payload(self._final())
        final["model"] = META_MODEL_API_MODEL
        final["usage"] = {"input_tokens": 30, "output_tokens": 40}
        call = _call_payload(
            ("c1", "get_case_context", {"link_id": self.link_id}),
            ("c2", "get_visit_findings", {"link_id": self.link_id}),
        )
        call["model"] = META_MODEL_API_MODEL
        call["usage"] = {"input_tokens": 10, "output_tokens": 20}
        transport = FakeTransport([call, final])
        result = self._run(transport)
        self.assertTrue(result["ok"])
        self.assertEqual(META_MODEL_API_MODEL, result["model"])
        self.assertEqual(META_MODEL_API_MODEL, result["response_model"])
        self.assertEqual([META_MODEL_API_MODEL] * 2, result["response_models"])
        self.assertEqual(
            f"{META_MODEL_API_MODEL}, {META_MODEL_API_MODEL}",
            result["response_models_display"],
        )
        self.assertEqual({"input_tokens": 40, "output_tokens": 60}, result["usage"])

    def test_divergent_response_models_remain_visible_in_step_history(self) -> None:
        final = _text_payload(self._final())
        final["model"] = META_MODEL_API_MODEL
        call = _call_payload(
            ("c1", "get_case_context", {"link_id": self.link_id}),
            ("c2", "get_visit_findings", {"link_id": self.link_id}),
        )
        call["model"] = "unexpected-proxy-model"
        result = self._run(FakeTransport([call, final]))
        self.assertTrue(result["ok"])
        self.assertEqual(META_MODEL_API_MODEL, result["response_model"])
        self.assertEqual(
            ["unexpected-proxy-model", META_MODEL_API_MODEL],
            result["response_models"],
        )
        self.assertEqual(
            "unexpected-proxy-model, " + META_MODEL_API_MODEL,
            result["response_models_display"],
        )

    def test_response_model_missing_stays_none(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                    ("c2", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["response_model"])
        self.assertEqual([None, None], result["response_models"])
        self.assertEqual("unbekannt, unbekannt", result["response_models_display"])

    def test_invoice_and_calendar_tools(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_invoice_state", {"link_id": self.link_id}),
                    ("c2", "get_calendar_context", {"link_id": self.link_id}),
                ),
                _text_payload(
                    self._final(
                        evidence_ref_ids=["CF-INV-1", self.event_id]
                    )
                ),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["ok"])
        bodies = json.dumps(transport.bodies, ensure_ascii=False)
        self.assertIn("RE-2026-008", bodies)
        self.assertIn(self.event_id, bodies)

    def test_unknown_tool_gets_error_and_run_recovers(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "drop_tables", {"link_id": self.link_id}),
                ),
                _call_payload(
                    ("c2", "get_case_context", {"link_id": self.link_id}),
                    ("c3", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["ok"])
        self.assertEqual(3, len(result["tool_calls"]))
        self.assertFalse(result["tool_calls"][0]["ok"])
        second = json.dumps(transport.bodies[1], ensure_ascii=False)
        self.assertIn("unknown tool", second)

    def test_wrong_link_arg_rejected(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": "foreign-link"}),
                ),
                _call_payload(
                    ("c2", "get_case_context", {"link_id": self.link_id}),
                    ("c3", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["ok"])
        self.assertFalse(result["tool_calls"][0]["ok"])

    def test_unknown_argument_rejected(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    (
                        "c1",
                        "get_case_context",
                        {"link_id": self.link_id, "evil": "x"},
                    ),
                ),
                _call_payload(
                    ("c2", "get_case_context", {"link_id": self.link_id}),
                    ("c3", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["ok"])
        self.assertFalse(result["tool_calls"][0]["ok"])
        self.assertIn("unknown tool argument", result["tool_calls"][0]["detail"])

    def test_calls_beyond_step_budget_get_error_output(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                    ("c2", "get_visit_findings", {"link_id": self.link_id}),
                    ("c3", "get_invoice_state", {"link_id": self.link_id}),
                    ("c4", "get_calendar_context", {"link_id": self.link_id}),
                    ("c5", "get_case_context", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["ok"])
        self.assertEqual(5, len(result["tool_calls"]))
        self.assertTrue(
            all(call["ok"] for call in result["tool_calls"][:4])
        )
        self.assertFalse(result["tool_calls"][4]["ok"])
        self.assertIn("budget exceeded", result["tool_calls"][4]["detail"])
        second = json.dumps(transport.bodies[1], ensure_ascii=False)
        self.assertIn("budget exceeded", second)

    def test_tool_call_details_recorded(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    (
                        "c1",
                        "get_visit_findings",
                        {"link_id": self.link_id, "status_filter": "all"},
                    ),
                    ("c2", "get_case_context", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        result = self._run(transport)
        by_name = {call["name"]: call for call in result["tool_calls"]}
        self.assertEqual(
            "2 Befunde (Filter: all)", by_name["get_visit_findings"]["detail"]
        )
        self.assertTrue(
            by_name["get_case_context"]["detail"].startswith("Fall: ")
        )

    def test_bad_status_filter_rejected(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    (
                        "c1",
                        "get_visit_findings",
                        {"link_id": self.link_id, "status_filter": "bogus"},
                    ),
                ),
                _call_payload(
                    ("c2", "get_case_context", {"link_id": self.link_id}),
                    ("c3", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["ok"])
        self.assertFalse(result["tool_calls"][0]["ok"])

    def test_wrong_model_refused_before_transport(self) -> None:
        transport = FakeTransport([])
        with self.assertRaises(MetaAgentError):
            self._run(transport, settings=_settings(meta_model="gpt-x"))
        self.assertEqual(0, len(transport.bodies))

    def test_wrong_endpoint_refused_before_transport(self) -> None:
        transport = FakeTransport([])
        with self.assertRaises(MetaAgentError):
            self._run(
                transport,
                settings=_settings(meta_endpoint="https://evil.example.test/v1"),
            )
        self.assertEqual(0, len(transport.bodies))

    def test_consent_missing_refused(self) -> None:
        for bad in (False, None, "yes"):
            transport = FakeTransport([])
            with self.assertRaises(PermissionError, msg=repr(bad)):
                self._run(transport, consent_meta=bad)
            self.assertEqual(0, len(transport.bodies))

    def test_server_consent_missing_refused(self) -> None:
        transport = FakeTransport([])
        with self.assertRaises(PermissionError):
            self._run(transport, settings=_settings(meta_consent=False))
        self.assertEqual(0, len(transport.bodies))

    def test_missing_key_refused(self) -> None:
        transport = FakeTransport([])
        with self.assertRaises(PermissionError):
            self._run(transport, settings=_settings(meta_api_key=""))
        self.assertEqual(0, len(transport.bodies))

    def test_provider_not_meta_refused(self) -> None:
        transport = FakeTransport([])
        with self.assertRaises(PermissionError):
            self._run(transport, settings=_settings(provider="local"))
        self.assertEqual(0, len(transport.bodies))

    def test_real_root_refused(self) -> None:
        (self.root / DEMO_MARKER).unlink()
        transport = FakeTransport([])
        with self.assertRaises(PermissionError):
            self._run(transport)
        self.assertEqual(0, len(transport.bodies))

    def test_marker_not_synthetic_refused(self) -> None:
        _write_marker(self.root, synthetic=False)
        transport = FakeTransport([])
        with self.assertRaises(PermissionError):
            self._run(transport)
        self.assertEqual(0, len(transport.bodies))

    def test_final_not_json_refused(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                    ("c2", "get_visit_findings", {"link_id": self.link_id}),
                ),
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": "kein JSON"}
                            ],
                        }
                    ],
                },
            ]
        )
        with self.assertRaises(MetaAgentError):
            self._run(transport)

    def test_final_unknown_evidence_refused(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                    ("c2", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final(evidence_ref_ids=["erfunden-1"])),
            ]
        )
        with self.assertRaises(MetaAgentError):
            self._run(transport)

    def test_final_without_tools_refused(self) -> None:
        transport = FakeTransport([_text_payload(self._final())])
        with self.assertRaises(MetaAgentError):
            self._run(transport)

    def test_step_budget_exhausted(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                ),
            ]
        )
        with self.assertRaises(MetaAgentError):
            self._run(transport, max_steps=1)
        self.assertEqual(1, len(transport.bodies))
        self.assertLessEqual(MAX_AGENT_STEPS, 5)

    def test_incomplete_status_refused(self) -> None:
        transport = FakeTransport(
            [
                {
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "output": [],
                }
            ]
        )
        with self.assertRaisesRegex(MetaAgentError, "max_output_tokens"):
            self._run(transport)

    def test_requires_approval_forced_true(self) -> None:
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                    ("c2", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final(requires_approval=False)),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["requires_approval"])

    def test_bad_action_type_refused(self) -> None:
        bad = self._final()
        bad["proposed_actions"] = [
            {
                "type": "mail.send",
                "title": "Senden",
                "target": self.case_id,
                "why": "Verboten.",
            }
        ]
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                    ("c2", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(bad),
            ]
        )
        with self.assertRaises(MetaAgentError):
            self._run(transport)

    def test_foreign_actor_sees_no_link(self) -> None:
        transport = FakeTransport([])
        with self.assertRaisesRegex(ValueError, "case link not found"):
            self._run(transport, actor="cf-fremd")
        self.assertEqual(0, len(transport.bodies))

    def test_empty_actor_rejected(self) -> None:
        transport = FakeTransport([])
        with self.assertRaises(ValueError):
            self._run(transport, actor="  ")
        self.assertEqual(0, len(transport.bodies))

    def test_naive_now_rejected(self) -> None:
        transport = FakeTransport([])
        with self.assertRaises(ValueError):
            self._run(transport, now=datetime(2026, 10, 29, 12, 0))
        self.assertEqual(0, len(transport.bodies))

    def test_revoked_rights_surface_as_tool_error(self) -> None:
        # Simulate rights revoked between the run start check and the tool
        # execution (TOCTOU race): the tool must surface the denial as an
        # error output while the run completes via the remaining tools.
        from app.contextflow.adapters import ContextflowReads

        real_state = ContextflowReads.read_invoice_state
        calls = {"count": 0}

        def flaky_state(self_obj, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                return real_state(self_obj, *args, **kwargs)
            raise PermissionError(
                "invoice context requires contact manage rights"
            )

        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_invoice_state", {"link_id": self.link_id}),
                    ("c2", "get_case_context", {"link_id": self.link_id}),
                    ("c3", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        with mock.patch.object(
            ContextflowReads, "read_invoice_state", flaky_state
        ):
            result = self._run(transport)
        self.assertTrue(result["ok"])
        by_name = {call["name"]: call["ok"] for call in result["tool_calls"]}
        self.assertFalse(by_name["get_invoice_state"])
        self.assertTrue(by_name["get_case_context"])
        self.assertTrue(by_name["get_visit_findings"])

    def test_canary_and_key_never_leave(self) -> None:
        fx.make_todo(self.root, title=f"Privat {CANARY} nicht senden")
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                    ("c2", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        result = self._run(transport)
        self.assertTrue(result["ok"])
        bodies = json.dumps(transport.bodies, ensure_ascii=False)
        self.assertNotIn(CANARY, bodies)
        self.assertNotIn("test-only-dummy-key", bodies)
        self.assertNotIn("Bearer", bodies)

    def test_run_creates_nothing(self) -> None:
        todos_before = len(TodoStore(self.root).items(fx.OWNER))
        drafts_before = len(
            MailCaseStore(self.root).get_case(fx.OWNER, self.case_id)["drafts"]
        )
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": self.link_id}),
                    ("c2", "get_visit_findings", {"link_id": self.link_id}),
                ),
                _text_payload(self._final()),
            ]
        )
        self._run(transport)
        self.assertEqual(todos_before, len(TodoStore(self.root).items(fx.OWNER)))
        self.assertEqual(
            drafts_before,
            len(MailCaseStore(self.root).get_case(fx.OWNER, self.case_id)["drafts"]),
        )

    def test_transport_http_error_maps_honestly(self) -> None:
        sender = _default_transport(
            META_MODEL_API_ENDPOINT, "test-only-dummy-key"
        )
        failure = urllib.error.HTTPError(
            META_MODEL_API_ENDPOINT, 400, "Bad Request", {}, None
        )
        with mock.patch(
            "urllib.request.build_opener", side_effect=failure
        ):
            with self.assertRaisesRegex(MetaAgentError, r"HTTP 400"):
                sender({"model": META_MODEL_API_MODEL})

    def test_transport_timeout_maps_honestly(self) -> None:
        sender = _default_transport(
            META_MODEL_API_ENDPOINT, "test-only-dummy-key"
        )
        with mock.patch(
            "urllib.request.build_opener", side_effect=TimeoutError()
        ):
            with self.assertRaisesRegex(MetaAgentError, "Zeitlimit"):
                sender({"model": META_MODEL_API_MODEL})

    def test_agent_run_throttle_limits_per_actor(self) -> None:
        reset_agent_run_limits()
        try:
            for _ in range(AGENT_RUN_LIMIT):
                state, _ = reserve_agent_run("throttle-actor")
                self.assertEqual("ok", state)
                release_agent_run("throttle-actor")
            state, retry_after = reserve_agent_run("throttle-actor")
            self.assertEqual("limited", state)
            assert retry_after is not None
            self.assertGreater(retry_after, 0)
            other, _ = reserve_agent_run("other-actor")
            self.assertEqual("ok", other)
            release_agent_run("other-actor")
        finally:
            reset_agent_run_limits()

    def test_agent_run_single_flight_busy(self) -> None:
        reset_agent_run_limits()
        try:
            state, _ = reserve_agent_run("busy-actor")
            self.assertEqual("ok", state)
            state, _ = reserve_agent_run("busy-actor")
            self.assertEqual("busy", state)
            release_agent_run("busy-actor")
            state, _ = reserve_agent_run("busy-actor")
            self.assertEqual("ok", state)
            release_agent_run("busy-actor")
        finally:
            reset_agent_run_limits()

    def test_agent_run_window_expires(self) -> None:
        reset_agent_run_limits()
        stamps = [1000.0] * (AGENT_RUN_LIMIT + 1) + [
            1000.0 + AGENT_RUN_WINDOW_SECONDS + 1.0
        ]
        try:
            with mock.patch("time.monotonic", side_effect=stamps):
                for _ in range(AGENT_RUN_LIMIT):
                    state, _ = reserve_agent_run("window-actor")
                    self.assertEqual("ok", state)
                    release_agent_run("window-actor")
                state, _ = reserve_agent_run("window-actor")
                self.assertEqual("limited", state)
                state, _ = reserve_agent_run("window-actor")
                self.assertEqual("ok", state)
                release_agent_run("window-actor")
        finally:
            reset_agent_run_limits()

    def test_demo_seed_contains_contradiction(self) -> None:
        seeded = seed_demo(self.root)
        record = SiteVisitStore(self.root).get(
            str(seeded["visit_id"]), str(seeded["actor"])
        )
        titles = [str(item.get("title", "")) for item in record.get("findings", [])]
        self.assertIn("Papierstau, Ersatzteil offen (synthetisch)", titles)
        self.assertTrue(any("widersprüchlich" in title for title in titles))
        statuses = {str(item.get("status", "")) for item in record.get("findings", [])}
        self.assertEqual({"unreviewed"}, statuses)





if __name__ == "__main__":
    unittest.main()
