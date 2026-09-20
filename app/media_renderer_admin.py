"""Administrator UI for the DLNA/UPnP MediaRenderer mini service."""
from __future__ import annotations

import shutil

from flask import Blueprint, abort, flash, g, render_template, request

from simpleoffice_media_renderer import (
    load_media_renderer_settings,
    save_media_renderer_settings,
)
from simpleoffice_mini_core import default_config_path, read_status

from .access_control import audit, is_admin
from .audio_output_discovery import discover_speaker_outputs
from .auth import login_required
from .network_system_status import network_interfaces

bp = Blueprint(
    "media_renderer_admin",
    __name__,
    url_prefix="/admin/mini-services/media-renderer",
)


def admin_required(view):
    @login_required
    def wrapped_view(**kwargs):
        if not is_admin(g.user):
            abort(403)
        return view(**kwargs)

    wrapped_view.__name__ = view.__name__
    return wrapped_view


def _form_settings(current: dict) -> dict:
    return {
        **current,
        "enabled": request.form.get("enabled") == "1",
        "friendly_name": request.form.get("friendly_name", ""),
        "bind": request.form.get("bind", ""),
        "port": request.form.get("port", ""),
        "allow_remote_media": request.form.get("allow_remote_media") == "1",
        "audio_output": request.form.get("audio_output", "default"),
        "video_mode": request.form.get("video_mode", "window"),
    }


@bp.route("", methods=["GET", "POST"])
@admin_required
def index():
    config_path = default_config_path()
    error = ""
    current = load_media_renderer_settings(config_path)
    if request.method == "POST":
        try:
            current = save_media_renderer_settings(_form_settings(current), config_path)
        except (ValueError, TypeError, OSError):
            error = "Media-Renderer-Einstellungen sind ungültig. Felder und Dateirechte prüfen."
            audit(
                "media_renderer_settings_failed",
                "service",
                "media-renderer",
                outcome="failure",
            )
        else:
            audit(
                "media_renderer_settings",
                "service",
                "media-renderer",
                detail={
                    "enabled": current["enabled"],
                    "bind": current["bind"],
                    "port": current["port"],
                    "allow_remote_media": current["allow_remote_media"],
                    "video_mode": current["video_mode"],
                },
            )
            flash("Media-Renderer-Einstellungen gespeichert.")
    try:
        outputs = discover_speaker_outputs()
    except (OSError, RuntimeError, ValueError):
        outputs = []
    try:
        interfaces = network_interfaces()
    except (OSError, RuntimeError, ValueError):
        interfaces = []
    status = read_status(config_path).get("services", {}).get("media-renderer", {})
    return render_template(
        "admin/media_renderer.html",
        settings=current,
        status=status,
        outputs=outputs,
        interfaces=interfaces,
        ffplay_available=shutil.which("ffplay") is not None,
        error=error,
    )
