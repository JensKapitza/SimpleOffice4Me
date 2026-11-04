"""Smart OT travel analysis: one grounded Meta text call, nothing stored.

Companion to ot_travel_hints (deterministic, authoritative): while the
base hint only fires on keyword-matched events with user-maintained
travel_km, this action asks a Meta model to *interpret* one ambiguous
calendar event (off-site verdict with verbatim evidence quote) and to
*propose* a round-trip distance read from the event text.

Anti-invention rules (fail-closed, mirrored from contact_suggest):
- the verdict needs a verbatim evidence quote from the event text;
  without it the verdict is dropped;
- a proposed distance is only accepted when it is numeric,
  within MAX_TRAVEL_KM, and backed by a verbatim quote containing the
  number — and it is ALWAYS flagged ``unverified_proposal``: it is
  never written to travel_km metadata by this path, adopting stays an
  explicit user edit in the calendar form;
- malformed model payloads raise (no silent fallback);
- mock providers are refused (503, no invented travel data);
- the Meta cloud path needs per-request consent, the server consent
  flag, an API key, and a verified synthetic demo root (real roots
  are refused with 403). Local Ollama (a released Meta text model)
  answers by default.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ..calendar_metadata import MAX_TRAVEL_KM
from .case_links import BERLIN
from .contact_suggest import _normalize_quote
from .doc_tags import complete_text
from .ot_travel_hints import SKIP_STATUSES
from .vision import (
    InferenceUnavailable,
    MockBlockedError,
    VisionError,
    select_provider,
)

TEXT_LIMIT = 2000
EVIDENCE_LIMIT = 200
VERDICTS = ("offsite", "not_offsite", "unclear")
CONFIDENCES = ("low", "medium", "high")


def event_source_text(event: Any) -> str:
    """Assemble the model-readable text of one calendar event."""
    if not isinstance(event, dict):
        return ""
    parts: list[str] = []
    for field in ("title", "location", "reason"):
        value = str(event.get(field) or "").strip()
        if value:
            parts.append(value)
    tags = event.get("tags")
    if isinstance(tags, list):
        names = []
        for tag in tags:
            name = tag.get("name") if isinstance(tag, dict) else tag
            text = str(name or "").strip()
            if text:
                names.append(text)
        if names:
            parts.append("Tags: " + ", ".join(names))
    return "\n".join(parts)[:TEXT_LIMIT]


def build_ot_smart_prompt(source_text: str) -> str:
    """Build the single interpretation prompt for one event text."""
    return (
        "Du bist der Büroengel. Deute folgenden Kalendereintrag: Ist es "
        "ein Auswärtstermin (vor Ort beim Kunden / auf der Baustelle / im "
        "Außendienst) oder nicht? Antworte NUR mit einem JSON-Objekt "
        '{"verdict": "offsite|not_offsite|unclear", '
        '"confidence": "low|medium|high", "evidence": "...", '
        '"distance_km": Zahl-oder-null, "distance_evidence": "..."-oder-null}. '
        "evidence muss ein kurzes wörtliches Zitat (max. 200 Zeichen) aus "
        "dem Eintrag sein, das die Deutung belegt; ohne Beleg gilt "
        '"unclear". distance_km darf nur eine Entfernung (Hin- und '
        "Rückweg in km) aus dem Eintrag selbst sein, mit wörtlichem Zitat "
        "als distance_evidence, das die Zahl enthält. NIEMALS Entfernungen "
        "aus Vorwissen schätzen oder erfinden — Unsicheres weglassen "
        "(distance_km null). Antworte nur mit dem JSON-Objekt.\n\n"
        f"Eintrag:\n{source_text}"
    )


def _number_in_quote(number_text: str, quote_key: str) -> bool:
    """Check the quoted text carries the proposed number (both normalized)."""
    digits = "".join(char for char in number_text if char.isdigit())
    if not digits:
        return False
    quote_digits = "".join(char for char in quote_key if char.isdigit())
    return bool(digits) and digits in quote_digits


def validate_ot_smart_analysis(
    raw: object, source_text: str
) -> tuple[dict[str, Any], int]:
    """Validate the model verdict; return (analysis, dropped_count).

    Malformed payloads raise VisionError. A verdict without a verbatim
    evidence quote, or an unbacked/invalid distance proposal, is
    dropped (counted) instead of invented: distance proposals that
    survive are always flagged ``unverified_proposal``.
    """
    if not isinstance(raw, dict):
        raise VisionError("OT-Analyse muss ein JSON-Objekt sein")
    verdict = str(raw.get("verdict", "") or "").strip()
    confidence = str(raw.get("confidence", "") or "").strip()
    evidence = str(raw.get("evidence", "") or "").strip()
    if verdict not in VERDICTS:
        raise VisionError("OT-Analyse braucht verdict offsite/not_offsite/unclear")
    if confidence not in CONFIDENCES:
        raise VisionError("OT-Analyse braucht confidence low/medium/high")
    dropped = 0
    source_key = _normalize_quote(source_text)
    evidence_key = _normalize_quote(evidence)
    if not evidence or len(evidence) > EVIDENCE_LIMIT:
        dropped += 1
        return {"verdict": "unclear", "confidence": "low"}, dropped
    if not evidence_key or evidence_key not in source_key:
        dropped += 1
        return {"verdict": "unclear", "confidence": "low"}, dropped
    analysis: dict[str, Any] = {
        "verdict": verdict,
        "confidence": confidence,
        "evidence": evidence,
    }
    distance_raw = raw.get("distance_km")
    distance_evidence = str(raw.get("distance_evidence", "") or "").strip()
    proposal: Decimal | None = None
    if distance_raw is not None:
        try:
            proposal = Decimal(str(distance_raw).strip().replace(",", "."))
        except (InvalidOperation, ValueError, AttributeError):
            proposal = None
    dist_key = _normalize_quote(distance_evidence)
    if proposal is None or not proposal.is_finite() or proposal <= 0:
        if distance_raw is not None:
            dropped += 1
    elif proposal > MAX_TRAVEL_KM:
        dropped += 1
    elif (
        not distance_evidence
        or len(distance_evidence) > EVIDENCE_LIMIT
        or not dist_key
        or dist_key not in source_key
        or not _number_in_quote(str(distance_raw), dist_key)
    ):
        dropped += 1
    else:
        analysis["distance_proposal_km"] = f"{proposal.normalize():f}"
        analysis["distance_evidence"] = distance_evidence
        analysis["unverified_proposal"] = True
    return analysis, dropped


def find_analyzable_event(
    events: Any,
    contact_id: str,
    event_id: str = "",
    today: str | None = None,
) -> dict[str, Any] | None:
    """Pick one event to interpret: by id, else the next upcoming one.

    Unlike the deterministic hint (keyword + travel_km required), every
    upcoming contact event with readable text qualifies — ambiguous
    events are exactly the smart case. Returns None when nothing
    qualifies.
    """
    if not isinstance(events, list) or not contact_id:
        return None
    if event_id:
        for event in events:
            if (
                isinstance(event, dict)
                and str(event.get("event_id") or "") == event_id
                and str(event.get("contact_id") or "") == contact_id
            ):
                return event
        return None
    day = today or datetime.now(BERLIN).date().isoformat()
    candidates: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("contact_id") or "") != contact_id:
            continue
        if str(event.get("status") or "active") in SKIP_STATUSES:
            continue
        if str(event.get("start") or "")[:10] < day:
            continue
        if not event_source_text(event):
            continue
        candidates.append((str(event.get("start") or ""), event))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _require_provider(
    settings: dict[str, Any], consent_meta: bool = False, root: object = None
) -> Any:
    """Select the real cloud provider; refuse mock placeholders.

    Local Ollama is the default. A cloud backend (Meta or OpenAI)
    additionally needs explicit per-request consent, the server-side
    consent flag, an API key, and a verified synthetic demo root;
    real data roots stay excluded (PermissionError, routed as 403).
    """
    choice = str(settings.get("provider", "local") or "local").strip().lower()
    if choice == "local" and not str(settings.get("ollama_model", "") or "").strip():
        raise InferenceUnavailable(
            "Kein freigegebenes Meta-Modell konfiguriert. Bitte ein Ollama-Modell wählen."
        )
    provider = select_provider(settings, consent_meta is True, root)
    if provider.name == "mock":
        raise MockBlockedError(
            "Für die OT-Analyse ist kein Meta-Modell verfügbar "
            "(Platzhalter sind hier nicht erlaubt). Bitte Ollama mit "
            "einem freigegebenen Meta-Modell einrichten."
        )
    return provider


def suggest_ot_travel_analysis(
    *,
    event_text: str,
    settings: dict[str, Any],
    consent_meta: bool = False,
    root: object = None,
) -> dict[str, Any]:
    """Analyse one event text with a single Meta text call; stores nothing.

    Returns the grounded verdict plus an optional explicitly unverified
    distance proposal. The deterministic OT hint stays authoritative:
    this proposal never becomes travel_km metadata by itself.
    """
    warnings: list[str] = []
    clean_text = str(event_text or "").strip()
    if not clean_text:
        raise ValueError("Bitte einen Kalendereintrag zur Deutung auswählen.")
    if len(clean_text) > TEXT_LIMIT:
        clean_text = clean_text[:TEXT_LIMIT]
        warnings.append(f"Der Eintragstext wurde auf {TEXT_LIMIT} Zeichen gekürzt.")
    provider = _require_provider(settings, consent_meta, root)
    prompt = build_ot_smart_prompt(clean_text)
    if provider.name in ("meta", "openai"):
        raw = provider.complete_text(prompt)
    else:
        raw = complete_text(provider, prompt)
    analysis, dropped = validate_ot_smart_analysis(raw, clean_text)
    if dropped:
        warnings.append(
            "Unbelegte Modellangaben wurden verworfen — nichts erfunden."
        )
    return {
        **analysis,
        "provider_label": provider.label,
        "model": getattr(provider, "resolved_model", None) or provider.model,
        "warnings": warnings,
    }
