"""ContextFlow T19: private canary in a local root never reaches Meta.

update2.md §10/T19: a private marker planted in tool-readable fields
of a real (non-demo) local root must appear in no Meta request
payload and in no log output. Complements the demo-root canary test
in ``tests.test_contextflow_meta_agent``: here the *gates* must refuse
before any transport call happens. Tripwire tests prove the harness
itself would detect a leak. No live Meta calls: scripted transports
and mocks only.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from app.contextflow.contact_suggest import suggest_contact_fields
from app.contextflow.meta_agent import reset_agent_run_limits, run_agent
from app.contextflow.vision import (
    META_MODEL_API_ENDPOINT,
    META_MODEL_API_MODEL,
    MetaVisionProvider,
    check_meta_model,
    select_provider,
)
from app.site_visit_store import SiteVisitStore

from tests import contextflow_fixtures as fx
from tests.test_contextflow_meta_agent import (
    NOW,
    FakeTransport,
    _call_payload,
    _text_payload,
    _write_marker,
)

T19_CANARY = "T19-CANARY-PRIVAT-NIEMALS-SENDEN"
DUMMY_KEY = "test-only-dummy-key"


def _meta_settings(**overrides):
    params = {
        "provider": "meta",
        "meta_consent": True,
        "meta_api_key": DUMMY_KEY,
        "meta_endpoint": META_MODEL_API_ENDPOINT,
        "meta_model": META_MODEL_API_MODEL,
    }
    params.update(overrides)
    return params


def _dump_bodies(bodies) -> str:
    return json.dumps(bodies, ensure_ascii=False, default=str)


class _SeededRoot:
    """Local root with the canary in tool-readable fields (unless clean)."""

    def __init__(self, testcase, *, actor=fx.OWNER, marker=False, taint=True):
        testcase.temp = tempfile.TemporaryDirectory()
        testcase.addCleanup(testcase.temp.cleanup)
        self.root = Path(testcase.temp.name) / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        if marker:
            _write_marker(self.root)
        tag = f" {T19_CANARY}" if taint else ""
        self.contact_id = str(
            fx.make_contact(
                self.root, actor, display_name=f"Betrieb{tag} GmbH"
            )["contact_id"]
        )
        self.case_id = fx.make_mail_case(
            self.root, actor, title=f"Drucker stoert{tag}"
        )
        self.visit_id = fx.make_visit(
            self.root,
            actor,
            completed=True,
            finding_title=f"Befund{tag} offen",
        )
        fx.make_event(
            self.root, actor, title=f"Rueckfrage{tag}",
            contact_id=self.contact_id,
        )
        fx.make_invoice_row(
            self.root, self.contact_id, "CF-INV-T19",
            invoice_number=f"RE{tag}",
        )
        link = fx.confirm_link(
            self.root,
            actor,
            case_id=self.case_id,
            contact_id=self.contact_id,
            visit_id=self.visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            invoice_id="CF-INV-T19",
            now=NOW,
        )
        self.link_id = str(link["link_id"])


class AgentRealRootCanaryTests(unittest.TestCase):
    """run_agent must refuse real roots before any transport call."""

    def setUp(self) -> None:
        reset_agent_run_limits()

    def test_real_root_refuses_before_transport(self) -> None:
        seed = _SeededRoot(self)
        transport = FakeTransport([])
        with self.assertRaisesRegex(PermissionError, "Demo-Root"):
            run_agent(
                actor=fx.OWNER,
                link_id=seed.link_id,
                consent_meta=True,
                settings=_meta_settings(),
                root=seed.root,
                now=NOW,
                transport=transport,
            )
        self.assertEqual([], transport.bodies)

    def test_consent_gate_precedes_everything(self) -> None:
        seed = _SeededRoot(self)
        transport = FakeTransport([])
        with self.assertRaisesRegex(PermissionError, "Einwilligung"):
            run_agent(
                actor=fx.OWNER,
                link_id=seed.link_id,
                consent_meta=False,
                settings=_meta_settings(),
                root=seed.root,
                now=NOW,
                transport=transport,
            )
        self.assertEqual([], transport.bodies)

    def test_demo_root_clean_flow_proves_capture(self) -> None:
        seed = _SeededRoot(self, marker=True, taint=False)
        fx.make_todo(seed.root, title=f"Unbeteiligte Notiz {T19_CANARY}")
        findings = SiteVisitStore(seed.root).get(
            seed.visit_id, fx.OWNER
        ).get("findings", [])
        self.assertTrue(findings)
        transport = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_case_context", {"link_id": seed.link_id}),
                    ("c2", "get_visit_findings", {"link_id": seed.link_id}),
                ),
                _text_payload(self._final(seed)),
            ]
        )
        result = run_agent(
            actor=fx.OWNER,
            link_id=seed.link_id,
            consent_meta=True,
            settings=_meta_settings(),
            root=seed.root,
            now=NOW,
            transport=transport,
        )
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(len(transport.bodies), 1)
        bodies = _dump_bodies(transport.bodies)
        self.assertNotIn(T19_CANARY, bodies)
        self.assertNotIn(DUMMY_KEY, bodies)

    def _final(self, seed) -> dict:
        findings = SiteVisitStore(seed.root).get(
            seed.visit_id, fx.OWNER
        ).get("findings", [])
        finding_id = str(findings[0].get("finding_id", ""))
        self.assertTrue(finding_id)
        return {
            "summary": "Nacharbeit offen (synthetisch).",
            "findings": [
                {
                    "title": "Offene Befunde",
                    "detail": "Ein Befund wartet.",
                    "severity": "low",
                }
            ],
            "evidence_ref_ids": [seed.case_id, finding_id],
            "unknowns": [],
            "proposed_actions": [
                {
                    "type": "task.create",
                    "title": "Befund nachtragen",
                    "target": finding_id,
                    "why": "Nacharbeit liegt vor.",
                }
            ],
            "requires_approval": True,
        }

    def test_tripwire_detector_flags_canary(self) -> None:
        with self.assertRaises(AssertionError):
            self.assertNotIn(T19_CANARY, _dump_bodies({"x": T19_CANARY}))


class ContactSuggestCanaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = str(Path(self.temp.name) / "documents")

    def test_meta_on_real_root_refused_without_call(self) -> None:
        with mock.patch.object(
            MetaVisionProvider, "_post_response"
        ) as posted:
            with self.assertRaises(PermissionError):
                suggest_contact_fields(
                    text=f"Ruf Mueller an {T19_CANARY}",
                    photos=[],
                    settings=_meta_settings(),
                    consent_meta=True,
                    root=self.root,
                )
        posted.assert_not_called()

    def test_complete_text_would_carry_tainted_prompt(self) -> None:
        provider = MetaVisionProvider(
            DUMMY_KEY, META_MODEL_API_ENDPOINT, META_MODEL_API_MODEL
        )
        with mock.patch.object(
            MetaVisionProvider, "_post_response", return_value={}
        ) as posted:
            provider.complete_text(f"Formuliere um: {T19_CANARY}")
        recorded = _dump_bodies(posted.call_args[0][0])
        self.assertIn(T19_CANARY, recorded)

    def test_complete_text_clean_prompt_no_canary(self) -> None:
        provider = MetaVisionProvider(
            DUMMY_KEY, META_MODEL_API_ENDPOINT, META_MODEL_API_MODEL
        )
        with mock.patch.object(
            MetaVisionProvider, "_post_response", return_value={}
        ) as posted:
            provider.complete_text("Max Muster, Beispielweg 1")
        self.assertEqual(1, posted.call_count)
        self.assertNotIn(
            T19_CANARY, _dump_bodies(posted.call_args[0][0])
        )


class VisionGateCanaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = str(Path(self.temp.name) / "documents")

    def test_select_provider_meta_real_root_refused(self) -> None:
        with self.assertRaisesRegex(PermissionError, "synthetic demo root"):
            select_provider(_meta_settings(), True, self.root)

    def test_check_meta_model_sends_no_body(self) -> None:
        requests = []

        class _Canned:
            def read(self):
                return json.dumps(
                    {"data": [{"id": META_MODEL_API_MODEL}]}
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Opener:
            def open(self, request, timeout=None):
                requests.append(request)
                return _Canned()

        with mock.patch(
            "urllib.request.build_opener", return_value=_Opener()
        ):
            listed, error = check_meta_model(DUMMY_KEY)
        self.assertTrue(listed)
        self.assertEqual("", error)
        self.assertEqual(1, len(requests))
        self.assertIsNone(requests[0].data)
        envelope = _dump_bodies(
            {
                "url": requests[0].full_url,
                "headers": dict(requests[0].header_items()),
            }
        )
        self.assertNotIn(T19_CANARY, envelope)


class AgentRouteCanaryTests(unittest.TestCase):
    """Agent route on a real root: 403, canary in no log record."""

    password = "contextflow-canary-test-password"

    def setUp(self) -> None:
        from app import app as flask_app
        from app.db import ensure_auth_database, get_db
        from app.password_security import hash_password

        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: flask_app.config.get(key)
            for key in (
                "DATABASE",
                "DOCUMENT_ROOT",
                "TESTING",
                "TEST_CSRF_PROTECTION",
                "CONTEXTFLOW_ENABLED",
                "CONTEXTFLOW_VISION_PROVIDER",
                "CONTEXTFLOW_META_CONSENT",
            )
        }
        flask_app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
            CONTEXTFLOW_VISION_PROVIDER="meta",
            CONTEXTFLOW_META_CONSENT=True,
        )
        with flask_app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        self.admin = self.login("admin")
        contact_id = str(
            fx.make_contact(
                self.root, "admin",
                display_name=f"Betrieb {T19_CANARY} GmbH",
            )["contact_id"]
        )
        case_id = fx.make_mail_case(
            self.root, "admin", title=f"Drucker stoert {T19_CANARY}"
        )
        visit_id = fx.make_visit(
            self.root, "admin", completed=True,
            finding_title=f"Befund {T19_CANARY} offen",
        )
        link = fx.confirm_link(
            self.root,
            "admin",
            case_id=case_id,
            contact_id=contact_id,
            visit_id=visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        self.link_id = str(link["link_id"])
        self._env_patch = mock.patch.dict(
            os.environ, {"MODEL_API_KEY": DUMMY_KEY}
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def tearDown(self) -> None:
        from app import app as flask_app

        flask_app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        from app import app as flask_app

        client = flask_app.test_client()
        page = client.get("/auth/login")
        match = re.search(
            rb'name="_csrf_token" value="([^"]+)"', page.data
        )
        assert match is not None
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": match.group(1).decode(),
            },
        )
        return client

    def csrf(self) -> str:
        response = self.admin.get("/contextflow/agent")
        match = re.search(
            rb'name="_csrf_token" value="([^"]+)"', response.data
        )
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def test_run_on_real_root_403_without_canary_in_logs(self) -> None:
        with mock.patch(
            "app.contextflow.meta_agent._default_transport"
        ) as transport_factory:
            with self.assertLogs(level="DEBUG") as captured:
                response = self.admin.post(
                    "/contextflow/agent/run",
                    data={
                        "link_id": self.link_id,
                        "consent_meta": "yes",
                        "_csrf_token": self.csrf(),
                    },
                )
        self.assertEqual(403, response.status_code)
        transport_factory.assert_not_called()
        records = "\n".join(captured.output)
        self.assertNotIn(T19_CANARY, records)


if __name__ == "__main__":
    unittest.main()
