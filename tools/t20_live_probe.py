"""T20 live probe: exactly one Meta tool-loop run against a local demo server.

Target: update2.md T20 (Muse Spark Sandbox/Tool-Loop) — real model ID,
2+ read tools, validated evidence. The probe drives the DEPLOYED demo
(no mocks, no bypasses): login, ``GET /contextflow/ml-status`` preflight,
``GET /contextflow/agent`` form read, then ONE ``POST /contextflow/agent/run``
with explicit consent. It parses the rendered result page, checks the T20
criteria, and writes an evidence JSON file.

Safety: credential-free HTTP(S) loopback bases only
(127.0.0.1/localhost/::1); redirects are not followed and each response
is capped at 2 MiB. The API key, password, and cookies are never printed,
logged, or written to the evidence file. The Meta budget of one probe run
is one free metadata call (``GET /v1/models`` via the ml-status probe)
plus at most five bounded inference calls (server step budget, typically 2-3).

This module has no import side effects; the pure parse/check helpers are
covered by tests/test_t20_live_probe.py with canned HTML fixtures.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import http.client
import http.cookiejar
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

EXPECTED_MODEL = "muse-spark-1.3-contributor"
MIN_OK_TOOLS = 2
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
RUN_TIMEOUT_SECONDS = 420
PAGE_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

_CSRF_RE = re.compile(r'name="_csrf_token"\s+value="([^"]+)"')
_OPTION_RE = re.compile(r'<option\s+value="([^"]+)"')
_STATUS_RE = re.compile(
    r'<p class="cf-status">\s*'
    r"(?P<steps>\d+)\s+Modellschritte\s*·\s*"
    r"(?P<tools>\d+)\s+Werkzeugaufrufe\s*·\s*"
    r"Modell\s+(?P<model>[^·<(]+?)"
    r"(?:\s*\(Antwort:\s*(?P<response>[^)]+?)\))?"
    r"(?:\s*·\s*Laufzeit:\s*(?P<seconds>[\d.]+)\s*s)?"
    r"(?:\s*·\s*Tokens\s+Ein/Aus:\s*(?P<tin>[\d–-]+)\s*/\s*(?P<tout>[\d–-]+))?"
    r"\s*</p>",
    re.DOTALL,
)
_TOOL_ITEM_RE = re.compile(
    r'<li><span class="cf-type">(?P<name>[^<]+)</span>\s*'
    r'<span aria-hidden="true">(?P<mark>✔|✘)</span>',
    re.DOTALL,
)


class ProbeError(RuntimeError):
    """A preflight, transport, or T20-criteria failure (message is safe to show)."""


def _validated_loopback_base(base: str) -> str:
    """Accept only credential-free HTTP(S) URLs on an explicit loopback host."""
    if not isinstance(base, str) or not base or len(base) > 2048:
        raise ProbeError("base must be a valid loopback URL")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in base):
        raise ProbeError("base must be a valid loopback URL")
    try:
        parsed = urllib.parse.urlsplit(base)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise ProbeError("base must be a valid loopback URL") from None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or hostname not in LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise ProbeError("base must be a credential-free HTTP(S) loopback origin")
    return base.rstrip("/")


def _read_bounded(stream) -> bytes:
    """Read a complete response within budget, tolerating short socket reads."""
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_RESPONSE_BYTES:
        chunk = stream.read(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ProbeError("loopback response exceeds the 2 MiB read budget")
    raise ProbeError("loopback response exceeds the 2 MiB read budget")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never let a local demo response redirect the probe to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def parse_csrf(html: str) -> str:
    """Return the first synchronizer token (login and agent forms)."""
    match = _CSRF_RE.search(html)
    if not match:
        raise ProbeError("no _csrf_token found on the page")
    return match.group(1)


def parse_first_link_id(html: str) -> str:
    """Return the first case option of the agent form (no confirmed case = error)."""
    if "Noch kein bestätigter Fall vorhanden." in html:
        raise ProbeError("demo root has no confirmed case (seed_demo did not run?)")
    match = _OPTION_RE.search(html)
    if not match:
        raise ProbeError("no case option found on the agent form")
    return match.group(1)


def parse_pills(html: str) -> dict[str, bool]:
    """Read the honest readiness pills of the agent page."""
    return {
        "meta_ready": "Meta-Demo nicht aktiviert" not in html,
        "model_pinned": f"Modell: {EXPECTED_MODEL}" in html,
        "server_consent": "Serverfreigabe aktiv" in html,
        "key_present": "Schlüssel vorhanden" in html,
        "demo_root": "Synthetischer Demo-Root" in html,
    }


def _section_items(html: str, heading: str) -> list[str]:
    start = html.find(heading)
    if start < 0:
        return []
    segment = html[start : start + 20000]
    end = segment.find("</ul>")
    items = re.findall(r"<li>(.*?)</li>", segment[:end] if end >= 0 else segment, re.DOTALL)
    return [re.sub(r"<[^>]+>", "", item).strip() for item in items]


def parse_result(html: str) -> dict:
    """Parse the rendered agent result (raises ProbeError without a result)."""
    if "Analyse nicht möglich" in html:
        reason = re.search(r'<p class="cf-reason">(.*?)</p>', html, re.DOTALL)
        detail = re.sub(r"<[^>]+>", "", reason.group(1)).strip() if reason else ""
        raise ProbeError(f"agent page reports an error: {detail or 'unknown'}")
    match = _STATUS_RE.search(html)
    if not match:
        raise ProbeError("no cf-status result line found (run did not render?)")
    tools = [
        {"name": item.group("name").strip(), "ok": item.group("mark") == "✔"}
        for item in _TOOL_ITEM_RE.finditer(html)
    ]
    findings = _section_items(html, "Feststellungen")
    evidence = _section_items(html, "Beleg-IDs")
    tokens_in = match.group("tin")
    tokens_out = match.group("tout")

    def _tokens(value: str | None) -> int | None:
        return int(value) if value and value.isdigit() else None

    seconds = match.group("seconds")
    return {
        "steps": int(match.group("steps")),
        "tool_calls": int(match.group("tools")),
        "model": match.group("model").strip(),
        "response_model": (match.group("response") or "").strip() or None,
        "run_seconds": float(seconds) if seconds else None,
        "tokens_in": _tokens(tokens_in),
        "tokens_out": _tokens(tokens_out),
        "tools": tools,
        "ok_tools": sum(1 for tool in tools if tool["ok"]),
        "findings": len(findings),
        "evidence": len(evidence),
        "proposal_note": "Nur ein Vorschlag" in html,
    }


def check_t20(parsed: dict, expected_model: str = EXPECTED_MODEL) -> list[str]:
    """Return the list of failed T20 criteria (empty means PASS)."""
    failures = []
    if parsed.get("model") != expected_model:
        failures.append(f"model is {parsed.get('model')!r}, expected {expected_model!r}")
    response = parsed.get("response_model")
    if not response:
        failures.append("response model ID missing (server predates the capture?)")
    elif response != expected_model:
        failures.append(f"response model is {response!r}, expected {expected_model!r}")
    if int(parsed.get("ok_tools", 0)) < MIN_OK_TOOLS:
        failures.append(
            f"only {parsed.get('ok_tools', 0)} successful tool calls, need {MIN_OK_TOOLS}+"
        )
    if int(parsed.get("evidence", 0)) < 1:
        failures.append("no validated evidence IDs rendered")
    if int(parsed.get("findings", 0)) < 1:
        failures.append("no findings rendered")
    if parsed.get("tokens_in") is None or parsed.get("tokens_out") is None:
        failures.append("token usage missing (no real API roundtrip proven)")
    if not parsed.get("proposal_note"):
        failures.append("proposal-only note missing (nothing-created proof)")
    return failures


class Session:
    """Cookie-carrying loopback HTTP client (stdlib only)."""

    def __init__(self, base: str) -> None:
        self.base = _validated_loopback_base(base)
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            _NoRedirectHandler(),
        )

    def _request(self, path: str, data: dict | None, timeout: int) -> tuple[int, str]:
        body = None
        headers = {}
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(self.base + path, data=body, headers=headers)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.status, _read_bounded(response).decode(charset, "replace")
        except urllib.error.HTTPError as exc:
            try:
                payload = _read_bounded(exc).decode("utf-8", "replace")
            finally:
                exc.close()
            return exc.code, payload
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProbeError("loopback request timed out") from None
            raise ProbeError("loopback request failed") from None
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            raise ProbeError("loopback request failed") from exc

    def get(self, path: str) -> tuple[int, str]:
        """GET a page (short timeout)."""
        return self._request(path, None, PAGE_TIMEOUT_SECONDS)

    def post(self, path: str, data: dict, timeout: int = PAGE_TIMEOUT_SECONDS) -> tuple[int, str]:
        """POST a form (caller picks the timeout for long runs)."""
        return self._request(path, data, timeout)


def check_ml_status(payload: dict) -> list[str]:
    """Preflight the ml-status JSON (empty list means the run may start)."""
    failures = []
    if payload.get("provider") != "meta":
        failures.append(f"provider is {payload.get('provider')!r}, need 'meta'")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    if meta.get("model") != EXPECTED_MODEL:
        failures.append(f"meta model is {meta.get('model')!r}")
    if meta.get("key_present") is not True:
        failures.append("MODEL_API_KEY missing on the server")
    if meta.get("server_consent") is not True:
        failures.append("CONTEXTFLOW_META_CONSENT missing on the server")
    if meta.get("model_listed") is not True:
        failures.append(f"model not listed by Meta ({meta.get('models_error') or 'unknown'})")
    return failures


def default_out_path() -> str:
    """Evidence path outside the repository (never beside the code)."""
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(tempfile.gettempdir(), f"t20-evidence-{stamp}.json")


def run_probe(base: str, username: str, password: str, link_id: str, out: str) -> dict:
    """Execute the full probe; returns the evidence dict (raises ProbeError)."""
    session = Session(base)
    print(f"[1/5] login as {username} ...")
    code, login_page = session.get("/login")
    if code != 200:
        raise ProbeError(f"GET /login returned {code}")
    code, _ = session.post(
        "/login",
        {"username": username, "password": password, "_csrf_token": parse_csrf(login_page)},
    )
    if code not in (200, 302):
        raise ProbeError(f"login POST returned {code} (bad credentials?)")
    print("[2/5] ml-status preflight (one free models lookup) ...")
    code, status_page = session.get("/contextflow/ml-status")
    if code != 200:
        raise ProbeError(f"ml-status returned {code} (login did not stick?)")
    try:
        status = json.loads(status_page)
    except ValueError:
        raise ProbeError("ml-status is not JSON") from None
    failures = check_ml_status(status)
    if failures:
        raise ProbeError("ml-status preflight failed: " + "; ".join(failures))
    print("[3/5] agent form (pills + case) ...")
    code, form_page = session.get("/contextflow/agent")
    if code != 200:
        raise ProbeError(f"agent form returned {code}")
    pills = parse_pills(form_page)
    missing = sorted(name for name, ok in pills.items() if not ok)
    if missing:
        raise ProbeError("agent pills not ready: " + ", ".join(missing))
    chosen = link_id or parse_first_link_id(form_page)
    print(f"[4/5] ONE consented agent run (link {chosen[:8]}..., up to 7 min) ...")
    code, result_page = session.post(
        "/contextflow/agent/run",
        {"link_id": chosen, "consent_meta": "yes", "_csrf_token": parse_csrf(form_page)},
        timeout=RUN_TIMEOUT_SECONDS,
    )
    if code == 429:
        raise ProbeError("server throttled the run (429) — wait and retry once")
    if code == 409:
        raise ProbeError("another run is in flight (409) — wait and retry once")
    if code != 200:
        raise ProbeError(f"agent run returned {code}")
    parsed = parse_result(result_page)
    verdict = check_t20(parsed)
    evidence = {
        "tool": "t20_live_probe",
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "base": base,
        "link_id": chosen,
        "ml_status": {
            "provider": status.get("provider"),
            "model": status.get("meta", {}).get("model"),
            "model_listed": status.get("meta", {}).get("model_listed"),
        },
        "result": parsed,
        "t20_pass": not verdict,
        "t20_failures": verdict,
    }
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, ensure_ascii=False, indent=2)
    print(f"[5/5] evidence written to {out}")
    return evidence


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (exit 0 only on T20 PASS)."""
    parser = argparse.ArgumentParser(
        description="Run exactly one consented Meta tool-loop against a local demo."
    )
    parser.add_argument("--base", default="http://127.0.0.1:5101")
    parser.add_argument("--user", default="demo")
    parser.add_argument("--password", default="demo-contextflow-1")
    parser.add_argument("--ask-password", action="store_true")
    parser.add_argument("--link-id", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    password = getpass.getpass("Demo password: ") if args.ask_password else args.password
    try:
        evidence = run_probe(args.base, args.user, password, args.link_id, args.out or default_out_path())
    except ProbeError as exc:
        print(f"PROBE FAIL: {exc}")
        return 1
    parsed = evidence["result"]
    print(
        "T20 "
        + ("PASS" if evidence["t20_pass"] else "FAIL")
        + f": steps={parsed['steps']} ok_tools={parsed['ok_tools']} "
        f"evidence={parsed['evidence']} model={parsed['model']} "
        f"response={parsed['response_model']} "
        f"tokens={parsed['tokens_in']}/{parsed['tokens_out']} "
        f"seconds={parsed['run_seconds']}"
    )
    for failure in evidence["t20_failures"]:
        print(f"  - {failure}")
    return 0 if evidence["t20_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
