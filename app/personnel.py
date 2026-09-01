"""Contact-backed personnel planning, absence approval and punch clock."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .auth import login_required
from .contact_store import ContactStore
from .db import get_db
from .document_store import utc_now


bp = Blueprint("personnel", __name__, url_prefix="/personnel")
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
PUNCH_ACTIONS = {"clock_in", "break_start", "break_end", "clock_out"}


def _contacts() -> ContactStore:
    from flask import current_app
    return ContactStore(current_app.config["DOCUMENT_ROOT"])


def _ensure() -> None:
    db = get_db()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS employee (
      id INTEGER PRIMARY KEY AUTOINCREMENT, contact_id TEXT NOT NULL UNIQUE,
      user_id INTEGER UNIQUE, can_approve INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1, schedule_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES user(id));
    CREATE TABLE IF NOT EXISTS employee_punch (
      id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL,
      action TEXT NOT NULL, occurred_at TEXT NOT NULL, recorded_by INTEGER NOT NULL,
      FOREIGN KEY(employee_id) REFERENCES employee(id), FOREIGN KEY(recorded_by) REFERENCES user(id));
    CREATE INDEX IF NOT EXISTS employee_punch_employee_time ON employee_punch(employee_id, occurred_at);
    CREATE TABLE IF NOT EXISTS employee_absence (
      id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL,
      kind TEXT NOT NULL, starts_on TEXT NOT NULL, ends_on TEXT NOT NULL,
      tags_json TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'requested', requested_at TEXT NOT NULL,
      decided_at TEXT, decided_by_employee_id INTEGER,
      FOREIGN KEY(employee_id) REFERENCES employee(id));
    CREATE TABLE IF NOT EXISTS employee_month_close (
      employee_id INTEGER NOT NULL, month TEXT NOT NULL,
      work_minutes INTEGER NOT NULL, break_minutes INTEGER NOT NULL,
      closed_at TEXT NOT NULL, PRIMARY KEY(employee_id,month),
      FOREIGN KEY(employee_id) REFERENCES employee(id));
    """)
    db.commit()


def _norm(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9äöüß]+", " ", str(value).casefold()).split())


def _username(name: str, email: str) -> str:
    base = (email.split("@", 1)[0] if "@" in email else name).casefold()
    base = re.sub(r"[^a-z0-9_.-]+", "-", base).strip("-._") or "mitarbeiter"
    candidate, number = base, 2
    while get_db().execute("SELECT 1 FROM user WHERE username=?", (candidate,)).fetchone():
        candidate, number = f"{base}-{number}", number + 1
    return candidate


def _link_or_create_user(contact: dict) -> tuple[int, str]:
    fields = contact.get("fields", {})
    email = str(fields.get("email", "")).strip().casefold()
    name = str(fields.get("display_name", "")).strip()
    db = get_db()
    matches = []
    if email:
        matches = db.execute("SELECT * FROM user WHERE lower(trim(email))=?", (email,)).fetchall()
    if not matches and name:
        matches = [row for row in db.execute("SELECT * FROM user").fetchall()
                   if _norm(row["display_name"] or row["username"]) == _norm(name)]
    if len(matches) == 1:
        return int(matches[0]["id"]), str(matches[0]["username"])
    username = _username(name, email)
    now = utc_now()
    cursor = db.execute(
        """INSERT INTO user(username,password,display_name,email,is_admin,is_disabled,
           auth_version,created_at,updated_at,profile_source)
           VALUES(?,?,?,?,0,1,1,?,?,?)""",
        (username, generate_password_hash(__import__("secrets").token_urlsafe(48)), name, email, now, now, "employee_contact"),
    )
    db.commit()
    return int(cursor.lastrowid), username


def _employee_for_user(user_id: int):
    _ensure()
    return get_db().execute("SELECT * FROM employee WHERE user_id=? AND active=1", (user_id,)).fetchone()


def _schedule(form) -> dict:
    result = {}
    for index, day in enumerate(WEEKDAYS):
        raw = str(form.get(f"hours_{index}", "0")).replace(",", ".")
        try: hours = float(raw)
        except ValueError: raise ValueError(f"Ungültige Stunden für {day}")
        if not 0 <= hours <= 10: raise ValueError("Die tägliche Arbeitszeit muss zwischen 0 und 10 Stunden liegen")
        start = str(form.get(f"start_{index}", "08:00")).strip()
        if not re.fullmatch(r"\d{2}:\d{2}", start): raise ValueError(f"Ungültiger Beginn für {day}")
        result[str(index)] = {"hours": hours, "start": start}
    return result


