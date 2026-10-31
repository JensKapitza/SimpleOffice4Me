"""Unit tests for the T20 live probe helpers (no network, canned fixtures).

The fixtures mirror templates/auth/login.html and
templates/contextflow/agent.html; the live HTTP run itself stays an
owner-side step (needs the demo server, loopback, and MODEL_API_KEY).
"""

from __future__ import annotations

import io
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from tools.t20_live_probe import (
    EXPECTED_MODEL,
    LOGIN_PATH,
    MAX_RESPONSE_BYTES,
    _NoRedirectHandler,
    _read_bounded,
    ProbeError,
    Session,
    check_ml_status,
    check_t20,
    parse_csrf,
    parse_first_link_id,
    parse_pills,
    parse_result,
)

LOGIN_HTML = """<form method="post" class="mt-3">\
<input type="hidden" name="_csrf_token" value="tok-123">\
<input name="username" id="username"><input type="password" name="password">\
</form>"""

AGENT_FORM_HTML = """<p class="cf-pills">
<span class="cf-pill" role="note">Meta Muse Spark 1.3 Contributor (Meta Model API)</span>
<span class="cf-pill cf-pill-quiet">Modell: muse-spark-1.3-contributor</span>
<span class="cf-pill">Serverfreigabe aktiv</span>
<span class="cf-pill">Schlüssel vorhanden</span>
<span class="cf-pill">Synthetischer Demo-Root</span>
</p>
<form method="post" action="/contextflow/agent/run" id="cf-agent-form">
<input type="hidden" name="_csrf_token" value="tok-abc">
<select id="cf-agent-link" name="link_id" required>
<option value="link-1">Fall A · Link 1</option>
<option value="link-2">Fall B · Link 2</option>
</select>
<input type="checkbox" name="consent_meta" value="yes">
</form>"""

RESULT_HTML = """<p class="cf-status">2 Modellschritte · 3 Werkzeugaufrufe · Modell muse-spark-1.3-contributor (Antwort: muse-spark-1.3-contributor) · Antwortmodelle: muse-spark-1.3-contributor, muse-spark-1.3-contributor · Laufzeit: 10.2 s · Tokens Ein/Aus: 1200 / 502</p>
<h3>Werkzeugaufrufe (alle lesend, alle serverseitig ausgeführt)</h3>
<ul class="cf-evidence">
<li><span class="cf-type">get_case_context</span>
<span aria-hidden="true">✔</span><span class="visually-hidden">erfolgreich</span>
<span class="cf-muted">Fall gelesen</span></li>
<li><span class="cf-type">get_visit_findings</span>
<span aria-hidden="true">✔</span><span class="visually-hidden">erfolgreich</span></li>
<li><span class="cf-type">get_invoice_state</span>
<span aria-hidden="true">✘</span><span class="visually-hidden">fehlgeschlagen</span></li>
</ul>
<h3>Feststellungen</h3>
<ul>
<li><strong>Offene Befunde</strong> (medium)<br>Zwei Befunde warten.</li>
</ul>
<h3>Beleg-IDs (alle in diesem Lauf gelesen)</h3>
<ul class="cf-evidence">
<li><span class="cf-type">Fall ab12cd34</span>
<a href="/mail/1">case-1</a></li>
<li><span class="cf-type">Befund ef56</span>
ef56</li>
</ul>
<p class="cf-invoice"><strong>Nur ein Vorschlag – nichts wurde erstellt oder versendet.</strong></p>"""

ERROR_HTML = """<h2 tabindex="-1" autofocus>Analyse nicht möglich</h2>
<p class="cf-reason">Meta-Demo-Analyse braucht die Einwilligung</p>"""


def _status(**overrides) -> dict:
    payload = {
        "provider": "meta",
        "meta": {
            "model": EXPECTED_MODEL,
            "key_present": True,
            "server_consent": True,
            "model_listed": True,
            "models_error": "",
        },
    }
    payload.update(overrides)
    return payload


