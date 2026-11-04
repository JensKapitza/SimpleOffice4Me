"""Admin-only runtime Meta key routes (ContextFlow, memory only).

Separated from ``web.py`` to keep that file under the 1000-line project
gate. Registered onto the existing ``contextflow`` blueprint via
``register_meta_key_routes(bp)`` so endpoint names stay
``contextflow.meta_key_*``. The key lives only in ``os.environ`` plus
Flask config for this process, is never written to disk, never logged,
and never rendered; status endpoints return booleans only.
"""

from __future__ import annotations

import os
from typing import Any

from flask import abort, current_app, g, request

from ..access_control import audit, is_admin
from ..auth import login_required
from .demo import is_verified_demo_root


def _helpers():
    """Lazy imports from web to avoid a module-load cycle."""
    from .web import _actor, _gate, _root, _vision_settings

    return _actor, _gate, _root, _vision_settings


def _meta_key_status() -> dict[str, object]:
    _actor, _gate, _root, _vision_settings = _helpers()
    settings: dict[str, Any] = _vision_settings()
    return {
        "key_present": bool(str(settings.get("meta_api_key", "") or "")),
        "runtime_key_present": bool(
            str(current_app.config.get("CONTEXTFLOW_META_API_KEY", "") or "")
        ),
        "provider": str(settings.get("provider", "local") or "local"),
        "server_consent": settings.get("meta_consent") is True,
        "demo_root_ok": is_verified_demo_root(_root()),
    }


@login_required
def meta_key_form():
    """Show the admin-only runtime key status (JSON, no secret)."""
    _actor, _gate, _root, _vision_settings = _helpers()
    _gate()
    _actor()
    if not is_admin(g.user):
        abort(403)
    return {"ok": True, **_meta_key_status()}, 200


@login_required
def meta_key_set():
    """Store the Meta key in process memory after start (admin-only)."""
    _actor, _gate, _root, _vision_settings = _helpers()
    _gate()
    actor = _actor()
    if not is_admin(g.user):
        abort(403)
    raw = str(request.form.get("meta_api_key", "") or "")
    value = raw.strip()
    if value and ("\n" in value or "\r" in value):
        abort(400, description="invalid key format")
    if value and not 8 <= len(value) <= 500:
        abort(400, description="invalid key format")
    if value:
        os.environ["MODEL_API_KEY"] = value
        current_app.config["CONTEXTFLOW_META_API_KEY"] = value
    else:
        current_app.config.pop("CONTEXTFLOW_META_API_KEY", None)
    try:
        audit(
            "contextflow.meta_key_set",
            "contextflow",
            "",
            "success",
            {"actor": actor, "cleared": not bool(value)},
        )
    except (OSError, ValueError, RuntimeError):
        pass
    return {"ok": True, **_meta_key_status()}, 200


@login_required
def meta_key_clear():
    """Drop the runtime Meta key override (admin-only, memory only)."""
    _actor, _gate, _root, _vision_settings = _helpers()
    _gate()
    actor = _actor()
    if not is_admin(g.user):
        abort(403)
    current_app.config.pop("CONTEXTFLOW_META_API_KEY", None)
    try:
        audit(
            "contextflow.meta_key_clear", "contextflow", "", "success", {"actor": actor}
        )
    except (OSError, ValueError, RuntimeError):
        pass
    return {"ok": True, **_meta_key_status()}, 200


def register_meta_key_routes(bp) -> None:
    """Attach the runtime key views to the existing blueprint."""
    bp.add_url_rule("/meta-key", view_func=meta_key_form, methods=["GET"])
    bp.add_url_rule("/meta-key", view_func=meta_key_set, methods=["POST"])
    bp.add_url_rule(
        "/meta-key/clear", view_func=meta_key_clear, methods=["POST"]
    )