def required_break_minutes(work_minutes: int) -> int:
    if work_minutes > 9 * 60: return 45
    if work_minutes > 6 * 60: return 30
    return 0


def _day_summary(employee_id: int, shown: date) -> dict:
    prefix = shown.isoformat()
    rows = get_db().execute(
        "SELECT action,occurred_at FROM employee_punch WHERE employee_id=? AND substr(occurred_at,1,10)=? ORDER BY occurred_at,id",
        (employee_id, prefix),
    ).fetchall()
    active = None; break_started = None; work = breaks = 0
    for row in rows:
        stamp = datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))
        if row["action"] == "clock_in": active = stamp
        elif row["action"] == "break_start" and active:
            work += max(0, int((stamp - active).total_seconds() // 60)); active = None; break_started = stamp
        elif row["action"] == "break_end" and break_started:
            breaks += max(0, int((stamp - break_started).total_seconds() // 60)); break_started = None; active = stamp
        elif row["action"] == "clock_out" and active:
            work += max(0, int((stamp - active).total_seconds() // 60)); active = None
    return {"work_minutes": work, "break_minutes": breaks, "required_break": required_break_minutes(work),
            "open": bool(active or break_started), "events": [dict(row) for row in rows],
            "compliant": breaks >= required_break_minutes(work)}


def _punch_state(employee_id: int) -> str:
    row = get_db().execute("SELECT action FROM employee_punch WHERE employee_id=? ORDER BY occurred_at DESC,id DESC LIMIT 1", (employee_id,)).fetchone()
    return str(row["action"]) if row else "clock_out"


def _previous_month(shown: date) -> str:
    first = shown.replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def close_due_months(as_of: date | None = None) -> int:
    """Freeze the previous month on or after the tenth, once per employee."""
    _ensure(); shown = as_of or date.today()
    if shown.day < 10: return 0
    month = _previous_month(shown); year, number = map(int, month.split("-"))
    first = date(year, number, 1)
    following = date(year + (number == 12), 1 if number == 12 else number + 1, 1)
    db = get_db(); created = 0
    for employee in db.execute("SELECT id FROM employee WHERE active=1").fetchall():
        if db.execute("SELECT 1 FROM employee_month_close WHERE employee_id=? AND month=?", (employee["id"], month)).fetchone(): continue
        work = breaks = 0; current = first
        while current < following:
            summary = _day_summary(int(employee["id"]), current); work += summary["work_minutes"]; breaks += summary["break_minutes"]; current += timedelta(days=1)
        db.execute("INSERT INTO employee_month_close(employee_id,month,work_minutes,break_minutes,closed_at) VALUES(?,?,?,?,?)", (employee["id"], month, work, breaks, utc_now())); created += 1
    db.commit(); return created


def month_is_closed(employee_id: int, month: str) -> bool:
    return get_db().execute("SELECT 1 FROM employee_month_close WHERE employee_id=? AND month=?", (employee_id, month)).fetchone() is not None


@bp.get("")
@login_required
def index():
    _ensure(); close_due_months(); db = get_db(); actor = _employee_for_user(int(g.user["id"])); contacts = _contacts()
    employees = []
    for row in db.execute("SELECT * FROM employee WHERE active=1 ORDER BY id").fetchall():
        try: contact = contacts.get(row["contact_id"], str(g.user["username"]))
        except ValueError: contact = {"contact_id": row["contact_id"], "fields": {"display_name": "Nicht sichtbarer Kontakt"}}
        employees.append({**dict(row), "contact": contact, "schedule": json.loads(row["schedule_json"] or "{}"),
                          "today": _day_summary(int(row["id"]), date.today())})
    absences = [dict(row) for row in db.execute("SELECT * FROM employee_absence ORDER BY starts_on DESC,id DESC").fetchall()]
    month_closes = [dict(row) for row in db.execute("SELECT * FROM employee_month_close ORDER BY month DESC,employee_id").fetchall()]
    by_id = {row["id"]: row for row in employees}
    actor = by_id.get(int(actor["id"])) if actor else None
    for absence in absences:
        absence["employee"] = by_id.get(absence["employee_id"])
        absence["tags"] = json.loads(absence["tags_json"] or "[]")
    available = [c for c in contacts.contacts(str(g.user["username"])) if c["contact_id"] not in {e["contact_id"] for e in employees}]
    return render_template("personnel/index.html", employees=employees, employee=actor, absences=absences,
                           month_closes=month_closes,
                           contacts=available, weekdays=WEEKDAYS, can_manage=bool(g.user["is_admin"]),
                           can_approve=bool(actor and actor["can_approve"]))


@bp.post("/employees")
@login_required
def add_employee():
    if not g.user["is_admin"]: abort(403)
    _ensure(); contacts = _contacts(); contact = contacts.get(request.form.get("contact_id", ""), str(g.user["username"])); user_id, username = _link_or_create_user(contact); now = utc_now()
    get_db().execute("INSERT INTO employee(contact_id,user_id,created_at,updated_at) VALUES(?,?,?,?)", (contact["contact_id"], user_id, now, now)); get_db().commit()
    contacts.share(contact["contact_id"], [*contact.get("managers", []), username], str(g.user["username"]), readers=contact.get("readers", []))
    flash("Mitarbeiterkontakt verknüpft. Ein neu erzeugtes Benutzerkonto bleibt bis zur Aktivierung gesperrt.")
    return redirect(url_for("personnel.index"))


@bp.post("/employees/<int:employee_id>/settings")
@login_required
def employee_settings(employee_id: int):
    if not g.user["is_admin"]: abort(403)
    try: schedule = _schedule(request.form)
    except ValueError as exc: flash(str(exc)); return redirect(url_for("personnel.index"))
    get_db().execute("UPDATE employee SET can_approve=?,schedule_json=?,updated_at=? WHERE id=?", (int(request.form.get("can_approve") == "1"), json.dumps(schedule), utc_now(), employee_id)); get_db().commit()
    return redirect(url_for("personnel.index"))


@bp.post("/punch/<action>")
@login_required
def punch(action: str):
    if action not in PUNCH_ACTIONS: abort(404)
    employee = _employee_for_user(int(g.user["id"]));
    if employee is None: abort(403)
    if month_is_closed(int(employee["id"]), date.today().strftime("%Y-%m")):
        abort(409, description="Dieser Arbeitszeitmonat ist festgeschrieben")
    allowed = {"clock_out": {"clock_in"}, "clock_in": {"break_start", "clock_out"}, "break_start": {"break_end"}, "break_end": {"break_start", "clock_out"}}
    if action not in allowed.get(_punch_state(int(employee["id"])), set()):
        flash("Diese Buchung passt nicht zum aktuellen Stempelstatus.")
        return redirect(url_for("personnel.index"))
    get_db().execute("INSERT INTO employee_punch(employee_id,action,occurred_at,recorded_by) VALUES(?,?,?,?)", (employee["id"], action, utc_now(), g.user["id"])); get_db().commit()
    return redirect(url_for("personnel.index"))


@bp.post("/self-service")
@login_required
def self_service():
    employee = _employee_for_user(int(g.user["id"]));
    if employee is None: abort(403)
    changes = {key: request.form.get(key, "") for key in ("display_name", "email", "phone")}
    try: _contacts().patch_fields(employee["contact_id"], changes, str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    else: flash("Mitarbeiterdaten gespeichert.")
    return redirect(url_for("personnel.index"))


@bp.post("/absence")
@login_required
def request_absence():
    employee = _employee_for_user(int(g.user["id"]));
    if employee is None: abort(403)
    starts, ends = request.form.get("starts_on", ""), request.form.get("ends_on", "")
    try:
        if date.fromisoformat(ends) < date.fromisoformat(starts): raise ValueError
    except ValueError: flash("Ungültiger Zeitraum"); return redirect(url_for("personnel.index"))
    kind = request.form.get("kind", "frei")
    if kind not in {"urlaub", "frei", "krank"}: kind = "frei"
    tags = sorted({tag.strip()[:50] for tag in request.form.get("tags", "").split(",") if tag.strip()})[:20]
    status = "reported" if kind == "krank" else "requested"
    get_db().execute("INSERT INTO employee_absence(employee_id,kind,starts_on,ends_on,tags_json,note,status,requested_at) VALUES(?,?,?,?,?,?,?,?)", (employee["id"], kind, starts, ends, json.dumps(tags, ensure_ascii=False), request.form.get("note", "")[:1000], status, utc_now())); get_db().commit()
    return redirect(url_for("personnel.index"))


@bp.post("/absence/<int:absence_id>/<decision>")
@login_required
def decide_absence(absence_id: int, decision: str):
    approver = _employee_for_user(int(g.user["id"])); row = get_db().execute("SELECT * FROM employee_absence WHERE id=?", (absence_id,)).fetchone()
    if not approver or not approver["can_approve"] or not row: abort(403)
    if int(row["employee_id"]) == int(approver["id"]): abort(403)
    if decision not in {"approved", "rejected"}: abort(404)
    get_db().execute("UPDATE employee_absence SET status=?,decided_at=?,decided_by_employee_id=? WHERE id=? AND status='requested'", (decision, utc_now(), approver["id"], absence_id)); get_db().commit()
    return redirect(url_for("personnel.index"))