class ParseHelpersTest(unittest.TestCase):
    def test_csrf_from_login(self) -> None:
        self.assertEqual("tok-123", parse_csrf(LOGIN_HTML))

    def test_csrf_missing_raises(self) -> None:
        with self.assertRaises(ProbeError):
            parse_csrf("<form></form>")

    def test_first_link_id(self) -> None:
        self.assertEqual("link-1", parse_first_link_id(AGENT_FORM_HTML))

    def test_no_confirmed_case_raises(self) -> None:
        with self.assertRaises(ProbeError):
            parse_first_link_id("<p>Noch kein bestätigter Fall vorhanden.</p>")

    def test_pills_all_ready(self) -> None:
        self.assertEqual(
            {
                "meta_ready": True,
                "model_pinned": True,
                "server_consent": True,
                "key_present": True,
                "demo_root": True,
            },
            parse_pills(AGENT_FORM_HTML),
        )

    def test_pills_detect_unready(self) -> None:
        pills = parse_pills("<span>Meta-Demo nicht aktiviert (Anbieter: mock)</span>")
        self.assertFalse(pills["meta_ready"])
        self.assertFalse(pills["model_pinned"])
        self.assertFalse(pills["server_consent"])
        self.assertFalse(pills["key_present"])
        self.assertFalse(pills["demo_root"])


class ParseResultTest(unittest.TestCase):
    def test_full_result(self) -> None:
        parsed = parse_result(RESULT_HTML)
        self.assertEqual(2, parsed["steps"])
        self.assertEqual(3, parsed["tool_calls"])
        self.assertEqual(EXPECTED_MODEL, parsed["model"])
        self.assertEqual(EXPECTED_MODEL, parsed["response_model"])
        self.assertEqual([EXPECTED_MODEL, EXPECTED_MODEL], parsed["response_models"])
        self.assertAlmostEqual(10.2, parsed["run_seconds"])
        self.assertEqual(1200, parsed["tokens_in"])
        self.assertEqual(502, parsed["tokens_out"])
        self.assertEqual(2, parsed["ok_tools"])
        self.assertEqual(
            ["get_case_context", "get_visit_findings", "get_invoice_state"],
            [tool["name"] for tool in parsed["tools"]],
        )
        self.assertEqual(1, parsed["findings"])
        self.assertEqual(2, parsed["evidence"])
        self.assertTrue(parsed["proposal_note"])

    def test_error_page_raises_with_reason(self) -> None:
        with self.assertRaises(ProbeError) as raised:
            parse_result(ERROR_HTML)
        self.assertIn("Einwilligung", str(raised.exception))

    def test_missing_status_raises(self) -> None:
        with self.assertRaises(ProbeError):
            parse_result("<p>nothing here</p>")

    def test_legacy_status_without_response_model(self) -> None:
        legacy = RESULT_HTML.replace(" (Antwort: muse-spark-1.3-contributor)", "")
        legacy = legacy.replace(
            " · Antwortmodelle: muse-spark-1.3-contributor, muse-spark-1.3-contributor",
            "",
        )
        parsed = parse_result(legacy)
        self.assertEqual(EXPECTED_MODEL, parsed["model"])
        self.assertIsNone(parsed["response_model"])
        self.assertEqual([], parsed["response_models"])


class CheckT20Test(unittest.TestCase):
    def test_pass(self) -> None:
        self.assertEqual([], check_t20(parse_result(RESULT_HTML)))

    def test_wrong_model_fails(self) -> None:
        parsed = parse_result(RESULT_HTML)
        parsed["model"] = "other-model"
        failures = check_t20(parsed)
        self.assertTrue(any("model is" in failure for failure in failures))

    def test_missing_response_model_fails(self) -> None:
        parsed = parse_result(RESULT_HTML)
        parsed["response_model"] = None
        self.assertTrue(any("response model" in failure for failure in check_t20(parsed)))

    def test_missing_per_step_response_models_fails(self) -> None:
        parsed = parse_result(RESULT_HTML)
        parsed["response_models"] = []
        self.assertTrue(any("per-step response model" in failure for failure in check_t20(parsed)))

    def test_diverged_response_model_fails(self) -> None:
        parsed = parse_result(RESULT_HTML)
        parsed["response_model"] = "other-model"
        self.assertTrue(any("response model" in failure for failure in check_t20(parsed)))

    def test_divergent_per_step_response_models_fail(self) -> None:
        parsed = parse_result(RESULT_HTML)
        parsed["response_models"] = [EXPECTED_MODEL, "other-model"]
        self.assertTrue(any("per-step response models" in failure for failure in check_t20(parsed)))

    def test_single_tool_fails(self) -> None:
        parsed = parse_result(RESULT_HTML)
        parsed["ok_tools"] = 1
        self.assertTrue(any("tool calls" in failure for failure in check_t20(parsed)))

    def test_no_evidence_fails(self) -> None:
        parsed = parse_result(RESULT_HTML)
        parsed["evidence"] = 0
        self.assertTrue(any("evidence" in failure for failure in check_t20(parsed)))

    def test_missing_tokens_fails(self) -> None:
        parsed = parse_result(RESULT_HTML)
        parsed["tokens_out"] = None
        self.assertTrue(any("token" in failure for failure in check_t20(parsed)))

    def test_missing_proposal_note_fails(self) -> None:
        parsed = parse_result(RESULT_HTML)
        parsed["proposal_note"] = False
        self.assertTrue(any("proposal" in failure for failure in check_t20(parsed)))


