"""Personnel time statistics, cross-module comparison and bounded federation import."""
from __future__ import annotations

import json
import urllib.parse
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from flask import Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from . import personnel
from .auth import login_required
from .db import get_db
from .document_store import utc_now
from .federation_core import sanitize_peer_id
from .federation_http import _authorized as federation_authorized
from .federation_store import FederationStore
from .federation_worker import _json_request
from .project_store import ProjectStore
from .todo_store import TodoStore


bp = Blueprint("personnel_time_insights", __name__, url_prefix="/personnel/time-admin/insights")
federation_bp = Blueprint("personnel_time_federation", __name__, url_prefix="/federation/v1/personnel/time")

_ALLOWED = {
    "clock_out": {"clock_in"},
    "clock_in": {"break_start", "clock_out"},
    "break_start": {"break_end"},
    "break_end": {"break_start", "clock_out"},
}
PERIODS = {"7d": 7, "30d": 30, "90d": 90}
MAX_FEDERATION_DAYS = 93
MAX_FEDERATION_EVENTS = 2000


def ensure_schema() -> None:
    """Extend the canonical punch table; existing local punches remain untouched."""
    personnel._ensure()
    db = get_db()
    columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(employee_punch)").fetchall()}
    additions = {
        "source_kind": "TEXT NOT NULL DEFAULT 'local'",
        "source_peer": "TEXT NOT NULL DEFAULT ''",
        "source_ref": "TEXT NOT NULL DEFAULT ''",
        "source_imported_at": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE employee_punch ADD COLUMN {name} {definition}")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS employee_time_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          employee_id INTEGER NOT NULL,
          punch_id INTEGER,
          action TEXT NOT NULL,
          before_json TEXT NOT NULL DEFAULT '{}',
          after_json TEXT NOT NULL DEFAULT '{}',
          reason TEXT NOT NULL DEFAULT '',
          actor_user_id INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(employee_id) REFERENCES employee(id),
          FOREIGN KEY(actor_user_id) REFERENCES user(id)
        );
        CREATE INDEX IF NOT EXISTS employee_time_audit_employee_time
          ON employee_time_audit(employee_id, created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS employee_punch_federation_source
          ON employee_punch(source_peer,source_ref)
          WHERE source_kind='federation' AND source_ref<>'';
        CREATE TABLE IF NOT EXISTS employee_time_federation_setting (
          id INTEGER PRIMARY KEY CHECK(id=1),
          export_enabled INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT '',
          updated_by INTEGER,
          FOREIGN KEY(updated_by) REFERENCES user(id)
        );
        CREATE TABLE IF NOT EXISTS employee_time_federation_map (
          peer_id TEXT NOT NULL,
          remote_employee_key TEXT NOT NULL,
          local_employee_id INTEGER NOT NULL,
          remote_label TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL,
          updated_by INTEGER NOT NULL,
          PRIMARY KEY(peer_id,remote_employee_key),
          FOREIGN KEY(local_employee_id) REFERENCES employee(id),
          FOREIGN KEY(updated_by) REFERENCES user(id)
        );
        """
    )
    db.execute(
        "INSERT OR IGNORE INTO employee_time_federation_setting(id,export_enabled,updated_at) VALUES(1,0,?)",
        (utc_now(),),
    )
    db.commit()


def _require_admin() -> None:
    if getattr(g, "user", None) is None or not g.user["is_admin"]:
        abort(403)


def _root():
    return current_app.config["DOCUMENT_ROOT"]


def _federation_export_enabled() -> bool:
    ensure_schema()
    row = get_db().execute("SELECT export_enabled FROM employee_time_federation_setting WHERE id=1").fetchone()
    return bool(row and row["export_enabled"])


def _set_federation_export(enabled: bool) -> None:
    ensure_schema()
    get_db().execute(
        "UPDATE employee_time_federation_setting SET export_enabled=?,updated_at=?,updated_by=? WHERE id=1",
        (1 if enabled else 0, utc_now(), int(g.user["id"])),
    )
    get_db().commit()
    FederationStore(_root()).record_event(
        "personnel_time_export_enabled" if enabled else "personnel_time_export_disabled",
        detail={"actor": str(g.user["username"])},
    )


def _employees() -> list[dict[str, Any]]:
    names = personnel._employee_names()
    rows = get_db().execute(
        """SELECT employee.*,user.username,user.display_name
           FROM employee LEFT JOIN user ON user.id=employee.user_id
           WHERE employee.active=1 ORDER BY employee.id"""
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["name"] = names.get(int(row["id"]), str(row["display_name"] or row["username"] or f"Mitarbeiter {row['id']}"))
        result.append(item)
    return result


def _selected_employee(employee_id: int) -> dict[str, Any]:
    row = next((item for item in _employees() if int(item["id"]) == int(employee_id)), None)
    if row is None:
        raise ValueError("Unbekannter Mitarbeiter")
    return row


def _period() -> tuple[str, date, date]:
    today = personnel._local_now().date()
    raw = str(request.values.get("period", "30d"))
    try:
        anchor = min(date.fromisoformat(str(request.values.get("anchor", today.isoformat()))), today)
    except ValueError:
        anchor = today
    if raw in PERIODS:
        days = PERIODS[raw]
        return raw, anchor - timedelta(days=days - 1), anchor
    if raw == "month":
        start = anchor.replace(day=1)
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return raw, start, min(following - timedelta(days=1), today)
    if raw == "custom":
        try:
            start = date.fromisoformat(str(request.values.get("start", "")))
            end = date.fromisoformat(str(request.values.get("end", "")))
        except ValueError as exc:
            raise ValueError("Individueller Zeitraum ist ungültig") from exc
        end = min(end, today)
        if end < start or (end - start).days > 365:
            raise ValueError("Zeitraum muss zwischen 1 und 366 Tagen liegen")
        return raw, start, end
    return "30d", today - timedelta(days=29), today


def _utc_bounds(start: date, end: date) -> tuple[str, str]:
    zone = personnel._personnel_timezone()
    lower = datetime.combine(start, time.min, zone).astimezone(timezone.utc).isoformat(timespec="seconds")
    upper = datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(timezone.utc).isoformat(timespec="seconds")
    return lower, upper


def _absences(employee_id: int, start: date, end: date) -> list[dict[str, Any]]:
    rows = get_db().execute(
        """SELECT kind,starts_on,ends_on,status FROM employee_absence
           WHERE employee_id=? AND status IN ('approved','reported') AND starts_on<=? AND ends_on>=?
           ORDER BY starts_on""",
        (employee_id, end.isoformat(), start.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _absence_for(rows: list[dict[str, Any]], shown: date) -> dict[str, Any] | None:
    value = shown.isoformat()
    return next((row for row in rows if row["starts_on"] <= value <= row["ends_on"]), None)


def _first_clock_in(employee_id: int, shown: date) -> datetime | None:
    zone = personnel._personnel_timezone()
    for row in personnel._day_punches(employee_id, shown):
        if row["action"] == "clock_in":
            return datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00")).astimezone(zone)
    return None


def period_statistics(employee: dict[str, Any], start: date, end: date) -> dict[str, Any]:
    ensure_schema()
    employee_id = int(employee["id"])
    schedule = json.loads(employee.get("schedule_json") or "{}")
    absence_rows = _absences(employee_id, start, end)
    today = personnel._local_now().date()
    daily: list[dict[str, Any]] = []
    totals = {
        "work_minutes": 0,
        "break_minutes": 0,
        "contract_minutes": 0,
        "presence_target_minutes": 0,
        "absence_minutes": 0,
        "days_with_work": 0,
        "absence_days": 0,
        "missing_days": 0,
        "open_days": 0,
        "missing_break_days": 0,
        "late_days": 0,
        "over_8h_days": 0,
        "over_9h_days": 0,
        "max_day_minutes": 0,
    }
    current = start
    while current <= end:
        summary = personnel._day_summary(employee_id, current)
        scheduled = round(float((schedule.get(str(current.weekday())) or {}).get("hours", 0) or 0) * 60)
        absence = _absence_for(absence_rows, current)
        expected = 0 if absence else scheduled
        actual = int(summary["work_minutes"])
        required = int(summary["required_break"])
        late_minutes = 0
        plan = schedule.get(str(current.weekday())) or {}
        first = _first_clock_in(employee_id, current)
        if expected and first and not absence:
            try:
                scheduled_start = datetime.combine(current, time.fromisoformat(str(plan.get("start", "08:00"))), personnel._personnel_timezone())
                late_minutes = max(0, int((first - scheduled_start).total_seconds() // 60))
            except ValueError:
                late_minutes = 0
        past_or_today = current <= today
        row = {
            "date": current,
            "work_minutes": actual,
            "break_minutes": int(summary["break_minutes"]),
            "required_break": required,
            "scheduled_minutes": scheduled,
            "presence_target_minutes": expected,
            "balance_minutes": actual - expected,
            "absence": absence,
            "open": bool(summary["open"]),
            "break_compliant": bool(summary["compliant"]),
            "late_minutes": late_minutes,
        }
        daily.append(row)
        totals["work_minutes"] += actual
        totals["break_minutes"] += int(summary["break_minutes"])
        totals["contract_minutes"] += scheduled
        totals["presence_target_minutes"] += expected
        totals["absence_minutes"] += scheduled if absence else 0
        totals["days_with_work"] += int(actual > 0)
        totals["absence_days"] += int(bool(absence and scheduled))
        totals["missing_days"] += int(past_or_today and expected > 0 and actual == 0)
        totals["open_days"] += int(bool(summary["open"]))
        totals["missing_break_days"] += int(past_or_today and actual > 6 * 60 and int(summary["break_minutes"]) < required)
        totals["late_days"] += int(past_or_today and late_minutes > 5)
        totals["over_8h_days"] += int(actual > 8 * 60)
        totals["over_9h_days"] += int(actual > 9 * 60)
        totals["max_day_minutes"] = max(totals["max_day_minutes"], actual)
        current += timedelta(days=1)
    totals["balance_minutes"] = totals["work_minutes"] - totals["presence_target_minutes"]
    completed_work_days = max(1, totals["days_with_work"])
    totals["average_day_minutes"] = round(totals["work_minutes"] / completed_work_days)
    compliant_days = max(0, totals["days_with_work"] - totals["missing_break_days"])
    totals["break_compliance_percent"] = round(compliant_days * 100 / completed_work_days)
    lower, upper = _utc_bounds(start, end)
    source_rows = get_db().execute(
        """SELECT source_kind,source_peer,COUNT(*) AS count FROM employee_punch
           WHERE employee_id=? AND occurred_at>=? AND occurred_at<?
           GROUP BY source_kind,source_peer ORDER BY count DESC""",
        (employee_id, lower, upper),
    ).fetchall()
    sources = [
        {"kind": str(row["source_kind"] or "local"), "peer": str(row["source_peer"] or ""), "count": int(row["count"])}
        for row in source_rows
    ]
    return {"start": start, "end": end, "daily": list(reversed(daily)), "totals": totals, "sources": sources}


def cross_area_statistics(employee: dict[str, Any], start: date, end: date) -> dict[str, Any]:
    username = str(employee.get("username") or "").strip()
    if not username:
        return {"total_minutes": 0, "project_minutes": 0, "task_minutes": 0, "entries": 0, "error": "Kein Benutzerkonto verknüpft"}
    seen: set[str] = set()
    project_minutes = task_minutes = entries = 0
    try:
        for project in ProjectStore(_root()).projects():
            for task in project.get("tasks", []):
                for entry in task.get("time_entries", []):
                    entry_id = str(entry.get("entry_id") or "")
                    if entry.get("created_by") != username or not entry_id or entry_id in seen:
                        continue
                    try:
                        booked = date.fromisoformat(str(entry.get("date") or ""))
                    except ValueError:
                        continue
                    if start <= booked <= end:
                        seen.add(entry_id)
                        project_minutes += max(0, int(entry.get("minutes") or 0))
                        entries += 1
        for item in TodoStore(_root()).items(username):
            for entry in item.get("time_entries", []):
                entry_id = str(entry.get("entry_id") or "")
                if entry.get("created_by") != username or not entry_id or entry_id in seen:
                    continue
                try:
                    booked = date.fromisoformat(str(entry.get("date") or ""))
                except ValueError:
                    continue
                if start <= booked <= end:
                    seen.add(entry_id)
                    minutes = max(0, int(entry.get("minutes") or 0))
                    if item.get("project_id"):
                        project_minutes += minutes
                    else:
                        task_minutes += minutes
                    entries += 1
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"total_minutes": project_minutes + task_minutes, "project_minutes": project_minutes, "task_minutes": task_minutes, "entries": entries, "error": "Fachzeiten konnten nur teilweise gelesen werden"}
    return {"total_minutes": project_minutes + task_minutes, "project_minutes": project_minutes, "task_minutes": task_minutes, "entries": entries, "error": ""}


def _mappings(local_employee_id: int) -> list[dict[str, Any]]:
    rows = get_db().execute(
        "SELECT * FROM employee_time_federation_map WHERE local_employee_id=? ORDER BY peer_id,remote_label",
        (local_employee_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _enabled_peers() -> list[dict[str, Any]]:
    return [peer for peer in FederationStore(_root()).list_peers() if peer.get("enabled")]


def _remote_employees(peer_id: str) -> tuple[list[dict[str, Any]], str]:
    if not peer_id:
        return [], ""
    try:
        peer_id = sanitize_peer_id(str(peer_id))
        store = FederationStore(_root())
        peer = store.get_peer(peer_id)
        if not peer or not peer.get("enabled"):
            raise ValueError("Federation-Peer ist nicht aktiv")
        token = store.peer_token(peer_id)
        capabilities = _json_request(peer["base_url"] + "/federation/v1/personnel/time/capabilities", token=token, timeout=10)
        if capabilities.get("resource") != "personnel_time" or int(capabilities.get("schema", 0)) != 1:
            raise ValueError("Peer unterstützt keinen kompatiblen Stempeluhr-Austausch")
        data = _json_request(peer["base_url"] + "/federation/v1/personnel/time/employees", token=token, timeout=10)
        rows = data.get("employees") or []
        if not isinstance(rows, list):
            raise ValueError("Peer lieferte keine gültige Mitarbeiterliste")
        result = []
        for row in rows[:1000]:
            if not isinstance(row, dict):
                continue
            key = str(row.get("employee_key") or "")[:160]
            label = str(row.get("label") or key)[:200]
            if key:
                result.append({"employee_key": key, "label": label})
        return result, ""
    except Exception as exc:
        current_app.logger.warning("personnel_time_remote_employees_failed peer=%s error=%s", peer_id, type(exc).__name__)
        return [], "Remote Stempeluhr konnte nicht gelesen werden. Peer, Token und Freigabe prüfen."


def _validate_sequence(employee_id: int, shown: date) -> str:
    state = "clock_out"
    for row in personnel._day_punches(employee_id, shown):
        action = str(row["action"])
        if action not in _ALLOWED.get(state, set()):
            return "Import würde eine ungültige Stempelfolge erzeugen"
        state = action
    if int(personnel._day_summary(employee_id, shown)["work_minutes"]) > 10 * 60:
        return "Import würde die 10-Stunden-Grenze überschreiten"
    return ""


def _audit_import(employee_id: int, punch_id: int, action: str, before: dict[str, Any], after: dict[str, Any], peer_id: str) -> None:
    get_db().execute(
        """INSERT INTO employee_time_audit(
               employee_id,punch_id,action,before_json,after_json,reason,actor_user_id,created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            employee_id,
            punch_id,
            action,
            json.dumps(before, ensure_ascii=False, sort_keys=True),
            json.dumps(after, ensure_ascii=False, sort_keys=True),
            f"Federation-Import von {peer_id}",
            int(g.user["id"]),
            utc_now(),
        ),
    )


