"""Extra ContextFlow OT route (split from web.py for the file-size gate).

Registers the smart travel-analysis action on the shared
``contextflow`` blueprint; imported for its side effect by
``app/__init__.py`` after ``.web`` (same pattern as position_web).
"""

from __future__ import annotations

from typing import Any

from flask import request

from ..auth import login_required
from ..calendar_store import CalendarStore
from ..contact_store import ContactStore
from .ot_travel_smart import (
    event_source_text,
    find_analyzable_event,
    suggest_ot_travel_analysis,
)
from .web import _actor, _gate, _root, _vision_settings, bp
from .vision import InferenceTimeoutError, InferenceUnavailable


@bp.post("/ot-travel-analyze")
@login_required
def ot_travel_analyze() -> tuple[dict[str, Any], int]:
    """Interpret one calendar event with a single Meta text call (JSON, nothing stored).

    Local Ollama answers by default; the Meta Model API path needs the
    server-side Meta switch plus API key and the explicit per-request
    consent checkbox, otherwise cloud inference is refused here. Meta cloud
    additionally requires a synthetic demo root (real roots answer 403).
    Mock answers are refused with 503 (no invented travel data). The
    deterministic OT hint stays authoritative: a proposed distance is
    always flagged unverified and never becomes travel_km metadata by
    itself — adopting stays a calendar-form edit.
    """
    _gate()
    actor = _actor()
    contact_id = str(request.form.get("contact_id", "") or "").strip()
    event_id = str(request.form.get("event_id", "") or "").strip()
    if not contact_id:
        return {"ok": False, "error": "Kontaktbezug fehlt."}, 400
    try:
        contact = ContactStore(_root()).get(contact_id, actor)
        if not ContactStore(_root()).can_manage_contact(contact, actor):
            raise PermissionError("OT-Analyse braucht Kontakt-Manage-Recht.")
    except PermissionError as exc:
        return {"ok": False, "error": str(exc)}, 403
    except (ValueError, KeyError, OSError):
        return {"ok": False, "error": "Kontakt unbekannt."}, 404
    try:
        events = CalendarStore(_root()).events(actor)
    except (OSError, ValueError):
        return {"ok": False, "error": "Kalender nicht lesbar."}, 400
    event = find_analyzable_event(events, contact_id, event_id)
    if event is None:
        return {"ok": False, "error": "Kein deutbarer Termin gefunden."}, 404
    try:
        result = suggest_ot_travel_analysis(
            event_text=event_source_text(event),
            settings=_vision_settings(),
            consent_meta=request.form.get("consent_meta") == "yes",
            root=_root(),
        )
    except PermissionError as exc:
        return {"ok": False, "error": str(exc)}, 403
    except InferenceUnavailable as exc:
        payload: dict[str, object] = {"ok": False, "error": str(exc)}
        if isinstance(exc, InferenceTimeoutError):
            payload["timeout"] = True
        return payload, 503
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}, 400
    return {"ok": True, "event_id": str(event.get("event_id", "")), **result}, 200
