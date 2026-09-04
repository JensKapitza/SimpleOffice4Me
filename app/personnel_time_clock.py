"""Admin/owner time clock and audited personnel time corrections."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import personnel
from .auth import login_required
from .contact_store import ContactStore
from .db import get_db
from .document_store import utc_now


bp = Blueprint("personnel_time", __name__, url_prefix="/personnel/time-admin")
_ALLOWED = {
    "clock_out": {"clock_in"},
    "clock_in": {"break_start", "clock_out"},
    "break_start": {"break_end"},
    "break_end": {"break_start", "clock_out"},
}
_ACTION_LABELS = {
    "clock_in": "Kommen",
    "break_start": "Pause beginnen",
    "break_end": "Pause beenden",
    "clock_out": "Gehen",
}


def _ensure_schema() -> None:
    personnel._ensure()
    db = get_db()
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
        """
    )
    db.commit()


def _require_admin() -> None:
    if getattr(g, "user", None) is None or not g.user["is_admin"]:
        abort(403)


def _owned_matching_contact(user: Any) -> dict[str, Any] | None:
    store = personnel._contacts()
    username = str(user["username"])
    principal = ContactStore._principal(username)
    email = str(user["email"] or "").strip().casefold()
    display_name = str(user["display_name"] or user["username"] or "Admin").strip()
    name_key = personnel._norm(display_name)
    candidates: list[tuple[int, dict[str, Any]]] = []
    db = get_db()
    for contact in store.contacts(username):
        owner = str(contact.get("owner", "")).strip() or ContactStore._principal(str(contact.get("created_by", "")))
        if ContactStore._principal(owner) != principal:
            continue
        fields = contact.get("fields", {})
        contact_email = str(fields.get("email", "")).strip().casefold()
        contact_name = personnel._norm(str(fields.get("display_name", "")))
        score = 2 if email and contact_email == email else 1 if name_key and contact_name == name_key else 0
        if not score:
            continue
        mapped = db.execute("SELECT user_id FROM employee WHERE contact_id=?", (contact["contact_id"],)).fetchone()
        if mapped and mapped["user_id"] not in {None, int(user["id"])}:
            continue
        candidates.append((score, contact))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _audit(
    employee_id: int,
    action: str,
    *,
    punch_id: int | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str = "",
) -> None:
    get_db().execute(
        """INSERT INTO employee_time_audit(
               employee_id,punch_id,action,before_json,after_json,reason,actor_user_id,created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            employee_id,
            punch_id,
            action,
            json.dumps(before or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(after or {}, ensure_ascii=False, sort_keys=True),
            reason[:1000],
            int(g.user["id"]),
            utc_now(),
        ),
    )


def ensure_admin_time_account() -> Any | None:
    """Give an admin a normal employee time account without manual self-enrolment."""
    user = getattr(g, "user", None)
    if user is None or not user["is_admin"]:
        return None
    _ensure_schema()
    db = get_db()
    existing = db.execute("SELECT * FROM employee WHERE user_id=? AND active=1", (int(user["id"]),)).fetchone()
    if existing:
        return existing

    contact = _owned_matching_contact(user)
    store = personnel._contacts()
    if contact is None:
        values = {
            "display_name": str(user["display_name"] or user["username"] or "Inhaber").strip(),
            "email": str(user["email"] or "").strip(),
        }
        contact = store.upsert(values, str(user["username"]))

    now = utc_now()
    mapped = db.execute("SELECT * FROM employee WHERE contact_id=?", (contact["contact_id"],)).fetchone()
    try:
        if mapped and mapped["user_id"] is None:
            db.execute(
                "UPDATE employee SET user_id=?,can_approve=1,active=1,updated_at=? WHERE id=?",
                (int(user["id"]), now, int(mapped["id"])),
            )
            employee_id = int(mapped["id"])
            action = "admin_time_account_linked"
        elif mapped and int(mapped["user_id"] or 0) == int(user["id"]):
            db.execute("UPDATE employee SET can_approve=1,active=1,updated_at=? WHERE id=?", (now, int(mapped["id"])))
            employee_id = int(mapped["id"])
            action = "admin_time_account_reactivated"
        else:
            cursor = db.execute(
                """INSERT INTO employee(contact_id,user_id,can_approve,active,schedule_json,created_at,updated_at)
                   VALUES(?,?,1,1,'{}',?,?)""",
                (contact["contact_id"], int(user["id"]), now, now),
            )
            employee_id = int(cursor.lastrowid)
            action = "admin_time_account_created"
        _audit(
            employee_id,
            action,
            after={"employee_id": employee_id, "contact_id": contact["contact_id"], "user_id": int(user["id"])},
            reason="Automatische Inhaber-/Admin-Zeiterfassung",
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
    return db.execute("SELECT * FROM employee WHERE user_id=? AND active=1", (int(user["id"]),)).fetchone()


def _auto_provision_admin_time_account() -> None:
    user = getattr(g, "user", None)
    if user is None or not user["is_admin"]:
        return
    if request.endpoint in {"personnel.index", "personnel.punch"} or request.blueprint == bp.name:
        ensure_admin_time_account()


def init_app(app) -> None:
    app.before_request(_auto_provision_admin_time_account)


def _employee(employee_id: int):
    row = get_db().execute("SELECT * FROM employee WHERE id=? AND active=1", (employee_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _day_bounds(shown: date) -> tuple[str, str]:
    zone = personnel._personnel_timezone()
    lower = datetime.combine(shown, time.min, zone).astimezone(timezone.utc).isoformat(timespec="seconds")
    upper = datetime.combine(shown + timedelta(days=1), time.min, zone).astimezone(timezone.utc).isoformat(timespec="seconds")
    return lower, upper


def _local_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Datum/Uhrzeit ist ungültig") from exc
    zone = personnel._personnel_timezone()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _utc_stamp(value: str) -> tuple[str, date]:
    local = _local_datetime(value)
    if local > personnel._local_now() + timedelta(minutes=5):
        raise ValueError("Eine Arbeitszeit darf nicht in der Zukunft liegen")
    return local.astimezone(timezone.utc).isoformat(timespec="seconds"), local.date()


def _punch_payload(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "employee_id": int(row["employee_id"]),
        "action": str(row["action"]),
        "occurred_at": str(row["occurred_at"]),
        "recorded_by": int(row["recorded_by"]),
    }


def _sequence_error(employee_id: int, shown: date) -> str:
    rows = personnel._day_punches(employee_id, shown)
    state = "clock_out"
    for row in rows:
        action = str(row["action"])
        if action not in _ALLOWED.get(state, set()):
            return f"Ungültige Reihenfolge: {_ACTION_LABELS.get(action, action)} nach {_ACTION_LABELS.get(state, state)}"
        state = action
    summary = personnel._day_summary(employee_id, shown)
    if int(summary["work_minutes"]) > 10 * 60:
        return "Die korrigierte Arbeitszeit überschreitet 10 Stunden"
    return ""


def _reason() -> str:
    value = str(request.form.get("reason", "")).strip()
    if len(value) < 3:
        raise ValueError("Für eine Zeitkorrektur ist eine kurze Begründung erforderlich")
    return value[:1000]


def _ensure_open_month(employee_id: int, shown: date) -> None:
    if personnel.month_is_closed(employee_id, shown.strftime("%Y-%m")):
        raise ValueError("Dieser Arbeitszeitmonat ist bereits festgeschrieben")


def _refresh_flex(employee: Any, shown: date) -> None:
    state = personnel._punch_state(int(employee["id"]), shown)
    if state == "clock_out":
        personnel._update_flex_day(int(employee["id"]), shown, json.loads(employee["schedule_json"] or "{}"))
    else:
        get_db().execute("DELETE FROM employee_flex_day WHERE employee_id=? AND work_date=?", (int(employee["id"]), shown.isoformat()))


def _redirect(employee_id: int, shown: date) -> Any:
    return redirect(url_for("personnel_time.index", employee_id=employee_id, date=shown.isoformat()))


def _employees_for_admin() -> list[dict[str, Any]]:
    names = personnel._employee_names()
    rows = []
    for row in get_db().execute("SELECT * FROM employee WHERE active=1 ORDER BY id").fetchall():
        item = dict(row)
        item["name"] = names.get(int(row["id"]), f"Mitarbeiter {row['id']}")
        item["state"] = personnel._punch_state(int(row["id"]), personnel._local_now().date())
        rows.append(item)
    return rows


@bp.get("")
@login_required
def index():
    _require_admin()
    ensure_admin_time_account()
    employees = _employees_for_admin()
    try:
        selected_id = int(request.args.get("employee_id", "") or (employees[0]["id"] if employees else 0))
    except ValueError:
        selected_id = int(employees[0]["id"] if employees else 0)
    selected = next((item for item in employees if int(item["id"]) == selected_id), None)
    if selected is None:
        abort(404)
    try:
        shown = date.fromisoformat(request.args.get("date", personnel._local_now().date().isoformat()))
    except ValueError:
        shown = personnel._local_now().date()
    lower, upper = _day_bounds(shown)
    rows = get_db().execute(
        """SELECT employee_punch.*,user.username,user.display_name
           FROM employee_punch LEFT JOIN user ON user.id=employee_punch.recorded_by
           WHERE employee_id=? AND occurred_at>=? AND occurred_at<? ORDER BY occurred_at,id""",
        (selected_id, lower, upper),
    ).fetchall()
    zone = personnel._personnel_timezone()
    events = []
    for row in rows:
        item = dict(row)
        local = datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00")).astimezone(zone)
        item["local_input"] = local.strftime("%Y-%m-%dT%H:%M")
        item["local_display"] = local.strftime("%d.%m.%Y %H:%M")
        item["recorded_by_name"] = str(row["display_name"] or row["username"] or "—")
        events.append(item)
    audits = []
    user_names = {int(row["id"]): str(row["display_name"] or row["username"]) for row in get_db().execute("SELECT id,username,display_name FROM user").fetchall()}
    for row in get_db().execute(
        "SELECT * FROM employee_time_audit WHERE employee_id=? ORDER BY id DESC LIMIT 100",
        (selected_id,),
    ).fetchall():
        item = dict(row)
        item["actor_name"] = user_names.get(int(row["actor_user_id"]), f"Benutzer {row['actor_user_id']}")
        try:
            item["before"] = json.loads(row["before_json"] or "{}")
            item["after"] = json.loads(row["after_json"] or "{}")
        except json.JSONDecodeError:
            item["before"], item["after"] = {}, {}
        audits.append(item)
    summary = personnel._day_summary(selected_id, shown)
    return render_template(
        "personnel/time_admin.html",
        employees=employees,
        selected=selected,
        shown=shown,
        events=events,
        audits=audits,
        summary=summary,
        action_labels=_ACTION_LABELS,
        allowed=_ALLOWED,
        current_state=personnel._punch_state(selected_id, personnel._local_now().date()),
        month_closed=personnel.month_is_closed(selected_id, shown.strftime("%Y-%m")),
    )


@bp.post("/<int:employee_id>/punch/<action>")
@login_required
def quick_punch(employee_id: int, action: str):
    _require_admin()
    if action not in personnel.PUNCH_ACTIONS:
        abort(404)
    employee = _employee(employee_id)
    shown = personnel._local_now().date()
    try:
        _ensure_open_month(employee_id, shown)
    except ValueError as exc:
        flash(str(exc))
        return _redirect(employee_id, shown)
    state = personnel._punch_state(employee_id, shown)
    summary = personnel._day_summary(employee_id, shown)
    if int(summary["work_minutes"]) >= 10 * 60 and state == "clock_in" and action != "clock_out":
        flash("Nach 10 Stunden ist nur noch Gehen zulässig.")
        return _redirect(employee_id, shown)
    if action not in _ALLOWED.get(state, set()):
        flash("Diese Buchung passt nicht zum aktuellen Stempelstatus des Mitarbeiters.")
        return _redirect(employee_id, shown)
    db = get_db()
    cursor = db.execute(
        "INSERT INTO employee_punch(employee_id,action,occurred_at,recorded_by) VALUES(?,?,?,?)",
        (employee_id, action, utc_now(), int(g.user["id"])),
    )
    row = db.execute("SELECT * FROM employee_punch WHERE id=?", (int(cursor.lastrowid),)).fetchone()
    _audit(employee_id, "admin_quick_punch", punch_id=int(row["id"]), after=_punch_payload(row), reason="Chef-Stempelung jetzt")
    if action == "clock_out":
        _refresh_flex(employee, shown)
    db.commit()
    flash(f"{_ACTION_LABELS[action]} für Mitarbeiter gespeichert.")
    return _redirect(employee_id, shown)


@bp.post("/<int:employee_id>/add")
@login_required
def add_punch(employee_id: int):
    _require_admin()
    employee = _employee(employee_id)
    action = str(request.form.get("action", ""))
    if action not in personnel.PUNCH_ACTIONS:
        abort(400)
    try:
        reason = _reason()
        stamp, shown = _utc_stamp(str(request.form.get("occurred_at", "")))
        _ensure_open_month(employee_id, shown)
    except ValueError as exc:
        flash(str(exc))
        return _redirect(employee_id, personnel._local_now().date())
    db = get_db()
    cursor = db.execute(
        "INSERT INTO employee_punch(employee_id,action,occurred_at,recorded_by) VALUES(?,?,?,?)",
        (employee_id, action, stamp, int(g.user["id"])),
    )
    row = db.execute("SELECT * FROM employee_punch WHERE id=?", (int(cursor.lastrowid),)).fetchone()
    error = _sequence_error(employee_id, shown)
    if error:
        db.rollback()
        flash(error)
        return _redirect(employee_id, shown)
    _audit(employee_id, "admin_punch_created", punch_id=int(row["id"]), after=_punch_payload(row), reason=reason)
    _refresh_flex(employee, shown)
    db.commit()
    flash("Arbeitszeitbuchung ergänzt.")
    return _redirect(employee_id, shown)


@bp.post("/punch/<int:punch_id>/edit")
@login_required
def edit_punch(punch_id: int):
    _require_admin()
    db = get_db()
    row = db.execute("SELECT * FROM employee_punch WHERE id=?", (punch_id,)).fetchone()
    if row is None:
        abort(404)
    employee = _employee(int(row["employee_id"]))
    action = str(request.form.get("action", ""))
    if action not in personnel.PUNCH_ACTIONS:
        abort(400)
    zone = personnel._personnel_timezone()
    old_local = datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00")).astimezone(zone)
    old_day = old_local.date()
    before = _punch_payload(row)
    try:
        reason = _reason()
        stamp, new_day = _utc_stamp(str(request.form.get("occurred_at", "")))
        _ensure_open_month(int(row["employee_id"]), old_day)
        _ensure_open_month(int(row["employee_id"]), new_day)
    except ValueError as exc:
        flash(str(exc))
        return _redirect(int(row["employee_id"]), old_day)
    db.execute("UPDATE employee_punch SET action=?,occurred_at=? WHERE id=?", (action, stamp, punch_id))
    for shown in {old_day, new_day}:
        error = _sequence_error(int(row["employee_id"]), shown)
        if error:
            db.rollback()
            flash(error)
            return _redirect(int(row["employee_id"]), old_day)
    updated = db.execute("SELECT * FROM employee_punch WHERE id=?", (punch_id,)).fetchone()
    _audit(int(row["employee_id"]), "admin_punch_updated", punch_id=punch_id, before=before, after=_punch_payload(updated), reason=reason)
    for shown in {old_day, new_day}:
        _refresh_flex(employee, shown)
    db.commit()
    flash("Arbeitszeit korrigiert; ursprünglicher Wert bleibt im Änderungsprotokoll erhalten.")
    return _redirect(int(row["employee_id"]), new_day)


@bp.post("/punch/<int:punch_id>/delete")
@login_required
def delete_punch(punch_id: int):
    _require_admin()
    db = get_db()
    row = db.execute("SELECT * FROM employee_punch WHERE id=?", (punch_id,)).fetchone()
    if row is None:
        abort(404)
    employee = _employee(int(row["employee_id"]))
    zone = personnel._personnel_timezone()
    shown = datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00")).astimezone(zone).date()
    try:
        reason = _reason()
        _ensure_open_month(int(row["employee_id"]), shown)
    except ValueError as exc:
        flash(str(exc))
        return _redirect(int(row["employee_id"]), shown)
    before = _punch_payload(row)
    db.execute("DELETE FROM employee_punch WHERE id=?", (punch_id,))
    error = _sequence_error(int(row["employee_id"]), shown)
    if error:
        db.rollback()
        flash("Löschen würde eine ungültige Stempelfolge erzeugen: " + error)
        return _redirect(int(row["employee_id"]), shown)
    _audit(int(row["employee_id"]), "admin_punch_deleted", punch_id=punch_id, before=before, reason=reason)
    _refresh_flex(employee, shown)
    db.commit()
    flash("Fehlbuchung entfernt; der ursprüngliche Datensatz bleibt im Änderungsprotokoll nachvollziehbar.")
    return _redirect(int(row["employee_id"]), shown)
