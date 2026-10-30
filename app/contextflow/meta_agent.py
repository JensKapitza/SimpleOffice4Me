"""Multi-step Meta runtime agent over one confirmed demo case (P0-G).

Runs a real tool loop against the Meta Model API Responses endpoint with
the pinned ``muse-spark-1.3-contributor`` model. The agent starts with
only a case reference and must call at least two read-only evidence
tools before answering; every tool executes server-side with the
actor's own rights on a verified synthetic demo root. The final
proposal is schema-validated, its evidence ids are checked against
sources actually read in this run, and ``requires_approval`` is always
true — the existing ContextFlow approval path stays in charge. No
writes, no mail, no SMTP, no model-driven actions.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .adapters import ContextflowReads, _require_actor
from .case_links import CaseLinkStore
from .demo import is_verified_demo_root
from .vision import (
    META_MODEL_API_ENDPOINT,
    META_MODEL_API_MODEL,
    MetaVisionProvider,
    _NoRedirectHandler,
)

BERLIN = ZoneInfo("Europe/Berlin")
MAX_AGENT_STEPS = 5
MIN_TOOL_CALLS = 2
META_CALL_TIMEOUT = 60.0
# Measured 30.10. live: the model emits reasoning items first (246 of
# 502 output tokens for step 1 without tuning), and later steps exceed
# small budgets. Minimal reasoning effort cuts this to 39 tokens with
# identical tool behavior; 2048 stays a small hard budget with headroom.
META_MAX_OUTPUT_TOKENS = 2048
META_REASONING_EFFORT = "minimal"
META_MAX_RESPONSE_BYTES = 256 * 1024
META_MAX_ARGS_BYTES = 4096
_MAX_STR = 500
_MAX_ITEMS = 50

ACTION_TYPES = frozenset({"task.create", "invoice.open", "mail.draft", "visit.open"})
FINDING_FILTERS = frozenset({"unreviewed", "all"})
SEVERITIES = frozenset({"info", "low", "medium", "high"})

# Per-actor cost guard for the synchronous loop (mirrors the
# github_error_reporter reserve/release pattern): at most 5 runs per
# actor per 5 minutes, and never two concurrent runs per actor.
# In-memory per process, like the reference pattern.
AGENT_RUN_WINDOW_SECONDS = 300
AGENT_RUN_LIMIT = 5
AGENT_ACTOR_CACHE_LIMIT = 1024
_agent_limit_lock = threading.Lock()
_agent_run_attempts: dict[str, deque[float]] = {}
_agent_run_inflight: set[str] = set()
_MAX_CALLS_PER_STEP = 4
_TOOL_ARG_KEYS = {
    "get_case_context": frozenset({"link_id"}),
    "get_visit_findings": frozenset({"link_id", "status_filter"}),
    "get_invoice_state": frozenset({"link_id"}),
    "get_calendar_context": frozenset({"link_id"}),
}

Transport = Callable[[dict[str, Any]], dict[str, Any]]


class MetaAgentError(Exception):
    """The Meta demo analysis failed honestly (no silent fallback)."""


def _clip_text(value: object, limit: int = _MAX_STR) -> str:
    return str(value or "")[:limit]


def _clip_items(items: object, limit: int = _MAX_ITEMS) -> list:
    if not isinstance(items, list):
        return []
    return items[:limit]


def _check_settings(settings: object) -> tuple[str, str, str]:
    """Validate the Meta server gates; return (key, endpoint, model)."""
    if not isinstance(settings, dict):
        raise PermissionError("Meta-Demo ist nicht konfiguriert")
    provider = str(settings.get("provider", "local") or "local").strip().lower()
    if provider != "meta":
        raise PermissionError("Meta-Demo-Analyse ist nicht aktiviert")
    if settings.get("meta_consent") is not True:
        raise PermissionError("Meta-Demo-Analyse braucht die Serverfreigabe")
    key = str(settings.get("meta_api_key", "") or "")
    endpoint = str(
        settings.get("meta_endpoint", META_MODEL_API_ENDPOINT)
        or META_MODEL_API_ENDPOINT
    ).strip()
    model = str(
        settings.get("meta_model", META_MODEL_API_MODEL) or META_MODEL_API_MODEL
    ).strip()
    if not key:
        raise PermissionError("Meta-Demo-Analyse braucht einen Model-API-Schlüssel")
    if endpoint != META_MODEL_API_ENDPOINT:
        raise MetaAgentError("Meta-Endpunkt ist nicht der offizielle Endpunkt")
    if model != META_MODEL_API_MODEL:
        raise MetaAgentError("Nur das freigegebene Meta-Modell ist erlaubt")
    return key, endpoint, model


def _default_transport(endpoint: str, api_key: str) -> Transport:
    """Live Responses transport: pinned endpoint, no redirects, capped read."""

    def call(body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=META_CALL_TIMEOUT) as response:
                raw = response.read(META_MAX_RESPONSE_BYTES + 1)
        except TimeoutError:
            raise MetaAgentError(
                "Meta-Aufruf hat das Zeitlimit überschritten"
            ) from None
        except urllib.error.HTTPError as exc:
            raise MetaAgentError(
                f"Meta-Aufruf abgelehnt (HTTP {exc.code})"
            ) from None
        except (urllib.error.URLError, OSError) as exc:
            raise MetaAgentError(
                f"Meta-Aufruf ist nicht erreichbar ({exc})"
            ) from None
        if len(raw) > META_MAX_RESPONSE_BYTES:
            raise MetaAgentError("Meta-Antwort überschreitet das Lesebudget")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise MetaAgentError("Meta-Antwort ist kein gültiges JSON") from None
        if not isinstance(payload, dict):
            raise MetaAgentError("Meta-Antwort ist kein gültiges JSON")
        return payload

    return call


class _Run:
    """One bound agent run: tools, evidence ledger, and step budget."""

    def __init__(
        self, actor: str, link: dict[str, Any], root: Path, now: datetime
    ) -> None:
        self.actor = actor
        self.link_id = str(link.get("link_id", ""))
        self.case_id = str(link.get("mail_case_id", ""))
        self.contact_id = str(link.get("contact_id", ""))
        self.visit_id = str(link.get("visit_id", ""))
        self.invoice_id = str(link.get("invoice_id", "") or "")
        self.completed_on = str(link.get("completed_on", ""))
        self.reads = ContextflowReads(root)
        self.now = now
        self.evidence_ids: set[str] = set()
        self.tool_calls: list[dict[str, Any]] = []
        self.executed_ok = 0

    def _remember(self, *ids: object) -> None:
        for value in ids:
            text = str(value or "").strip()
            if text:
                self.evidence_ids.add(text[:128])

    def _check_args(self, tool: str, args: dict[str, Any]) -> None:
        allowed = _TOOL_ARG_KEYS.get(tool, frozenset())
        for key in args:
            if key not in allowed:
                raise ValueError(f"unknown tool argument: {key}")
        if args.get("link_id") != self.link_id:
            raise ValueError("tool call is not bound to this case")

    def tool_case_context(self, args: dict[str, Any]) -> dict[str, Any]:
        self._check_args("get_case_context", args)
        case = self.reads.read_mail_case(self.actor, self.case_id)
        contact = self.reads.read_contact(self.actor, self.contact_id)
        summary = case["summary"]
        self._remember(summary.get("case_id"), contact["summary"].get("contact_id"))
        return {
            "case_id": summary.get("case_id"),
            "title": _clip_text(summary.get("title")),
            "status": _clip_text(summary.get("status")),
            "message_count": summary.get("message_count", 0),
            "contact_display_name": _clip_text(
                contact["summary"].get("display_name")
            ),
            "visit_id": self.visit_id,
            "invoice_id": self.invoice_id or None,
            "completed_on": self.completed_on,
        }

    def tool_visit_findings(self, args: dict[str, Any]) -> dict[str, Any]:
        self._check_args("get_visit_findings", args)
        status_filter = str(args.get("status_filter", "unreviewed") or "unreviewed")
        if status_filter not in FINDING_FILTERS:
            raise ValueError("unknown status filter")
        visit = self.reads.read_visit(self.actor, self.visit_id)
        summary = visit["summary"]
        findings = []
        for item in _clip_items(summary.get("findings")):
            if not isinstance(item, dict):
                continue
            if status_filter == "unreviewed" and item.get("status") != "unreviewed":
                continue
            self._remember(item.get("finding_id"), item.get("asset_id"))
            findings.append(
                {
                    "finding_id": item.get("finding_id"),
                    "title": _clip_text(item.get("title")),
                    "status": item.get("status"),
                    "evidence_class": item.get("evidence_class"),
                    "observed_at": item.get("observed_at"),
                    "asset_id": item.get("asset_id") or None,
                }
            )
        assets = []
        for asset in _clip_items(summary.get("assets")):
            if not isinstance(asset, dict):
                continue
            self._remember(asset.get("asset_id"))
            assets.append(
                {
                    "asset_id": asset.get("asset_id"),
                    "name": _clip_text(asset.get("name")),
                    "kind": _clip_text(asset.get("kind")),
                    "status": asset.get("status"),
                }
            )
        self._remember(summary.get("visit_id"))
        return {
            "visit_id": summary.get("visit_id"),
            "title": _clip_text(summary.get("title")),
            "status": summary.get("status"),
            "visit_date": summary.get("visit_date"),
            "status_filter": status_filter,
            "findings": findings,
            "assets": assets,
        }

    def tool_invoice_state(self, args: dict[str, Any]) -> dict[str, Any]:
        self._check_args("get_invoice_state", args)
        state = self.reads.read_invoice_state(
            self.actor,
            contact_id=self.contact_id,
            case_link_confirmed=True,
            invoice_id=self.invoice_id or None,
        )
        if state.get("state") == "linked":
            self._remember(state.get("invoice_id"))
            return {
                "state": "linked",
                "invoice_id": state.get("invoice_id"),
                "invoice_number": _clip_text(state.get("invoice_number"), 64),
                "status": state.get("status"),
                "issue_date": state.get("issue_date"),
            }
        return {"state": "unknown", "reason": state.get("reason", "unknown")}

    def tool_calendar_context(self, args: dict[str, Any]) -> dict[str, Any]:
        self._check_args("get_calendar_context", args)
        lower = self.now - timedelta(days=7)
        upper = self.now + timedelta(days=30)
        data = self.reads.read_calendar(self.actor, lower, upper)
        items = []
        for row in _clip_items(data.get("items"), 20):
            if not isinstance(row, dict):
                continue
            self._remember(row.get("event_id"))
            items.append(
                {
                    "event_id": row.get("event_id"),
                    "title": _clip_text(row.get("title")),
                    "start": row.get("start"),
                    "end": row.get("end"),
                }
            )
        return {"window": {"lower": lower.isoformat(), "upper": upper.isoformat()},
                "events": items}


def _tool_summary(name: str, output: dict[str, Any]) -> str:
    """One honest status line per successful tool call (actor's own data)."""
    if name == "get_visit_findings":
        count = len(output.get("findings", []) or [])
        noun = "Befund" if count == 1 else "Befunde"
        planning = str(output.get("status_filter", "unreviewed"))
        return f"{count} {noun} (Filter: {planning})"
    if name == "get_case_context":
        return f"Fall: {_clip_text(output.get('title'), 60)}"
    if name == "get_invoice_state":
        if output.get("state") == "linked":
            number = _clip_text(output.get("invoice_number"), 32)
            state = str(output.get("status", ""))
            return f"Rechnung {number} ({state})".strip()
        return "Rechnung: unbekannt"
    if name == "get_calendar_context":
        count = len(output.get("events", []) or [])
        noun = "Termin" if count == 1 else "Termine"
        return f"{count} {noun}"
    return ""


_TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "name": "get_case_context",
        "description": (
            "Read the confirmed mail case and contact display name for "
            "this demo case. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {"link_id": {"type": "string"}},
            "required": ["link_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_visit_findings",
        "description": (
            "Read visit findings and assets for this demo case. "
            "status_filter 'unreviewed' (default) or 'all'. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "link_id": {"type": "string"},
                "status_filter": {"type": "string", "enum": ["unreviewed", "all"]},
            },
            "required": ["link_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_invoice_state",
        "description": (
            "Read the invoice state for this demo case: linked with "
            "number/status or unknown. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {"link_id": {"type": "string"}},
            "required": ["link_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_calendar_context",
        "description": (
            "Read calendar events around now for this demo case. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {"link_id": {"type": "string"}},
            "required": ["link_id"],
            "additionalProperties": False,
        },
    },
)

_TOOL_HANDLERS = {
    "get_case_context": _Run.tool_case_context,
    "get_visit_findings": _Run.tool_visit_findings,
    "get_invoice_state": _Run.tool_invoice_state,
    "get_calendar_context": _Run.tool_calendar_context,
}

_SYSTEM_PROMPT = (
    "Du bist der Büroengel-Analysator für einen synthetischen Demo-Fall. "
    "Du startest nur mit einer Fallreferenz und musst Belege über "
    "Werkzeuge lesen: rufe mindestens zwei verschiedene Werkzeuge auf. "
    "Antworte am Ende NUR mit einem JSON-Objekt mit den Schlüsseln "
    "summary (string), findings (Liste aus {title, detail, severity aus "
    "info|low|medium|high}, mindestens 1 Eintrag), evidence_ref_ids "
    "(Liste gelesener IDs, mindestens 1), unknowns (Liste offener "
    "Punkte), proposed_actions (Liste aus {type aus task.create|"
    "invoice.open|mail.draft|visit.open, title, target, why}, mindestens "
    "1 Eintrag) und requires_approval (immer true). Nenne nur IDs, die "
    "dir Werkzeuge geliefert haben — erfinde keine. Deutsch, kurz, "
    "freundlich. Keine Aktionen ausführen, nur vorschlagen."
)


def _parse_output(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Split one Responses payload into function calls and text."""
    status = payload.get("status")
    if status is not None and status != "completed":
        reason = ""
        details = payload.get("incomplete_details")
        if isinstance(details, dict) and details.get("reason"):
            reason = f" ({details['reason']})"
        raise MetaAgentError(f"Meta-Lauf endete mit Status {status}{reason}")
    output = payload.get("output")
    if not isinstance(output, list):
        raise MetaAgentError("Meta-Antwort ohne Ergebnisliste")
    calls: list[dict[str, Any]] = []
    texts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            calls.append(item)
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "output_text"
                and str(block.get("text") or "").strip()
            ):
                texts.append(str(block["text"]))
    return calls, "\n".join(texts)


def _final_json(text: str) -> dict[str, Any]:
    """Parse the final answer; tolerate fenced JSON blocks."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:] if len(lines) > 1 else []
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        parsed = json.loads(stripped)
    except ValueError:
        raise MetaAgentError("Meta-Ergebnis ist kein gültiges JSON") from None
    if not isinstance(parsed, dict):
        raise MetaAgentError("Meta-Ergebnis ist kein gültiges JSON")
    return parsed


def _require_text(value: object, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MetaAgentError(f"Meta-Ergebnis ohne {what}")
    return value.strip()[:limit]


def _validate_final(parsed: dict[str, Any], run: _Run) -> dict[str, Any]:
    """Schema-check the proposal against sources read in this run."""
    findings = parsed.get("findings")
    if not isinstance(findings, list) or not findings:
        raise MetaAgentError("Meta-Ergebnis ohne Befunde")
    clean_findings = []
    for entry in _clip_items(findings, 20):
        if not isinstance(entry, dict):
            raise MetaAgentError("Meta-Befund ist ungültig")
        severity = str(entry.get("severity", "info") or "info").strip().lower()
        if severity not in SEVERITIES:
            raise MetaAgentError("Meta-Befund mit ungültiger Dringlichkeit")
        clean_findings.append(
            {
                "title": _require_text(entry.get("title"), "Befundtitel", 300),
                "detail": _require_text(entry.get("detail"), "Befundtext", 2000),
                "severity": severity,
            }
        )
    ref_ids = parsed.get("evidence_ref_ids")
    if not isinstance(ref_ids, list) or not ref_ids:
        raise MetaAgentError("Meta-Ergebnis ohne Beleg-IDs")
    clean_refs = []
    for ref in _clip_items(ref_ids):
        text = str(ref or "").strip()
        if not text:
            raise MetaAgentError("Meta-Ergebnis mit leerer Beleg-ID")
        if text not in run.evidence_ids:
            raise MetaAgentError("Meta-Ergebnis mit unbelegter Beleg-ID")
        clean_refs.append(text[:128])
    unknowns = parsed.get("unknowns", [])
    if not isinstance(unknowns, list):
        raise MetaAgentError("Meta-Ergebnis mit ungültigen Unbekannten")
    clean_unknowns = [
        _clip_text(item) for item in _clip_items(unknowns, 20) if str(item or "").strip()
    ]
    actions = parsed.get("proposed_actions")
    if not isinstance(actions, list) or not actions:
        raise MetaAgentError("Meta-Ergebnis ohne Handlungsvorschlag")
    clean_actions = []
    for entry in _clip_items(actions, 10):
        if not isinstance(entry, dict):
            raise MetaAgentError("Handlungsvorschlag ist ungültig")
        action_type = str(entry.get("type", "") or "").strip()
        if action_type not in ACTION_TYPES:
            raise MetaAgentError("Handlungsvorschlag mit ungültigem Typ")
        clean_actions.append(
            {
                "type": action_type,
                "title": _require_text(entry.get("title"), "Vorschlagstitel", 200),
                "target": _require_text(entry.get("target"), "Vorschlagsziel", 200),
                "why": _require_text(entry.get("why"), "Vorschlagsgrund", 1000),
            }
        )
    return {
        "summary": _require_text(parsed.get("summary"), "Zusammenfassung", 2000),
        "findings": clean_findings,
        "evidence_ref_ids": clean_refs,
        "unknowns": clean_unknowns,
        "proposed_actions": clean_actions,
        "requires_approval": True,
    }


def reserve_agent_run(actor: str) -> tuple[str, int | None]:
    """Reserve one synchronous agent run for an actor.

    Returns ("ok", None), ("limited", retry_after_seconds), or
    ("busy", None) when the actor already has a run in flight.
    The caller must pass every "ok" slot to release_agent_run,
    even when the run itself fails.
    """
    now = time.monotonic()
    with _agent_limit_lock:
        if actor in _agent_run_inflight:
            return "busy", None
        attempts = _agent_run_attempts.get(actor)
        if attempts is None:
            if len(_agent_run_attempts) >= AGENT_ACTOR_CACHE_LIMIT:
                oldest = next(iter(_agent_run_attempts))
                _agent_run_attempts.pop(oldest, None)
            attempts = _agent_run_attempts.setdefault(actor, deque())
        cutoff = now - AGENT_RUN_WINDOW_SECONDS
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= AGENT_RUN_LIMIT:
            retry_after = int(attempts[0] + AGENT_RUN_WINDOW_SECONDS - now) + 1
            return "limited", max(retry_after, 1)
        attempts.append(now)
        _agent_run_inflight.add(actor)
        return "ok", None


def release_agent_run(actor: str) -> None:
    """Free the actor's in-flight slot (throttle history is kept)."""
    with _agent_limit_lock:
        _agent_run_inflight.discard(actor)


def reset_agent_run_limits() -> None:
    """Clear throttle and in-flight state (tests only, same process)."""
    with _agent_limit_lock:
        _agent_run_attempts.clear()
        _agent_run_inflight.clear()


def run_agent(
    *,
    actor: str,
    link_id: str,
    consent_meta: bool,
    settings: dict[str, Any],
    root: str | Path,
    now: datetime,
    transport: Transport | None = None,
    max_steps: int = MAX_AGENT_STEPS,
) -> dict[str, Any]:
    """Run the Meta tool loop for one confirmed demo case.

    Gates (fail closed): literal per-request consent, server consent
    flag, ``meta`` provider, Model API key, pinned endpoint and model,
    and a verified synthetic demo root. The model starts with only the
    case reference, must execute at least two evidence tools, and its
    final proposal is validated against sources read in this run. The
    result is a proposal with ``requires_approval=True`` — nothing is
    created, sent, or changed.
    """
    actor = _require_actor(actor)
    if consent_meta is not True:
        raise PermissionError("Meta-Demo-Analyse braucht die Einwilligung")
    if not isinstance(link_id, str) or not link_id.strip():
        raise ValueError("Fallreferenz fehlt")
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("aware timestamp is required")
    if not isinstance(max_steps, int) or max_steps < 1 or max_steps > MAX_AGENT_STEPS:
        raise ValueError("step budget is out of range")
    key, endpoint, model = _check_settings(settings)
    store_root = Path(root).expanduser()
    if not is_verified_demo_root(store_root):
        raise PermissionError("Meta-Demo-Analyse nur auf synthetischem Demo-Root")
    link = CaseLinkStore(store_root).get(actor, link_id.strip())
    if not link.get("mail_case_id") or not link.get("contact_id"):
        raise ValueError("link references are incomplete")
    if not link.get("visit_id"):
        raise ValueError("link references are incomplete")
    sender = transport if transport is not None else _default_transport(endpoint, key)
    run = _Run(actor, link, store_root, now)
    task = (
        f"Untersuche den bestätigten Demo-Fall {run.link_id} "
        f"(abgeschlossen am {run.completed_on or 'unbekannt'}). Finde "
        "Lücken oder Widersprüche in Befunden, Rechnung, Aufgabenlage "
        "oder Terminen und schlage begründete nächste Schritte vor."
    )
    conversation: list[dict[str, Any]] = [
        {"role": "developer", "content": [{"type": "input_text", "text": _SYSTEM_PROMPT}]},
        {"role": "user", "content": [{"type": "input_text", "text": task}]},
    ]
    usage: dict[str, Any] = {}
    steps = 0
    for _ in range(max_steps):
        steps += 1
        payload = sender(
            {
                "model": model,
                "input": conversation,
                "tools": list(_TOOL_SPECS),
                "max_output_tokens": META_MAX_OUTPUT_TOKENS,
                "reasoning": {"effort": META_REASONING_EFFORT},
            }
        )
        if not isinstance(payload, dict):
            raise MetaAgentError("Meta-Antwort ist kein gültiges JSON")
        for key_name in ("input_tokens", "output_tokens"):
            value = payload.get("usage", {}).get(key_name) if isinstance(
                payload.get("usage"), dict
            ) else None
            if isinstance(value, int):
                usage[key_name] = value
        calls, text = _parse_output(payload)
        if not calls:
            if run.executed_ok < MIN_TOOL_CALLS:
                raise MetaAgentError(
                    "Meta-Analyse ohne ausreichende Beleglesung"
                )
            return {
                "ok": True,
                "provider": "meta",
                "provider_label": MetaVisionProvider.label,
                "model": model,
                "link_id": run.link_id,
                "steps": steps,
                "tool_calls": run.tool_calls,
                "usage": usage,
                **_validate_final(_final_json(text), run),
            }
        for position, item in enumerate(_clip_items(calls, _MAX_ITEMS)):
            call_id = str(item.get("call_id", "") or "")
            name = str(item.get("name", "") or "")
            raw_args = str(item.get("arguments", "") or "")
            if not call_id or not name:
                raise MetaAgentError("Meta-Werkzeugaufruf ist ungültig")
            handler = _TOOL_HANDLERS.get(name)
            detail = ""
            try:
                if position >= _MAX_CALLS_PER_STEP:
                    raise ValueError("tool call budget exceeded")
                if len(raw_args.encode("utf-8")) > META_MAX_ARGS_BYTES:
                    raise ValueError("tool arguments exceed the budget")
                args = json.loads(raw_args) if raw_args.strip() else {}
                if not isinstance(args, dict):
                    raise ValueError("tool arguments must be an object")
                if handler is None:
                    raise ValueError(f"unknown tool: {name}")
                output_obj: dict[str, Any] = handler(run, args)
                ok = True
                run.executed_ok += 1
                detail = _tool_summary(name, output_obj)
            except (PermissionError, ValueError) as exc:
                output_obj = {"error": _clip_text(exc, 300)}
                ok = False
                detail = _clip_text(exc, 120)
            run.tool_calls.append(
                {
                    "name": name,
                    "call_id": call_id[:64],
                    "ok": ok,
                    "detail": detail,
                }
            )
            conversation.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": raw_args,
                }
            )
            conversation.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(output_obj, ensure_ascii=False),
                }
            )
    raise MetaAgentError("Meta-Analyse hat das Schrittbudget erreicht")
