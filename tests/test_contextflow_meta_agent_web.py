"""ContextFlow P0-G: Meta agent web routes and demo flow.

Companion to ``tests.test_contextflow_meta_agent`` (loop contract);
shared fakes live there and are imported, not duplicated. Run with the
project venv (``.venv``) and TEMP/TMP pointing to a short local path.
No live Meta calls: scripted transports and mocks only.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from app.contextflow.meta_agent import (
    AGENT_RUN_LIMIT,
    MetaAgentError,
    release_agent_run,
    reserve_agent_run,
    reset_agent_run_limits,
)
from app.contextflow.vision import META_MODEL_API_MODEL
from app.mail_case_store import MailCaseStore
from app.site_visit_store import SiteVisitStore

from tests import contextflow_fixtures as fx
from tests.test_contextflow_meta_agent import (
    NOW,
    FakeTransport,
    _call_payload,
    _text_payload,
    _write_marker,
)


class MetaAgentWebTest(unittest.TestCase):
    password = "contextflow-meta-agent-test-password"

    def setUp(self) -> None:
        from app import app as flask_app
        from app.db import ensure_auth_database, get_db
        from app.password_security import hash_password

        reset_agent_run_limits()
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
            for username, admin in (("admin", 1), ("berta", 0)):
                db.execute(
                    "INSERT INTO user(username,password,display_name,is_admin,"
                    "is_disabled,auth_version,created_at,updated_at)"
                    " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (
                        username,
                        hash_password(self.password),
                        username.title(),
                        admin,
                    ),
                )
            db.commit()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir(parents=True, exist_ok=True)
        _write_marker(self.root)
        self.admin = self.login("admin")
        self.contact_id = str(fx.make_contact(self.root, "admin")["contact_id"])
        self.case_id = fx.make_mail_case(self.root, "admin")
        self.visit_id = fx.make_visit(self.root, "admin", completed=True)
        self.link = fx.confirm_link(
            self.root,
            "admin",
            case_id=self.case_id,
            contact_id=self.contact_id,
            visit_id=self.visit_id,
            completed_on=(NOW - timedelta(days=5)).date().isoformat(),
            now=NOW,
        )
        self.link_id = str(self.link["link_id"])
        self._env_patch = mock.patch.dict(
            os.environ, {"MODEL_API_KEY": "test-only-dummy-key"}
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def tearDown(self) -> None:
        from app import app as flask_app

        flask_app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path="/contextflow/agent") -> str:
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data[:300])
        assert match is not None
        return match.group(1).decode()

    def login(self, username: str):
        from app import app as flask_app

        client = flask_app.test_client()
        response = client.get("/auth/login")
        match = re.search(
            rb'name="_csrf_token" value="([^"]+)"', response.data
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

    def _result(self) -> dict:
        return {
            "ok": True,
            "provider": "meta",
            "model": META_MODEL_API_MODEL,
            "response_model": META_MODEL_API_MODEL,
            "response_models": [META_MODEL_API_MODEL, META_MODEL_API_MODEL],
            "response_models_display": f"{META_MODEL_API_MODEL}, {META_MODEL_API_MODEL}",
            "link_id": self.link_id,
            "steps": 2,
            "tool_calls": [
                {"name": "get_case_context", "call_id": "c1", "ok": True},
                {"name": "get_visit_findings", "call_id": "c2", "ok": True},
            ],
            "summary": "Zwei Befunde offen (synthetisch).",
            "findings": [
                {
                    "title": "Offene Befunde",
                    "detail": "Nacharbeit liegt vor.",
                    "severity": "medium",
                }
            ],
            "evidence_ref_ids": [self.link_id],
            "unknowns": ["Ersatzteilnummer unbekannt."],
            "proposed_actions": [
                {
                    "type": "task.create",
                    "title": "Befunde nachtragen",
                    "target": self.link_id,
                    "why": "Fällige Nacharbeit.",
                }
            ],
            "requires_approval": True,
            "usage": {"input_tokens": 2152, "output_tokens": 784},
        }

    def test_agent_form_shows_consent_and_model(self) -> None:
        response = self.admin.get("/contextflow/agent")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn(META_MODEL_API_MODEL, body)
        self.assertIn("Meta Muse Spark", body)
        self.assertIn(self.link_id[:8], body)
        self.assertIn('name="consent_meta" value="yes"', body)
        self.assertNotIn('name="consent_meta" value="yes" checked', body)
        self.assertIn("Contributor", body)
        self.assertIn("Serverfreigabe aktiv", body)
        self.assertIn('class="cf-checkbox"', body)
        title = MailCaseStore(self.root).list_cases("admin")[0]["title"]
        self.assertTrue(title)
        self.assertIn(title, body)
        self.assertLess(
            body.index("contextflow.css"), body.index("</head>")
        )

    def test_run_without_consent_forbidden(self) -> None:
        response = self.admin.post(
            "/contextflow/agent/run",
            data={
                "link_id": self.link_id,
                "_csrf_token": self.csrf(self.admin),
            },
        )
        self.assertEqual(403, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Einwilligung", body)
        self.assertIn(f'value="{self.link_id}" selected', body)
        self.assertIn("<h2 tabindex=\"-1\" autofocus>", body)

    def test_agent_form_preselects_link(self) -> None:
        response = self.admin.get(f"/contextflow/agent?link_id={self.link_id}")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn(f'value="{self.link_id}" selected', body)

    def test_agent_form_provider_local_is_honest(self) -> None:
        from app import app as flask_app

        flask_app.config.update(CONTEXTFLOW_VISION_PROVIDER="local")
        try:
            body = self.admin.get("/contextflow/agent").get_data(as_text=True)
        finally:
            flask_app.config.update(CONTEXTFLOW_VISION_PROVIDER="meta")
        self.assertIn("nicht aktiviert", body)
        self.assertIn("(Anbieter: local)", body)

    def test_run_provider_not_meta_forbidden(self) -> None:
        from app import app as flask_app

        # No mock: the real provider gate inside run_agent must refuse
        # before any transport exists. The loop suite proves separately
        # that no request is recorded on this path.
        flask_app.config.update(CONTEXTFLOW_VISION_PROVIDER="local")
        try:
            response = self.admin.post(
                "/contextflow/agent/run",
                data={
                    "link_id": self.link_id,
                    "consent_meta": "yes",
                    "_csrf_token": self.csrf(self.admin),
                },
            )
        finally:
            flask_app.config.update(CONTEXTFLOW_VISION_PROVIDER="meta")
        self.assertEqual(403, response.status_code)

    def test_run_happy_renders_proposal(self) -> None:
        with mock.patch(
            "app.contextflow.meta_agent.run_agent", return_value=self._result()
        ) as run_mock:
            response = self.admin.post(
                "/contextflow/agent/run",
                data={
                    "link_id": self.link_id,
                    "consent_meta": "yes",
                    "_csrf_token": self.csrf(self.admin),
                },
            )
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Offene Befunde", body)
        self.assertIn("get_case_context", body)
        self.assertIn("get_visit_findings", body)
        self.assertIn(self.link_id, body)
        self.assertIn("Nur ein Vorschlag", body)
        self.assertIn("/tasks/", body)
        self.assertIn("/documents/mail/reader", body)
        self.assertIn(
            f"/documents/business/contacts/{self.contact_id}/invoice", body
        )
        self.assertIn("Rechnungseditor öffnen", body)
        title = MailCaseStore(self.root).list_cases("admin")[0]["title"]
        self.assertIn(title, body)
        self.assertIn(f"Fall-ID: {self.case_id}", body)
        self.assertIn("Laufzeit:", body)
        self.assertIn(
            f"Antwortmodelle: {META_MODEL_API_MODEL}, {META_MODEL_API_MODEL}",
            body,
        )
        self.assertIn("Tokens Ein/Aus: 2152 / 784", body)
        self.assertIn('aria-hidden="true"', body)
        self.assertIn("visually-hidden", body)
        self.assertIn("erfolgreich", body)
        self.assertIn("<h2 tabindex=\"-1\" autofocus>", body)
        _, kwargs = run_mock.call_args
        self.assertEqual("admin", kwargs["actor"])
        self.assertEqual(self.link_id, kwargs["link_id"])
        self.assertIs(True, kwargs["consent_meta"])

    def test_run_result_hides_editor_without_manage(self) -> None:
        with (
            mock.patch(
                "app.contextflow.meta_agent.run_agent",
                return_value=self._result(),
            ),
            mock.patch(
                "app.contextflow.web.ContactStore.can_manage",
                return_value=False,
            ),
        ):
            response = self.admin.post(
                "/contextflow/agent/run",
                data={
                    "link_id": self.link_id,
                    "consent_meta": "yes",
                    "_csrf_token": self.csrf(self.admin),
                },
            )
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Mail-Vorgang öffnen", body)
        self.assertIn("Aufgaben-Board öffnen", body)
        self.assertNotIn("Rechnungseditor öffnen", body)
        self.assertNotIn("/documents/business/contacts/", body)

    def test_run_result_hides_follow_links_for_foreign_actor(self) -> None:
        berta = self.login("berta")
        with mock.patch(
            "app.contextflow.meta_agent.run_agent",
            return_value=self._result(),
        ):
            response = berta.post(
                "/contextflow/agent/run",
                data={
                    "link_id": self.link_id,
                    "consent_meta": "yes",
                    "_csrf_token": self.csrf(berta),
                },
            )
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertNotIn("Mail-Vorgang öffnen", body)
        self.assertNotIn("Aufgaben-Board öffnen", body)
        self.assertNotIn("Rechnungseditor öffnen", body)

    def test_run_limited_answers_429(self) -> None:
        for _ in range(AGENT_RUN_LIMIT):
            state, _ = reserve_agent_run("admin")
            self.assertEqual("ok", state)
            release_agent_run("admin")
        with mock.patch(
            "app.contextflow.meta_agent.run_agent"
        ) as run_mock:
            response = self.admin.post(
                "/contextflow/agent/run",
                data={
                    "link_id": self.link_id,
                    "consent_meta": "yes",
                    "_csrf_token": self.csrf(self.admin),
                },
            )
        self.assertEqual(429, response.status_code)
        self.assertIn("Retry-After", response.headers)
        self.assertIn("Zu viele Analysen", response.get_data(as_text=True))
        run_mock.assert_not_called()

    def test_run_busy_answers_409(self) -> None:
        state, _ = reserve_agent_run("admin")
        self.assertEqual("ok", state)
        try:
            with mock.patch(
                "app.contextflow.meta_agent.run_agent"
            ) as run_mock:
                response = self.admin.post(
                    "/contextflow/agent/run",
                    data={
                        "link_id": self.link_id,
                        "consent_meta": "yes",
                        "_csrf_token": self.csrf(self.admin),
                    },
                )
        finally:
            release_agent_run("admin")
        self.assertEqual(409, response.status_code)
        self.assertIn("bereits eine Analyse", response.get_data(as_text=True))
        run_mock.assert_not_called()

    def test_run_without_csrf_rejected(self) -> None:
        with mock.patch(
            "app.contextflow.meta_agent.run_agent"
        ) as run_mock:
            response = self.admin.post(
                "/contextflow/agent/run",
                data={"link_id": self.link_id, "consent_meta": "yes"},
            )
        self.assertIn(response.status_code, (400, 403))
        run_mock.assert_not_called()

    def test_agent_flag_off_404s(self) -> None:
        from app import app as flask_app

        # CSRF runs before the view: capture a valid token first so the
        # POST reaches the disabled flag instead of the token check.
        token = self.csrf(self.admin)
        flask_app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            self.assertEqual(
                404, self.admin.get("/contextflow/agent").status_code
            )
            response = self.admin.post(
                "/contextflow/agent/run",
                data={
                    "link_id": self.link_id,
                    "consent_meta": "yes",
                    "_csrf_token": token,
                },
            )
            self.assertEqual(404, response.status_code)
        finally:
            flask_app.config.update(CONTEXTFLOW_ENABLED=True)

    def test_agent_key_error_maps_controlled_500(self) -> None:
        with mock.patch(
            "app.contextflow.meta_agent.run_agent",
            side_effect=KeyError("internal-store-key"),
        ):
            response = self.admin.post(
                "/contextflow/agent/run",
                data={
                    "link_id": self.link_id,
                    "consent_meta": "yes",
                    "_csrf_token": self.csrf(self.admin),
                },
            )
        self.assertEqual(500, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("Analyse gestört", body)
        self.assertNotIn("internal-store-key", body)

    def test_agent_meta_error_maps_502(self) -> None:
        with mock.patch(
            "app.contextflow.meta_agent.run_agent",
            side_effect=MetaAgentError("Meta-Aufruf ist nicht erreichbar"),
        ):
            response = self.admin.post(
                "/contextflow/agent/run",
                data={
                    "link_id": self.link_id,
                    "consent_meta": "yes",
                    "_csrf_token": self.csrf(self.admin),
                },
            )
        self.assertEqual(502, response.status_code)
        self.assertIn(
            "nicht erreichbar", response.get_data(as_text=True)
        )

    def test_run_e2e_scripted_transport(self) -> None:
        # Full HTTP stack with a scripted model: route, gates, real
        # tool loop on the web fixtures, and the rendered proposal.
        visit = SiteVisitStore(self.root)
        link_visit = self.visit_id
        findings = visit.get(str(link_visit), "admin").get("findings", [])
        finding_id = str(findings[0]["finding_id"])
        proposal = {
            "summary": "E2E-Befundlage (synthetisch).",
            "findings": [
                {
                    "title": "E2E-Fund",
                    "detail": "Durch den Stapel belegt.",
                    "severity": "low",
                }
            ],
            "evidence_ref_ids": [finding_id, self.case_id],
            "unknowns": [],
            "proposed_actions": [
                {
                    "type": "visit.open",
                    "title": "Ortstermin prüfen",
                    "target": str(link_visit),
                    "why": "E2E-Nachweis.",
                }
            ],
            "requires_approval": True,
        }
        script = FakeTransport(
            [
                _call_payload(
                    ("c1", "get_visit_findings", {"link_id": self.link_id}),
                    ("c2", "get_case_context", {"link_id": self.link_id}),
                ),
                _text_payload(proposal),
            ]
        )
        with mock.patch(
            "app.contextflow.meta_agent._default_transport",
            return_value=script,
        ):
            response = self.admin.post(
                "/contextflow/agent/run",
                data={
                    "link_id": self.link_id,
                    "consent_meta": "yes",
                    "_csrf_token": self.csrf(self.admin),
                },
            )
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn("E2E-Fund", body)
        self.assertIn("get_visit_findings", body)
        self.assertIn(finding_id, body)
        self.assertIn("1 Befund (Filter: unreviewed)", body)
        self.assertIn(f"/site-visits/{self.visit_id}#findings", body)
        self.assertIn(f"/documents/mail/reader?case={self.case_id}", body)
        self.assertEqual(2, len(script.bodies))

    def test_agent_form_keeps_global_submit_guard(self) -> None:
        # The per-page script is gone: the document-wide guard in
        # static/js/global_ui.js is the only double-submit protection.
        # The form must stay enrolled (real submit control, no
        # opt-out) and the guard script must load on this page.
        body = self.admin.get("/contextflow/agent").get_data(as_text=True)
        self.assertIn("js/global_ui.js", body)
        match = re.search(
            r'<form[^>]*id="cf-agent-form"[^>]*>(.*?)</form>',
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        form_tag = body[match.start() : body.index(">", match.start()) + 1]
        self.assertNotIn("data-allow-multiple-submit", form_tag)
        self.assertIn('type="submit"', match.group(1))


if __name__ == "__main__":
    unittest.main()
