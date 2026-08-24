"""Configuration and chart UI for persistent measurements."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .datalogger_store import DataLoggerStore
from .db import get_db

bp = Blueprint("datalogger", __name__, url_prefix="/datalogger")


def _store(): return DataLoggerStore(current_app.config["DOCUMENT_ROOT"])


def _names(value):
    return sorted({item.strip() for item in value.split(",") if item.strip()})


def _users():
    return [row["username"] for row in get_db().execute("SELECT username FROM user WHERE is_disabled=0 ORDER BY username COLLATE NOCASE")]


def _read_channel(channel_id):
    channel = _store().channel(channel_id)
    if not channel or not _store().can_read(channel, g.user["username"], is_admin()): abort(404)
    return channel


def _source_config(kind, form):
    if kind == "linux": return {"metric": form.get("metric", "load1"), "path": form.get("path", "/")[:500]}
    if kind == "file": return {"metric": form.get("metric", "count"), "path": form.get("path", ".")[:500], "recursive": form.get("recursive") == "1", "max_entries": 100000}
    if kind == "http_json":
        return {"url": form.get("url", "")[:1000], "json_path": form.get("json_path", "value")[:300], "timeout": 5,
                "header_name": form.get("header_name", "Authorization")[:80], "header_env": form.get("header_env", "")[:120]}
    if kind == "lm_sensors": return {"json_path": form.get("json_path", "")[:300]}
    raise ValueError("Unbekannter Quellentyp.")


@bp.get("/")
@login_required
def index():
    store = _store(); channels = store.channels_for(g.user["username"], is_admin())
    series = [{"id": row["channel_id"], "name": row["name"], "unit": row["unit"], "color": row["color"],
               "samples": [dict(sample) for sample in store.samples(row["channel_id"], 200)]} for row in channels]
    return render_template("datalogger/index.html", channels=channels, series=series)


@bp.post("/channels")
@login_required
def create_channel():
    try:
        channel_id = _store().create_channel(request.form.get("name", ""), g.user["username"], request.form.get("description", ""), request.form.get("unit", ""), request.form.get("color", "#0d6efd"))
    except ValueError as exc:
        flash(str(exc)); return redirect(url_for("datalogger.index"))
    return redirect(url_for("datalogger.channel", channel_id=channel_id))


@bp.get("/channels/<channel_id>")
@login_required
def channel(channel_id):
    channel = _read_channel(channel_id); store = _store()
    samples = [dict(row) for row in store.samples(channel_id, request.args.get("limit", 1000))]
    return render_template("datalogger/channel.html", channel=channel, samples=samples, sources=store.sources(channel_id),
                           can_edit=store.can_edit(channel, g.user["username"], is_admin()), users=_users())


@bp.post("/channels/<channel_id>")
@login_required
def update_channel(channel_id):
    known = set(_users())
    readers, editors = set(_names(request.form.get("readers", ""))), set(_names(request.form.get("editors", "")))
    if not readers <= known or not editors <= known:
        abort(400, "Freigaben dürfen nur vorhandene aktive Benutzer enthalten.")
    try:
        _store().update_channel(channel_id, g.user["username"], is_admin(), name=request.form.get("name", ""), description=request.form.get("description", ""), unit=request.form.get("unit", ""), color=request.form.get("color", "#0d6efd"), readers=readers, editors=editors)
    except PermissionError: abort(403)
    flash("Messkanal gespeichert."); return redirect(url_for("datalogger.channel", channel_id=channel_id))


@bp.post("/channels/<channel_id>/samples")
@login_required
def add_sample(channel_id):
    try: _store().add_sample(channel_id, request.form.get("value", ""), g.user["username"], measured_at=request.form.get("measured_at") or None, admin=is_admin())
    except PermissionError: abort(403)
    except (ValueError, TypeError): flash("Ungültiger Messwert oder Zeitstempel.")
    return redirect(url_for("datalogger.channel", channel_id=channel_id))


@bp.post("/channels/<channel_id>/sources")
@login_required
def add_source(channel_id):
    kind = request.form.get("kind", "")
    try:
        _store().add_source(channel_id, g.user["username"], kind, request.form.get("source_name", ""), _source_config(kind, request.form), request.form.get("interval_seconds", 60), is_admin())
        flash("Quelle gespeichert; der getrennte Datendienst übernimmt die erste Messung.")
    except PermissionError: abort(403)
    except (ValueError, TypeError) as exc: flash(str(exc))
    return redirect(url_for("datalogger.channel", channel_id=channel_id))


@bp.post("/sources/<source_id>/toggle")
@login_required
def toggle_source(source_id):
    try: _store().set_source_enabled(source_id, g.user["username"], request.form.get("enabled") == "1", is_admin())
    except PermissionError: abort(403)
    return redirect(request.referrer or url_for("datalogger.index"))


@bp.get("/channels/<channel_id>/data.json")
@login_required
def data(channel_id):
    channel = _read_channel(channel_id)
    rows = _store().samples(channel_id, request.args.get("limit", 5000), request.args.get("start"), request.args.get("end"))
    response = jsonify({"channel": {"id": channel_id, "name": channel["name"], "unit": channel["unit"]}, "samples": [dict(row) for row in rows]})
    response.headers["Cache-Control"] = "private, no-store"; return response
