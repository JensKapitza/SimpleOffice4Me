"""Configuration and chart UI for persistent measurements."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, make_response, redirect, render_template, request, url_for

from .access_control import is_admin
from .auth import login_required
from .datalogger_store import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    MAX_POLL_INTERVAL_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    DataLoggerStore,
    pico_availability_path,
)
from .datalogger_collectors import (
    DEFAULT_SENSOR_ALLOWED_HOSTS,
    MAX_SENSOR_SECRET_CHARS,
    read_sensor_allowed_hosts,
    validate_sensor_allowed_hosts,
)
from .datalogger_reporting import EXPORT_ROWS, csv_content, date_window, summarize, summary_pdf
from .db import get_db
from .local_device_access import direct_loopback_request
from .settings_store import SettingsStore
from .system_management import platform_kind

bp = Blueprint("datalogger", __name__, url_prefix="/datalogger")

LINUX_METRICS = {
    "load1", "load5", "load15", "memory_used_percent", "disk_used_percent", "temperature_c",
    "uptime_seconds", "disk_free_bytes", "disk_total_bytes",
}
FILE_METRICS = {"count", "total_bytes", "mtime"}
PAGE_SIZES = {50, 100, 250}
_PICO_LOCAL_NETWORKS = tuple(IPv4Network(block) for block in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
))
SERVER_PRESETS = {
    'uptime_seconds': ('Laufzeit seit Systemstart', 's', 'Linux /proc/uptime. Container können die Laufzeit des Hosts anzeigen.'),
    'disk_free_bytes': ('Freier Speicher für Dokumente', 'B', 'Freie Kapazität im Dateisystem der Dokumentwurzel.'),
    'disk_total_bytes': ('Gesamtspeicher für Dokumente', 'B', 'Gesamtkapazität im Dateisystem der Dokumentwurzel.'),
    'memory_used_percent': ('Arbeitsspeicher belegt', '%', 'Linux /proc/meminfo. Der Wert kann den Host statt eines Containerlimits beschreiben.'),
    'load1': ('Systemlast der letzten Minute', '', 'Linux-Lastmittelwert. Das ist keine CPU-Prozentzahl.'),
}


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
    if kind != "http_json" and form.get("track_availability") == "1":
        raise ValueError("Pico-Zustandswechsel brauchen eine HTTP/JSON-Quelle.")
    if kind == "linux":
        metric = str(form.get("metric", "")).strip() or "load1"
        if metric not in LINUX_METRICS:
            raise ValueError(f"Unbekannte Linux-Messgröße: {metric}. Erlaubt: {', '.join(sorted(LINUX_METRICS))}.")
        return {"metric": metric, "path": (str(form.get("path", "")).strip() or "/")[:500]}
    if kind == "file":
        metric = str(form.get("metric", "")).strip() or "count"
        if metric not in FILE_METRICS:
            raise ValueError(f"Unbekannte Datei-Messgröße: {metric}. Erlaubt: {', '.join(sorted(FILE_METRICS))}.")
        return {"metric": metric, "path": (str(form.get("path", "")).strip() or ".")[:500], "recursive": form.get("recursive") == "1", "max_entries": 100000}
    if kind == "http_json":
        config = {"url": form.get("url", "")[:1000], "json_path": (form.get("json_path", "") or "value")[:300], "timeout": 5,
                  "header_name": form.get("header_name", "Authorization")[:80],
                  "header_secret": str(form.get("header_secret", ""))[:MAX_SENSOR_SECRET_CHARS]}
        if form.get("track_availability") == "1":
            if not pico_availability_path(config):
                raise ValueError("Für Pico-Zustandswechsel nutze den Pfad nodes.KNOTEN.available.")
            config["track_availability"] = True
        return config
    if kind == "lm_sensors": return {"json_path": form.get("json_path", "")[:300]}
    raise ValueError("Unbekannter Quellentyp.")


@bp.get("/")
@login_required
def index():
    from .channel_stale_hints import suggest_stale_channels

    store = _store(); channels = store.channels_for(g.user["username"], is_admin())
    series = [{"id": row["channel_id"], "name": row["name"], "unit": row["unit"], "color": row["color"],
               "samples": [dict(sample) for sample in store.samples(row["channel_id"], 200)]} for row in channels]
    return render_template(
        "datalogger/index.html", channels=channels, series=series,
        can_manage_settings=is_admin(),
        server_presets=SERVER_PRESETS if is_admin() and platform_kind() == 'linux' else {},
        stale_hints=suggest_stale_channels(series),
    )


@bp.get("/serial")
@login_required
def serial_terminal():
    local = direct_loopback_request()
    channels = []
    if local:
        store = _store()
        username = g.user["username"]
        admin = is_admin()
        channels = [row for row in store.channels_for(username, admin) if store.can_edit(row, username, admin)]
    response = make_response(render_template(
        "datalogger/serial.html", serial_allowed=local, channels=channels, can_manage_settings=is_admin(),
    ))
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    serial = "(self)" if local else "()"
    response.headers["Permissions-Policy"] = (
        f"camera=(), microphone=(), geolocation=(), display-capture=(), serial={serial}"
    )
    return response


@bp.post("/serial/pico-setup")
@login_required
def setup_pico_example():
    if not is_admin():
        abort(403)
    if set(request.form) - {"_csrf_token", "gateway_ip", "port", "unit", "availability"}:
        abort(400)
    if any(len(request.form.getlist(key)) != 1 for key in request.form):
        abort(400)
    address = request.form.get("gateway_ip", "").strip()
    port = request.form.get("port", "8080").strip()
    unit = request.form.get("unit", "").strip()
    availability = request.form.get("availability")
    try:
        if len(address) > 15 or len(port) > 5 or not port.isascii() or not port.isdecimal():
            raise ValueError
        host = IPv4Address(address)
        port_number = int(port)
        if (not any(host in network for network in _PICO_LOCAL_NETWORKS)
                or not 1 <= port_number <= 65535 or len(unit) > 40 or any(ord(char) < 32 for char in unit)
                or availability not in (None, "1")):
            raise ValueError
    except ValueError:
        flash("Gib eine lokale IPv4-Adresse, einen gültigen Port und eine kurze Einheit ein.", "warning")
        return redirect(url_for("datalogger.serial_terminal", _anchor="pico-setup"), code=303)

    if str(host) not in read_sensor_allowed_hosts(current_app.config["DOCUMENT_ROOT"]):
        flash("Erlaube die Gateway-IP zuerst in den Datenlogger-Einstellungen.", "warning")
        return redirect(url_for("datalogger.serial_terminal", _anchor="pico-setup"), code=303)
    try:
        created = _store().provision_pico_example(g.user["username"], f"http://{host}:{port_number}/status",
                                                  unit, availability=availability == "1")
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Pico setup failed: %s", type(exc).__name__)
        abort(503)
    flash(f"{created} Pico-Kanäle angelegt. Der Datendienst beginnt mit der Messung."
          if created else "Diese Pico-Kanäle sind schon eingerichtet und bleiben unverändert.", "success")
    return redirect(url_for("datalogger.index"), code=303)


@bp.post('/server-presets/<metric>')
@login_required
def server_preset(metric):
    if not is_admin():
        abort(403)
    if metric not in SERVER_PRESETS or platform_kind() != 'linux':
        abort(404)
    name, unit, description = SERVER_PRESETS[metric]
    config = {'metric': metric}
    if metric in {'disk_free_bytes', 'disk_total_bytes'}:
        config['path'] = str(current_app.config['DOCUMENT_ROOT'])
    channel_id = _store().provision_server_preset(g.user['username'], metric, name, unit, description, config)
    return redirect(url_for('datalogger.channel', channel_id=channel_id))


@bp.get("/settings")
@login_required
def settings():
    if not is_admin():
        abort(403)
    try:
        tick_seconds = _store().poll_interval_seconds()
        allowed_hosts = SettingsStore(current_app.config["DOCUMENT_ROOT"]).settings()["datalogger"]["allowed_hosts"]
    except (OSError, sqlite3.Error, ValueError) as exc:
        current_app.logger.warning("Datalogger settings read failed: %s", type(exc).__name__)
        flash("Datalogger-Einstellung konnte nicht gelesen werden. Datenbankrechte prüfen.")
        return redirect(url_for("datalogger.index"))
    return render_template(
        "datalogger/settings.html", tick_seconds=tick_seconds,
        default_tick_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
        min_tick_seconds=MIN_POLL_INTERVAL_SECONDS,
        max_tick_seconds=MAX_POLL_INTERVAL_SECONDS,
        allowed_hosts=allowed_hosts,
        default_allowed_hosts=list(DEFAULT_SENSOR_ALLOWED_HOSTS),
    )


@bp.post("/settings")
@login_required
def save_settings():
    if not is_admin():
        abort(403)
    submitted = request.form.get("poll_interval_seconds", "")
    reset_defaults = request.form.get("reset") == "1"
    if reset_defaults:
        submitted = DEFAULT_POLL_INTERVAL_SECONDS
    try:
        _store().update_poll_interval(submitted, g.user["username"])
        if reset_defaults:
            allowed_hosts = list(DEFAULT_SENSOR_ALLOWED_HOSTS)
        else:
            raw_hosts = request.form.get("allowed_hosts", "")
            parsed_hosts = [
                item for chunk in raw_hosts.replace("\n", ",").split(",")
                if (item := chunk.strip())
            ]
            allowed_hosts = validate_sensor_allowed_hosts(parsed_hosts)
        SettingsStore(current_app.config["DOCUMENT_ROOT"]).save(
            {"datalogger": {"allowed_hosts": allowed_hosts}}, str(g.user["username"]),
        )
    except ValueError as exc:
        flash(str(exc))
        return render_template(
            "datalogger/settings.html", tick_seconds=submitted,
            default_tick_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
            min_tick_seconds=MIN_POLL_INTERVAL_SECONDS,
            max_tick_seconds=MAX_POLL_INTERVAL_SECONDS,
            allowed_hosts=request.form.get("allowed_hosts", "").split(),
            default_allowed_hosts=list(DEFAULT_SENSOR_ALLOWED_HOSTS),
        ), 400
    except (OSError, sqlite3.Error) as exc:
        current_app.logger.warning("Datalogger settings save failed: %s", type(exc).__name__)
        flash("Datalogger-Einstellung konnte nicht gespeichert werden. Datenbankrechte prüfen.")
        return render_template(
            "datalogger/settings.html", tick_seconds=submitted,
            default_tick_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
            min_tick_seconds=MIN_POLL_INTERVAL_SECONDS,
            max_tick_seconds=MAX_POLL_INTERVAL_SECONDS,
            allowed_hosts=request.form.get("allowed_hosts", "").split(),
            default_allowed_hosts=list(DEFAULT_SENSOR_ALLOWED_HOSTS),
        ), 503
    flash("Datalogger-Einstellungen gespeichert. Sie gelten ab dem nächsten Worker-Zyklus.")
    return redirect(url_for("datalogger.settings"))


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
    start_day, end_day, window = _report_period()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get("per_page", 100))
    except (TypeError, ValueError):
        per_page = 100
    if per_page not in PAGE_SIZES:
        per_page = 100
    total = store.sample_count(channel_id, utc_window=window)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    samples = [dict(row) for row in store.samples(channel_id, per_page, offset=(page - 1) * per_page, utc_window=window)]
    chart_samples = [dict(row) for row in store.samples(channel_id, 200, utc_window=window)]
    latest = next(iter(store.samples(channel_id, 1)), None)
    errors = _report_errors(store, channel_id, window, limit=20)
    for error in errors:
        day = date.fromisoformat(error['measured_at'][:10])
        error['before'] = (day - timedelta(days=1)).isoformat()
        error['after'] = (day + timedelta(days=1)).isoformat()
    sources = store.sources(channel_id)
    source_names = {source["source_id"]: source["name"] for source in sources}
    changes = store.pico_availability_changes(channel_id)
    for change in changes:
        change["source_name"] = source_names.get(change.pop("source_id"), "Ehemalige Quelle")
    return render_template(
        "datalogger/channel.html", channel=channel, samples=samples, chart_samples=chart_samples,
        sources=sources, availability_changes=changes, can_edit=store.can_edit(channel, g.user["username"], is_admin()), users=_users(),
        page=page, pages=pages, per_page=per_page, total_samples=total,
        linux_metrics=sorted(LINUX_METRICS), file_metrics=sorted(FILE_METRICS),
        start_day=start_day, end_day=end_day, latest=latest, errors=errors,
    )


def _report_period():
    today = datetime.now(timezone.utc).date()
    start = request.args.get('start_day', (today - timedelta(days=6)).isoformat())
    end = request.args.get('end_day', today.isoformat())
    try:
        if not start or not end:
            raise ValueError('Bitte Anfang und Ende des Zeitraums angeben.')
        return start, end, date_window(start, end)
    except ValueError as exc:
        abort(400, str(exc))


def _report_errors(store, channel_id, window, *, limit=EXPORT_ROWS):
    sources = {source['source_id']: source for source in store.sources(channel_id)}
    errors = []
    for entry in store.collection_errors(channel_id, window, limit=limit):
        source = sources.get(entry['source_id'])
        errors.append(dict(measured_at=entry['occurred_at'], source_name=source['name'] if source else 'Ehemalige Quelle',
                           source_kind=source['kind'] if source else '', value=None, quality='error', error_code=entry['code']))
    return errors


@bp.get('/channels/<channel_id>/report.<kind>')
@login_required
def report(channel_id, kind):
    channel = _read_channel(channel_id)
    if kind not in {'csv', 'txt', 'pdf'}:
        abort(404)
    start, end, window = _report_period()
    store = _store()
    if store.sample_count(channel_id, utc_window=window) > EXPORT_ROWS:
        abort(400, 'Zu viele Messwerte. Für einen vollständigen Export den Zeitraum verkürzen.')
    rows = store.samples(channel_id, EXPORT_ROWS, utc_window=window)
    errors = _report_errors(store, channel_id, window)
    if len(rows) + len(errors) > EXPORT_ROWS or len(errors) == EXPORT_ROWS:
        abort(400, 'Zu viele Messwerte oder Ereignisse. Bitte den Zeitraum verkürzen.')
    if kind == 'csv':
        payload, content_type = csv_content(channel, rows + errors), 'text/csv; charset=utf-8'
    else:
        summary = summarize(rows)
        lines = [f"Datenlogger · {channel['name']}", f'Zeitraum: {start} bis {end} (UTC)',
                 f"Einheit: {channel['unit'] or 'nicht angegeben'}",
                 f"Messwerte: {summary['rows']}; gültig: {summary['valid']}; anderer Status: {summary['not_ok']}"]
        if summary['valid']:
            lines.append(f"Minimum: {summary['min']:.6g}; Maximum: {summary['max']:.6g}; Mittelwert: {summary['mean']:.6g}")
        else:
            lines.append('Keine gültigen Messwerte im gewählten Zeitraum.')
        lines.append(f'Protokollierte Erfassungsfehler: {len(errors)}')
        lines.extend(f"{entry['measured_at']} · {entry['source_name']} · {entry['error_code']}" for entry in errors[:20])
        if len(errors) > 20:
            lines.append('Weitere protokollierte Fehler stehen im CSV-Export.')
        lines.append('Nicht aufgezeichnete Messungen sind keine Nullwerte. Eine vollständige Abdeckung wird hier nicht vorausgesetzt.')
        if kind == 'pdf':
            payload, content_type = summary_pdf(lines), 'application/pdf'
        else:
            payload, content_type = '\n'.join(lines) + '\n', 'text/plain; charset=utf-8'
    response = Response(payload, content_type=content_type)
    response.headers['Content-Disposition'] = f'attachment; filename="messbericht.{kind}"'
    response.headers['Cache-Control'] = 'private, no-store'
    return response


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
    return _record_sample(channel_id, measured_at=request.form.get("measured_at") or None)


@bp.post("/serial/samples")
@login_required
def add_serial_sample():
    if not direct_loopback_request():
        abort(403)
    selected = request.form.getlist("channel_id")
    if len(selected) != 1 or len(selected[0]) != 36:
        abort(400)
    channel_id = selected[0]
    try:
        canonical_id = str(UUID(channel_id))
    except ValueError:
        abort(400)
    if channel_id != canonical_id:
        abort(400)
    return _record_sample(channel_id)


def _record_sample(channel_id, *, measured_at=None):
    try:
        _store().add_sample(channel_id, request.form.get("value", ""), g.user["username"],
                            measured_at=measured_at, admin=is_admin())
    except PermissionError:
        abort(403)
    except (ValueError, TypeError):
        flash("Ungültiger Messwert oder Zeitstempel.")
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
    # Never trust Referer as a redirect target; it is an attacker-controlled HTTP header.
    return redirect(url_for("datalogger.index"))


@bp.get("/channels/<channel_id>/data.json")
@login_required
def data(channel_id):
    channel = _read_channel(channel_id)
    extra = {}
    if 'start_day' in request.args or 'end_day' in request.args:
        _, _, window = _report_period()
        store = _store()
        if store.sample_count(channel_id, utc_window=window) > EXPORT_ROWS:
            abort(400, 'Zu viele Messwerte. Für einen vollständigen Export den Zeitraum verkürzen.')
        rows = store.samples(channel_id, EXPORT_ROWS, utc_window=window)
        errors = _report_errors(store, channel_id, window)
        if len(rows) + len(errors) > EXPORT_ROWS or len(errors) == EXPORT_ROWS:
            abort(400, 'Zu viele Messwerte oder Ereignisse. Bitte den Zeitraum verkürzen.')
        rows = [{key: value for key, value in row.items() if key != 'metadata'} for row in rows]
        extra = {'collection_errors': errors, 'period_utc': dict(start=window[0], end_exclusive=window[1])}
    else:
        rows = _store().samples(channel_id, request.args.get("limit", 5000), request.args.get("start"), request.args.get("end"))
    response = jsonify({"channel": {"id": channel_id, "name": channel["name"], "unit": channel["unit"]}, "samples": [dict(row) for row in rows], **extra})
    response.headers["Cache-Control"] = "private, no-store"; return response
