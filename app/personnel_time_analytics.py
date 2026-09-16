"""Team/site analytics and automatic federation sync for personnel time."""
from __future__ import annotations

import json
import os
import threading
import time as time_module
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import click
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from . import personnel
from .auth import login_required
from .db import get_db
from .document_store import utc_now
from .federation_core import sanitize_peer_id
from .federation_store import FederationStore
from .federation_worker import _json_request


bp = Blueprint("personnel_time_analytics", __name__, url_prefix="/personnel/time-admin/analytics")
MAX_SYNC_WINDOW_DAYS = 92
MIN_SYNC_INTERVAL_MINUTES = 15
MAX_SYNC_INTERVAL_MINUTES = 24 * 60
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def _insights():
    from . import personnel_time_insights
    return personnel_time_insights


def ensure_schema() -> None:
    insights = _insights()
    insights.ensure_schema()
    db = get_db()
    columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(employee_time_federation_map)").fetchall()}
    additions = {
        "auto_sync": "INTEGER NOT NULL DEFAULT 0",
        "sync_interval_minutes": "INTEGER NOT NULL DEFAULT 60",
        "sync_window_days": "INTEGER NOT NULL DEFAULT 14",
        "last_attempt_at": "TEXT NOT NULL DEFAULT ''",
        "last_success_at": "TEXT NOT NULL DEFAULT ''",
        "last_sync_status": "TEXT NOT NULL DEFAULT ''",
        "last_sync_error": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE employee_time_federation_map ADD COLUMN {name} {definition}")
    db.execute(
        "CREATE INDEX IF NOT EXISTS employee_time_federation_auto_sync ON employee_time_federation_map(auto_sync,last_attempt_at)"
    )
    db.commit()


def _require_admin() -> None:
    if getattr(g, "user", None) is None or not g.user["is_admin"]:
        from flask import abort
        abort(403)


def _period() -> tuple[str, date, date]:
    today = personnel._local_now().date()
    raw = str(request.values.get("period", "30d"))
    try:
        anchor = min(date.fromisoformat(str(request.values.get("anchor", today.isoformat()))), today)
    except ValueError:
        anchor = today
    if raw in {"7d", "30d", "90d"}:
        days = int(raw[:-1])
        return raw, anchor - timedelta(days=days - 1), anchor
    if raw == "month":
        start = anchor.replace(day=1)
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return raw, start, min(following - timedelta(days=1), today)
    if raw == "custom":
        try:
            start = date.fromisoformat(str(request.values.get("start", "")))
            end = min(date.fromisoformat(str(request.values.get("end", ""))), today)
        except ValueError as exc:
            raise ValueError("Individueller Zeitraum ist ungültig") from exc
        if end < start or (end - start).days > 365:
            raise ValueError("Zeitraum muss zwischen 1 und 366 Tagen liegen")
        return raw, start, end
    return "30d", today - timedelta(days=29), today


def _bucket_series(daily: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in sorted(daily, key=lambda item: item["date"]):
        shown = row["date"]
        if mode == "week":
            iso = shown.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
            start = shown - timedelta(days=shown.weekday())
            end = start + timedelta(days=6)
            label = f"KW {iso.week:02d} · {start.strftime('%d.%m.')}–{end.strftime('%d.%m.%Y')}"
        else:
            key = shown.strftime("%Y-%m")
            label = shown.strftime("%m/%Y")
        bucket = buckets.setdefault(key, {"key": key, "label": label, "work_minutes": 0, "target_minutes": 0, "break_minutes": 0})
        bucket["work_minutes"] += int(row.get("work_minutes") or 0)
        bucket["target_minutes"] += int(row.get("presence_target_minutes") or 0)
        bucket["break_minutes"] += int(row.get("break_minutes") or 0)
    result = list(buckets.values())
    scale = max([1, *[max(item["work_minutes"], item["target_minutes"]) for item in result]])
    for item in result:
        item["balance_minutes"] = item["work_minutes"] - item["target_minutes"]
        item["work_pct"] = round(item["work_minutes"] * 100 / scale, 1)
        item["target_pct"] = round(item["target_minutes"] * 100 / scale, 1)
    return result


def team_statistics(start: date, end: date) -> dict[str, Any]:
    insights = _insights()
    employees = insights._employees()
    totals = defaultdict(int)
    rows: list[dict[str, Any]] = []
    daily_map: dict[date, dict[str, Any]] = {}
    for employee in employees:
        stats = insights.period_statistics(employee, start, end)
        values = stats["totals"]
        row = {
            "employee_id": int(employee["id"]),
            "name": employee["name"],
            "work_minutes": int(values["work_minutes"]),
            "target_minutes": int(values["presence_target_minutes"]),
            "balance_minutes": int(values["balance_minutes"]),
            "break_minutes": int(values["break_minutes"]),
            "absence_days": int(values["absence_days"]),
            "missing_days": int(values["missing_days"]),
            "missing_break_days": int(values["missing_break_days"]),
            "late_days": int(values["late_days"]),
            "open_days": int(values["open_days"]),
        }
        rows.append(row)
        for key in ("work_minutes", "target_minutes", "break_minutes", "absence_days", "missing_days", "missing_break_days", "late_days", "open_days"):
            totals[key] += int(row[key])
        for day in stats["daily"]:
            shown = day["date"]
            combined = daily_map.setdefault(shown, {"date": shown, "work_minutes": 0, "presence_target_minutes": 0, "break_minutes": 0})
            combined["work_minutes"] += int(day["work_minutes"])
            combined["presence_target_minutes"] += int(day["presence_target_minutes"])
            combined["break_minutes"] += int(day["break_minutes"])
    totals["balance_minutes"] = totals["work_minutes"] - totals["target_minutes"]
    totals["employees"] = len(rows)
    return {"rows": rows, "totals": dict(totals), "daily": list(daily_map.values())}


def _event_summary(rows: list[dict[str, Any]]) -> tuple[int, int, bool]:
    state = "clock_out"
    work = breaks = 0
    active: datetime | None = None
    paused: datetime | None = None
    allowed = {
        "clock_out": {"clock_in"},
        "clock_in": {"break_start", "clock_out"},
        "break_start": {"break_end"},
        "break_end": {"break_start", "clock_out"},
    }
    for row in rows:
        action = str(row["action"])
        if action not in allowed.get(state, set()):
            return 0, 0, True
        stamp = datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00"))
        if action == "clock_in":
            active = stamp
        elif action == "break_start":
            if active is None:
                return 0, 0, True
            work += max(0, int((stamp - active).total_seconds() // 60))
            paused = stamp
            active = None
        elif action == "break_end":
            if paused is None:
                return 0, 0, True
            breaks += max(0, int((stamp - paused).total_seconds() // 60))
            active = stamp
            paused = None
        elif action == "clock_out":
            if active is None:
                return 0, 0, True
            work += max(0, int((stamp - active).total_seconds() // 60))
            active = None
        state = action
    return work, breaks, state != "clock_out"


def site_statistics(start: date, end: date) -> list[dict[str, Any]]:
    insights = _insights()
    insights.ensure_schema()
    lower, upper = insights._utc_bounds(start, end)
    rows = get_db().execute(
        """SELECT employee_id,action,occurred_at,source_kind,source_peer
           FROM employee_punch WHERE occurred_at>=? AND occurred_at<? ORDER BY occurred_at,id""",
        (lower, upper),
    ).fetchall()
    zone = personnel._personnel_timezone()
    grouped: dict[tuple[str, int, date], list[dict[str, Any]]] = defaultdict(list)
    event_counts: dict[str, int] = defaultdict(int)
    employees: dict[str, set[int]] = defaultdict(set)
    for raw in rows:
        row = dict(raw)
        source = str(row.get("source_peer") or "") if str(row.get("source_kind") or "local") == "federation" else "local"
        source = source or "local"
        shown = datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00")).astimezone(zone).date()
        grouped[(source, int(row["employee_id"]), shown)].append(row)
        event_counts[source] += 1
        employees[source].add(int(row["employee_id"]))
    labels = {"local": "Lokal"}
    try:
        for peer in FederationStore(current_app.config["DOCUMENT_ROOT"]).list_peers():
            labels[str(peer["peer_id"])] = str(peer.get("label") or peer["peer_id"])
    except OSError:
        pass
    result: dict[str, dict[str, Any]] = {}
    for (source, _employee_id, _shown), events in grouped.items():
        item = result.setdefault(source, {"source": source, "label": labels.get(source, source), "work_minutes": 0, "break_minutes": 0, "incomplete_days": 0})
        work, breaks, incomplete = _event_summary(events)
        item["work_minutes"] += work
        item["break_minutes"] += breaks
        item["incomplete_days"] += int(incomplete)
    for source in set(event_counts) | set(result):
        item = result.setdefault(source, {"source": source, "label": labels.get(source, source), "work_minutes": 0, "break_minutes": 0, "incomplete_days": 0})
        item["event_count"] = event_counts[source]
        item["employee_count"] = len(employees[source])
    return sorted(result.values(), key=lambda item: (item["source"] != "local", item["label"].casefold()))


def _mapping_rows() -> list[dict[str, Any]]:
    ensure_schema()
    names = personnel._employee_names()
    peers = {}
    try:
        peers = {str(peer["peer_id"]): str(peer.get("label") or peer["peer_id"]) for peer in FederationStore(current_app.config["DOCUMENT_ROOT"]).list_peers()}
    except OSError:
        pass
    rows = get_db().execute("SELECT * FROM employee_time_federation_map ORDER BY local_employee_id,peer_id,remote_label").fetchall()
    result = []
    for raw in rows:
        row = dict(raw)
        row["employee_name"] = names.get(int(row["local_employee_id"]), f"Mitarbeiter {row['local_employee_id']}")
        row["peer_label"] = peers.get(str(row["peer_id"]), str(row["peer_id"]))
        result.append(row)
    return result


def _parse_stamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _mapping_due(row: dict[str, Any], now: datetime) -> bool:
    last = _parse_stamp(str(row.get("last_attempt_at") or ""))
    interval = max(MIN_SYNC_INTERVAL_MINUTES, min(int(row.get("sync_interval_minutes") or 60), MAX_SYNC_INTERVAL_MINUTES))
    return last is None or now - last.astimezone(timezone.utc) >= timedelta(minutes=interval)


def _claim_mapping(peer_id: str, remote_key: str, *, force: bool, now: datetime) -> dict[str, Any] | None:
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        raw = db.execute(
            "SELECT * FROM employee_time_federation_map WHERE peer_id=? AND remote_employee_key=?",
            (peer_id, remote_key),
        ).fetchone()
        if raw is None:
            db.rollback()
            return None
        row = dict(raw)
        if not force and (not row.get("auto_sync") or not _mapping_due(row, now)):
            db.rollback()
            return None
        db.execute(
            """UPDATE employee_time_federation_map
               SET last_attempt_at=?,last_sync_status='running',last_sync_error=''
               WHERE peer_id=? AND remote_employee_key=?""",
            (now.isoformat(timespec="seconds"), peer_id, remote_key),
        )
        db.commit()
        return row
    except Exception:
        db.rollback()
        raise


def _open_sync_start(employee_id: int, start: date, end: date) -> date | None:
    current = start
    while current <= end and personnel.month_is_closed(employee_id, current.strftime("%Y-%m")):
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        current = next_month
    return current if current <= end else None


def _actor_for_mapping(mapping: dict[str, Any]):
    db = get_db()
    actor = db.execute("SELECT * FROM user WHERE id=?", (int(mapping.get("updated_by") or 0),)).fetchone()
    if actor is None:
        actor = db.execute("SELECT * FROM user WHERE is_admin=1 ORDER BY id LIMIT 1").fetchone()
    if actor is None:
        raise ValueError("Für die automatische Synchronisation ist kein Admin-Benutzer vorhanden")
    return actor


def _sync_mapping(mapping: dict[str, Any]) -> dict[str, int]:
    insights = _insights()
    peer_id = sanitize_peer_id(str(mapping["peer_id"]))
    remote_key = str(mapping["remote_employee_key"])
    local_employee_id = int(mapping["local_employee_id"])
    end = personnel._local_now().date()
    window = max(1, min(int(mapping.get("sync_window_days") or 14), MAX_SYNC_WINDOW_DAYS))
    requested_start = end - timedelta(days=window - 1)
    start = _open_sync_start(local_employee_id, requested_start, end)
    if start is None:
        return {"inserted": 0, "updated": 0, "removed": 0, "unchanged": 0}
    store = FederationStore(current_app.config["DOCUMENT_ROOT"])
    peer = store.get_peer(peer_id)
    if not peer or not peer.get("enabled"):
        raise ValueError("Federation-Peer ist nicht aktiv")
    token = store.peer_token(peer_id)
    capabilities = _json_request(peer["base_url"] + "/federation/v1/personnel/time/capabilities", token=token, timeout=10)
    if capabilities.get("resource") != "personnel_time" or int(capabilities.get("schema", 0)) != 1:
        raise ValueError("Peer unterstützt keinen kompatiblen Stempeluhr-Austausch")
    query = urllib.parse.urlencode({"employee_key": remote_key, "start": start.isoformat(), "end": end.isoformat()})
    data = _json_request(peer["base_url"] + "/federation/v1/personnel/time/punches?" + query, token=token, timeout=20)
    if data.get("employee_key") != remote_key or data.get("complete_range") is not True:
        raise ValueError("Federation-Antwort deckt den angeforderten Zeitraum nicht vollständig ab")
    actor = _actor_for_mapping(mapping)
    g.user = actor
    return insights.import_federated_events(
        peer_id,
        remote_key,
        local_employee_id,
        data.get("events") or [],
        start,
        end,
        remote_label=str(data.get("employee_label") or mapping.get("remote_label") or remote_key),
    )


def _finish_mapping(mapping: dict[str, Any], status: str, error: str = "") -> None:
    db = get_db()
    now = utc_now()
    if status == "success":
        db.execute(
            """UPDATE employee_time_federation_map SET last_success_at=?,last_sync_status='success',last_sync_error=''
               WHERE peer_id=? AND remote_employee_key=?""",
            (now, mapping["peer_id"], mapping["remote_employee_key"]),
        )
    else:
        db.execute(
            """UPDATE employee_time_federation_map SET last_sync_status='error',last_sync_error=?
               WHERE peer_id=? AND remote_employee_key=?""",
            (error[:1000], mapping["peer_id"], mapping["remote_employee_key"]),
        )
    db.commit()


def run_auto_sync_once(*, force: bool = False, peer_id: str = "", remote_key: str = "") -> dict[str, Any]:
    ensure_schema()
    now = datetime.now(timezone.utc)
    query = "SELECT * FROM employee_time_federation_map"
    params: list[Any] = []
    clauses = []
    if not force:
        clauses.append("auto_sync=1")
    if peer_id:
        clauses.append("peer_id=?")
        params.append(sanitize_peer_id(peer_id))
    if remote_key:
        clauses.append("remote_employee_key=?")
        params.append(remote_key)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    rows = [dict(row) for row in get_db().execute(query, params).fetchall()]
    summary = {"checked": len(rows), "synced": 0, "skipped": 0, "errors": 0, "inserted": 0, "updated": 0, "removed": 0, "unchanged": 0, "details": []}
    for row in rows:
        claimed = _claim_mapping(str(row["peer_id"]), str(row["remote_employee_key"]), force=force, now=now)
        if claimed is None:
            summary["skipped"] += 1
            continue
        try:
            result = _sync_mapping(claimed)
            _finish_mapping(claimed, "success")
            summary["synced"] += 1
            for key in ("inserted", "updated", "removed", "unchanged"):
                summary[key] += int(result.get(key, 0))
            summary["details"].append({"peer_id": claimed["peer_id"], "remote_employee_key": claimed["remote_employee_key"], "status": "success", **result})
            try:
                FederationStore(current_app.config["DOCUMENT_ROOT"]).record_event(
                    "personnel_time_auto_synced",
                    peer_id=str(claimed["peer_id"]),
                    detail={"employee_id": int(claimed["local_employee_id"]), **result},
                )
            except OSError:
                current_app.logger.warning("personnel_time_auto_sync_event_log_failed peer=%s", claimed["peer_id"])
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            _finish_mapping(claimed, "error", message)
            summary["errors"] += 1
            summary["details"].append({"peer_id": claimed["peer_id"], "remote_employee_key": claimed["remote_employee_key"], "status": "error", "error": message})
            current_app.logger.warning("personnel_time_auto_sync_failed peer=%s error=%s", claimed["peer_id"], type(exc).__name__)
    return summary


def configured_poll_seconds() -> int:
    try:
        value = int(os.environ.get("SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC_SECONDS", "300"))
    except ValueError:
        value = 300
    return max(60, min(value, 3600))


def auto_sync_runtime_enabled() -> bool:
    return os.environ.get("SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC", "1").strip().casefold() not in {"0", "false", "no", "off"}


def _worker(app) -> None:
    while True:
        with app.app_context():
            try:
                result = run_auto_sync_once()
                if result["synced"] or result["errors"]:
                    app.logger.info(
                        "personnel_time_auto_sync synced=%s skipped=%s errors=%s",
                        result["synced"], result["skipped"], result["errors"],
                    )
            except Exception as exc:
                app.logger.warning("personnel_time_auto_sync_worker_failed error=%s", type(exc).__name__)
        time_module.sleep(configured_poll_seconds())


@bp.get("")
@login_required
def index():
    _require_admin()
    from .personnel_time_clock import ensure_admin_time_account
    ensure_admin_time_account()
    ensure_schema()
    insights = _insights()
    employees = insights._employees()
    if not employees:
        from flask import abort
        abort(404)
    try:
        selected_id = int(request.args.get("employee_id", "") or employees[0]["id"])
        selected = insights._selected_employee(selected_id)
        period, start, end = _period()
    except (TypeError, ValueError) as exc:
        flash(str(exc))
        selected = employees[0]
        today = personnel._local_now().date()
        period, start, end = "30d", today - timedelta(days=29), today
    selected_stats = insights.period_statistics(selected, start, end)
    team = team_statistics(start, end)
    return render_template(
        "personnel/time_analytics.html",
        employees=employees,
        selected=selected,
        period=period,
        start=start,
        end=end,
        selected_stats=selected_stats,
        selected_weekly=_bucket_series(selected_stats["daily"], "week"),
        selected_monthly=_bucket_series(selected_stats["daily"], "month"),
        team=team,
        team_weekly=_bucket_series(team["daily"], "week"),
        team_monthly=_bucket_series(team["daily"], "month"),
        sites=site_statistics(start, end),
        mappings=_mapping_rows(),
        worker_enabled=auto_sync_runtime_enabled(),
        poll_seconds=configured_poll_seconds(),
    )


@bp.post("/autosync/settings")
@login_required
def save_auto_sync():
    _require_admin()
    ensure_schema()
    try:
        peer_id = sanitize_peer_id(str(request.form.get("peer_id", "")))
        remote_key = str(request.form.get("remote_employee_key", "")).strip()[:160]
        interval = int(request.form.get("sync_interval_minutes", "60"))
        window = int(request.form.get("sync_window_days", "14"))
        if not remote_key:
            raise ValueError("Remote-Mitarbeiter fehlt")
        if not MIN_SYNC_INTERVAL_MINUTES <= interval <= MAX_SYNC_INTERVAL_MINUTES:
            raise ValueError("Sync-Intervall muss zwischen 15 Minuten und 24 Stunden liegen")
        if not 1 <= window <= MAX_SYNC_WINDOW_DAYS:
            raise ValueError("Sync-Fenster muss zwischen 1 und 92 Tagen liegen")
        enabled = request.form.get("auto_sync") == "1"
        db = get_db()
        row = db.execute(
            "SELECT 1 FROM employee_time_federation_map WHERE peer_id=? AND remote_employee_key=?",
            (peer_id, remote_key),
        ).fetchone()
        if row is None:
            raise ValueError("Gespeicherte Federation-Zuordnung wurde nicht gefunden")
        db.execute(
            """UPDATE employee_time_federation_map SET auto_sync=?,sync_interval_minutes=?,sync_window_days=?,
               last_attempt_at=CASE WHEN auto_sync<>? THEN '' ELSE last_attempt_at END,
               updated_at=?,updated_by=? WHERE peer_id=? AND remote_employee_key=?""",
            (1 if enabled else 0, interval, window, 1 if enabled else 0, utc_now(), int(g.user["id"]), peer_id, remote_key),
        )
        db.commit()
        flash("Automatische Federation-Synchronisation gespeichert.")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("personnel_time_analytics.index", employee_id=request.form.get("employee_id", ""), period=request.form.get("period", "30d")))


@bp.post("/autosync/run")
@login_required
def sync_now():
    _require_admin()
    try:
        peer_id = sanitize_peer_id(str(request.form.get("peer_id", "")))
        remote_key = str(request.form.get("remote_employee_key", "")).strip()[:160]
        result = run_auto_sync_once(force=True, peer_id=peer_id, remote_key=remote_key)
        if result["errors"]:
            error = next((item.get("error") for item in result["details"] if item.get("status") == "error"), "Synchronisation fehlgeschlagen")
            flash(f"Federation-Sync fehlgeschlagen: {error}")
        elif result["synced"]:
            flash(
                f"Federation-Sync abgeschlossen: {result['inserted']} neu, {result['updated']} aktualisiert, "
                f"{result['removed']} entfernt, {result['unchanged']} unverändert."
            )
        else:
            flash("Keine passende Federation-Zuordnung gefunden.")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("personnel_time_analytics.index", employee_id=request.form.get("employee_id", ""), period=request.form.get("period", "30d")))


@click.command("personnel-time-sync")
@click.option("--force", is_flag=True, help="Run all mappings immediately, including mappings without automatic sync.")
def sync_command(force: bool) -> None:
    result = run_auto_sync_once(force=force)
    click.echo(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["errors"]:
        raise click.ClickException("one or more personnel-time federation mappings failed")


def init_app(app) -> None:
    global _WORKER_STARTED
    if bp.name not in app.blueprints:
        app.register_blueprint(bp)
    if "personnel-time-sync" not in app.cli.commands:
        app.cli.add_command(sync_command)
    if app.testing or not auto_sync_runtime_enabled():
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        _WORKER_STARTED = True
        thread = threading.Thread(target=_worker, args=(app,), daemon=True, name="personnel-time-federation-sync")
        thread.start()