class MlStatusCheckTest(unittest.TestCase):
    def test_pass(self) -> None:
        self.assertEqual([], check_ml_status(_status()))

    def test_wrong_provider_fails(self) -> None:
        failures = check_ml_status(_status(provider="mock"))
        self.assertTrue(any("provider" in failure for failure in failures))

    def test_unlisted_model_fails(self) -> None:
        meta = dict(_status()["meta"], model_listed=False, models_error="403")
        failures = check_ml_status(_status(meta=meta))
        self.assertTrue(any("listed" in failure for failure in failures))

    def test_missing_key_fails(self) -> None:
        meta = dict(_status()["meta"], key_present=False)
        self.assertTrue(check_ml_status(_status(meta=meta)))


class SessionGuardTest(unittest.TestCase):
    def test_non_loopback_refused(self) -> None:
        with self.assertRaises(ProbeError):
            Session("https://example.com")


    def test_only_credential_free_http_loopback_urls_are_accepted(self) -> None:
        invalid = (
            "file://localhost/tmp/demo",
            "ftp://localhost/demo",
            "http://demo:secret@localhost:5101",
            "http://localhost:5101/?token=secret",
            "http://localhost:5101/#fragment",
            "http://localhost:5101/prefix",
            "http://localhost:5101/../outside",
        )
        for base in invalid:
            with self.subTest(base=base), self.assertRaises(ProbeError) as raised:
                Session(base)
            self.assertNotIn("secret", str(raised.exception))

    def test_probe_never_follows_redirects(self) -> None:
        request = urllib.request.Request("http://127.0.0.1:5101/login")
        handler = _NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://example.com/collect",
            ),
        )
        session = Session("http://127.0.0.1:5101")
        redirect_handlers = [
            handler for handler in session.opener.handlers
            if isinstance(handler, urllib.request.HTTPRedirectHandler)
        ]
        self.assertEqual([_NoRedirectHandler], [type(item) for item in redirect_handlers])

    def test_response_body_limit_is_enforced(self) -> None:
        self.assertEqual(
            MAX_RESPONSE_BYTES,
            len(_read_bounded(io.BytesIO(b"x" * MAX_RESPONSE_BYTES))),
        )
        with self.assertRaisesRegex(ProbeError, "2 MiB"):
            _read_bounded(io.BytesIO(b"x" * (MAX_RESPONSE_BYTES + 1)))

    def test_response_limit_survives_short_reads(self) -> None:
        class ShortReadStream(io.BytesIO):
            def read(self, size: int = -1) -> bytes:
                return super().read(min(size, 3))

        with patch("tools.t20_live_probe.MAX_RESPONSE_BYTES", 4):
            with self.assertRaisesRegex(ProbeError, "2 MiB"):
                _read_bounded(ShortReadStream(b"123456"))

    def test_wrapped_timeout_has_a_safe_diagnostic(self) -> None:
        session = Session("http://127.0.0.1:5101")
        with patch.object(session.opener, "open", side_effect=urllib.error.URLError(TimeoutError())):
            with self.assertRaisesRegex(ProbeError, "timed out"):
                session.get("/login")


class ProbePathsTest(unittest.TestCase):
    def test_login_path_matches_auth_blueprint(self) -> None:
        from app.auth import bp

        self.assertEqual(f"{bp.url_prefix}/login", LOGIN_PATH)


if __name__ == "__main__":
    unittest.main()
