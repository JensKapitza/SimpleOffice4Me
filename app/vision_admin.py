"""Administrator configuration for the Vision/ML backend (OCR + Meta/Ollama).

The Meta Model API key is entered here and stored encrypted; the page only
ever reports whether a key is present, never the key itself.
"""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .access_control import audit
from .contextflow.vision import ml_status
from .mini_services_admin import admin_required
from .translations import translate
from . import vision_config as config

bp = Blueprint("vision_admin", __name__, url_prefix="/admin/mini-services/vision")
ERRORS = (ValueError, TypeError, OSError)


def _form_value() -> dict:
    return {
        "enabled": request.form.get("enabled") == "1",
        "provider": request.form.get("provider", "local"),
        "ollama_url": request.form.get("ollama_url", ""),
        "ollama_model": request.form.get("ollama_model", ""),
        "ollama_timeout": request.form.get("ollama_timeout", "60"),
        "meta_consent": request.form.get("meta_consent") == "1",
        "meta_endpoint": request.form.get("meta_endpoint", ""),
        "meta_model": request.form.get("meta_model", ""),
        "meta_api_key": request.form.get("meta_api_key", ""),
        "openai_consent": request.form.get("openai_consent") == "1",
        "openai_endpoint": request.form.get("openai_endpoint", ""),
        "openai_model": request.form.get("openai_model", ""),
        "openai_api_key": request.form.get("openai_api_key", ""),
    }


@bp.route("", methods=["GET", "POST"])
@admin_required
def index():
    if request.method == "POST":
        value = _form_value()
        try:
            clean = config.prepare(value, g.language)
            config.persist(clean)
        except ERRORS as exc:
            audit("vision_settings", "service", "vision", outcome="failure",
                  detail={"error_type": type(exc).__name__})
            flash(translate(g.language, "vision.invalid").format(exc=exc))
            value.pop("meta_api_key", None)
            value.pop("openai_api_key", None)
            return render_template("admin/vision_service.html",
                                   config=config.public({**config.settings(), **value}),
                                   status=_status()), 400
        audit("vision_settings", "service", "vision")
        flash(translate(g.language, "vision.saved"))
        return redirect(url_for("vision_admin.index"))
    stored = config.settings()
    return render_template("admin/vision_service.html",
                           config=config.public(stored), status=_status())


def _status() -> dict:
    """ML backend state for the status box (booleans only, no secrets)."""
    from pathlib import Path as _Path

    from .contextflow.demo import is_verified_demo_root

    try:
        settings = config.effective_settings(current_app.config)
    except (ValueError, TypeError, OSError):
        settings = config.effective_settings({})
    status = ml_status(settings)
    try:
        status["demo_root_verified"] = bool(
            is_verified_demo_root(_Path(str(current_app.config["DOCUMENT_ROOT"])))
        )
    except (KeyError, TypeError, ValueError, OSError):
        status["demo_root_verified"] = False
    return status