def _punch_payload(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "employee_id": int(row["employee_id"]),
        "action": str(row["action"]),
        "occurred_at": str(row["occurred_at"]),
        "source_kind": str(row["source_kind"] or "local"),
        "source_peer": str(row["source_peer"] or ""),
        "source_ref": str(row["source_ref"] or ""),
    }


def _row_local_date(row: Any) -> date:
    return datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00")).astimezone(personnel._personnel_timezone()).date()


def import_federated_events(peer_id: str, remote_key: str, local_employee_id: int, events: list[Any], start: date, end: date) -> dict[str, int]:
    ensure_schema()
    peer_id = sanitize_peer_id(str(peer_id))
    remote_key = str(remote_key or "").strip()[:160]
    if not remote_key or not isinstance(events, list) or len(events) > MAX_FEDERATION_EVENTS:
        raise ValueError("Ungültige Federation-Stempeldaten")
    employee = get_db().execute("SELECT * FROM employee WHERE id=? AND active=1", (local_employee_id,)).fetchone()
    if employee is None:
        raise ValueError("Lokaler Mitarbeiter ist nicht aktiv")
    if end < start or (end - start).days >= MAX_FEDERATION_DAYS:
        raise ValueError("Federation-Import ist auf 93 Tage begrenzt")
    db = get_db()
    db.execute("SAVEPOINT personnel_time_federation")
    affected: set[date] = set()
    incoming_refs: set[str] = set()
    result = {"inserted": 0, "updated": 0, "removed": 0, "unchanged": 0}
    try:
        for raw in events:
            if not isinstance(raw, dict):
                raise ValueError("Ungültiger Federation-Stempel")
            event_key = str(raw.get("event_key") or "").strip()[:160]
            action = str(raw.get("action") or "")
            stamp_text = str(raw.get("occurred_at") or "")
            if not event_key or action not in personnel.PUNCH_ACTIONS:
                raise ValueError("Federation-Stempel enthält unbekannte Felder")
            source_ref = remote_key + ":" + event_key
            if source_ref in incoming_refs:
                raise ValueError("Federation-Antwort enthält doppelte Ereignis-IDs")
            incoming_refs.add(source_ref)
            try:
                stamp = datetime.fromisoformat(stamp_text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Federation-Stempel enthält ungültige Zeit") from exc
            if stamp.tzinfo is None:
                raise ValueError("Federation-Stempel benötigt Zeitzone")
            local = stamp.astimezone(personnel._personnel_timezone())
            shown = local.date()
            if not start <= shown <= end:
                raise ValueError("Federation-Stempel liegt außerhalb des angeforderten Zeitraums")
            if stamp.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise ValueError("Federation-Stempel liegt in der Zukunft")
            if personnel.month_is_closed(local_employee_id, shown.strftime("%Y-%m")):
                raise ValueError(f"Monat {shown.strftime('%Y-%m')} ist bereits festgeschrieben")
            existing = db.execute(
                "SELECT * FROM employee_punch WHERE source_kind='federation' AND source_peer=? AND source_ref=?",
                (peer_id, source_ref),
            ).fetchone()
            normalized_stamp = stamp.astimezone(timezone.utc).isoformat(timespec="seconds")
            if existing:
                if int(existing["employee_id"]) != local_employee_id:
                    raise ValueError("Federation-Stempel ist bereits einem anderen Mitarbeiter zugeordnet")
                previous_day = _row_local_date(existing)
                affected.add(previous_day)
                before = _punch_payload(existing)
                if str(existing["action"]) == action and str(existing["occurred_at"]) == normalized_stamp:
                    result["unchanged"] += 1
                else:
                    db.execute(
                        "UPDATE employee_punch SET action=?,occurred_at=?,source_imported_at=? WHERE id=?",
                        (action, normalized_stamp, utc_now(), int(existing["id"])),
                    )
                    updated = db.execute("SELECT * FROM employee_punch WHERE id=?", (int(existing["id"]),)).fetchone()
                    _audit_import(local_employee_id, int(existing["id"]), "federation_punch_updated", before, _punch_payload(updated), peer_id)
                    result["updated"] += 1
            else:
                cursor = db.execute(
                    """INSERT INTO employee_punch(
                           employee_id,action,occurred_at,recorded_by,source_kind,source_peer,source_ref,source_imported_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (local_employee_id, action, normalized_stamp, int(g.user["id"]), "federation", peer_id, source_ref, utc_now()),
                )
                created = db.execute("SELECT * FROM employee_punch WHERE id=?", (int(cursor.lastrowid),)).fetchone()
                _audit_import(local_employee_id, int(created["id"]), "federation_punch_imported", {}, _punch_payload(created), peer_id)
                result["inserted"] += 1
            affected.add(shown)

        lower, upper = _utc_bounds(start, end)
        existing_range = db.execute(
            """SELECT * FROM employee_punch
               WHERE employee_id=? AND source_kind='federation' AND source_peer=?
                 AND occurred_at>=? AND occurred_at<? ORDER BY occurred_at,id""",
            (local_employee_id, peer_id, lower, upper),
        ).fetchall()
        prefix = remote_key + ":"
        for existing in existing_range:
            source_ref = str(existing["source_ref"] or "")
            if not source_ref.startswith(prefix) or source_ref in incoming_refs:
                continue
            shown = _row_local_date(existing)
            if personnel.month_is_closed(local_employee_id, shown.strftime("%Y-%m")):
                raise ValueError(f"Monat {shown.strftime('%Y-%m')} ist bereits festgeschrieben")
            before = _punch_payload(existing)
            _audit_import(local_employee_id, int(existing["id"]), "federation_punch_removed", before, {}, peer_id)
            db.execute("DELETE FROM employee_punch WHERE id=?", (int(existing["id"]),))
            affected.add(shown)
            result["removed"] += 1

        for shown in affected:
            error = _validate_sequence(local_employee_id, shown)
            if error:
                raise ValueError(error + f" ({shown.isoformat()})")
        schedule = json.loads(employee["schedule_json"] or "{}")
        for shown in affected:
            if personnel._punch_state(local_employee_id, shown) == "clock_out":
                personnel._update_flex_day(local_employee_id, shown, schedule)
            else:
                db.execute("DELETE FROM employee_flex_day WHERE employee_id=? AND work_date=?", (local_employee_id, shown.isoformat()))
        db.execute("RELEASE SAVEPOINT personnel_time_federation")
        db.commit()
        return result
    except Exception:
        db.execute("ROLLBACK TO SAVEPOINT personnel_time_federation")
        db.execute("RELEASE SAVEPOINT personnel_time_federation")
        raise


@bp.get("")
@login_required
def index():
    _require_admin()
    from .personnel_time_clock import ensure_admin_time_account
    ensure_admin_time_account()
    ensure_schema()
    employees = _employees()
    if not employees:
        abort(404)
    try:
        selected_id = int(request.args.get("employee_id", "") or employees[0]["id"])
        selected = _selected_employee(selected_id)
        period, start, end = _period()
    except (TypeError, ValueError) as exc:
        flash(str(exc))
        selected = employees[0]
        period, start, end = "30d", personnel._local_now().date() - timedelta(days=29), personnel._local_now().date()
    stats = period_statistics(selected, start, end)
    areas = cross_area_statistics(selected, start, end)
    peer_id = str(request.args.get("federation_peer", "")).strip()
    remote_employees, remote_error = _remote_employees(peer_id)
    return render_template(
        "personnel/time_insights.html",
        employees=employees,
        selected=selected,
        period=period,
        start=start,
        end=end,
        stats=stats,
        areas=areas,
        peers=_enabled_peers(),
        federation_peer=peer_id,
        remote_employees=remote_employees,
        remote_error=remote_error,
        mappings=_mappings(int(selected["id"])),
        federation_export_enabled=_federation_export_enabled(),
    )


@bp.post("/federation/export")
@login_required
def set_federation_export():
    _require_admin()
    enabled = request.form.get("enabled") == "1"
    _set_federation_export(enabled)
    flash("Federation-Export der Stempeluhr aktiviert." if enabled else "Federation-Export der Stempeluhr deaktiviert.")
    return redirect(url_for("personnel_time_insights.index", employee_id=request.form.get("employee_id", "")))


@bp.post("/federation/import")
@login_required
def federation_import():
    _require_admin()
    ensure_schema()
    try:
        peer_id = sanitize_peer_id(str(request.form.get("peer_id", "")))
        remote_key = str(request.form.get("remote_employee_key", "")).strip()[:160]
        local_employee_id = int(request.form.get("employee_id", "0"))
        start = date.fromisoformat(str(request.form.get("start", "")))
        end = date.fromisoformat(str(request.form.get("end", "")))
        if end < start or (end - start).days >= MAX_FEDERATION_DAYS:
            raise ValueError("Federation-Import muss zwischen 1 und 93 Tagen liegen")
        store = FederationStore(_root())
        peer = store.get_peer(peer_id)
        if not peer or not peer.get("enabled"):
            raise ValueError("Federation-Peer ist nicht aktiv")
        token = store.peer_token(peer_id)
        query = urllib.parse.urlencode({"employee_key": remote_key, "start": start.isoformat(), "end": end.isoformat()})
        data = _json_request(peer["base_url"] + "/federation/v1/personnel/time/punches?" + query, token=token, timeout=20)
        if data.get("employee_key") != remote_key or data.get("complete_range") is not True:
            raise ValueError("Federation-Antwort deckt den angeforderten Zeitraum nicht vollständig ab")
        result = import_federated_events(peer_id, remote_key, local_employee_id, data.get("events") or [], start, end)
        remote_label = str(data.get("employee_label") or remote_key)[:200]
        get_db().execute(
            """INSERT INTO employee_time_federation_map(peer_id,remote_employee_key,local_employee_id,remote_label,updated_at,updated_by)
               VALUES(?,?,?,?,?,?) ON CONFLICT(peer_id,remote_employee_key) DO UPDATE SET
               local_employee_id=excluded.local_employee_id,remote_label=excluded.remote_label,
               updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            (peer_id, remote_key, local_employee_id, remote_label, utc_now(), int(g.user["id"])),
        )
        get_db().commit()
        store.record_event("personnel_time_imported", peer_id=peer_id, detail={"employee_id": local_employee_id, **result})
        flash(
            f"Federation-Zeiten synchronisiert: {result['inserted']} neu, {result['updated']} aktualisiert, "
            f"{result['removed']} entfernt, {result['unchanged']} unverändert."
        )
        return redirect(url_for("personnel_time_insights.index", employee_id=local_employee_id, period="custom", start=start.isoformat(), end=end.isoformat(), federation_peer=peer_id))
    except Exception as exc:
        current_app.logger.warning("personnel_time_federation_import_failed error=%s", type(exc).__name__)
        flash(str(exc) if isinstance(exc, ValueError) else "Federation-Import fehlgeschlagen. Peer, Token und Daten prüfen.")
        return redirect(url_for("personnel_time_insights.index", employee_id=request.form.get("employee_id", "")))


@federation_bp.before_request
def authenticate_federation():
    if not federation_authorized():
        return Response(
            "federation authentication required\n",
            401,
            {"WWW-Authenticate": 'Bearer realm="SimpleOffice4Me Federation"', "Cache-Control": "no-store"},
        )
    return None


def _require_export():
    if _federation_export_enabled():
        return None
    return jsonify({"error": "personnel_time_export_disabled"}), 403


@federation_bp.get("/capabilities")
def federation_capabilities():
    disabled = _require_export()
    if disabled is not None:
        return disabled
    return jsonify({
        "resource": "personnel_time",
        "schema": 1,
        "employees": True,
        "punches": True,
        "max_days": MAX_FEDERATION_DAYS,
        "complete_range": True,
        "reexports_federated_events": False,
    })


@federation_bp.get("/employees")
def federation_employees():
    disabled = _require_export()
    if disabled is not None:
        return disabled
    ensure_schema()
    names = personnel._employee_names()
    rows = get_db().execute("SELECT id,contact_id FROM employee WHERE active=1 ORDER BY id").fetchall()
    return jsonify({
        "schema": 1,
        "employees": [
            {"employee_key": str(row["contact_id"]), "label": names.get(int(row["id"]), f"Mitarbeiter {row['id']}")}
            for row in rows
        ],
    })


@federation_bp.get("/punches")
def federation_punches():
    disabled = _require_export()
    if disabled is not None:
        return disabled
    ensure_schema()
    employee_key = str(request.args.get("employee_key", "")).strip()[:160]
    row = get_db().execute("SELECT id,contact_id FROM employee WHERE contact_id=? AND active=1", (employee_key,)).fetchone()
    if row is None:
        return jsonify({"error": "employee_not_found"}), 404
    try:
        start = date.fromisoformat(str(request.args.get("start", "")))
        end = date.fromisoformat(str(request.args.get("end", "")))
    except ValueError:
        return jsonify({"error": "invalid_range"}), 400
    if end < start or (end - start).days >= MAX_FEDERATION_DAYS:
        return jsonify({"error": "range_too_large", "max_days": MAX_FEDERATION_DAYS}), 400
    lower, upper = _utc_bounds(start, end)
    punches = get_db().execute(
        """SELECT id,action,occurred_at FROM employee_punch
           WHERE employee_id=? AND occurred_at>=? AND occurred_at<? AND source_kind<>'federation'
           ORDER BY occurred_at,id LIMIT ?""",
        (int(row["id"]), lower, upper, MAX_FEDERATION_EVENTS + 1),
    ).fetchall()
    if len(punches) > MAX_FEDERATION_EVENTS:
        return jsonify({"error": "too_many_events", "max_events": MAX_FEDERATION_EVENTS}), 409
    names = personnel._employee_names()
    return jsonify({
        "schema": 1,
        "employee_key": employee_key,
        "employee_label": names.get(int(row["id"]), f"Mitarbeiter {row['id']}"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "complete_range": True,
        "events": [
            {"event_key": f"punch:{int(item['id'])}", "action": str(item["action"]), "occurred_at": str(item["occurred_at"])}
            for item in punches
        ],
    })
