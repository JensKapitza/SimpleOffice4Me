"""Dedicated mobile-friendly VTODO task management on top of TodoStore."""
from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .todo_store import TodoStore


bp = Blueprint("tasks", __name__, url_prefix="/tasks")
BOARD_COLUMNS = (
    ("needs-action", "Offen", "Open"),
    ("in-process", "In Arbeit", "In progress"),
    ("completed", "Erledigt", "Completed"),
    ("cancelled", "Abgebrochen", "Cancelled"),
)


def _store() -> TodoStore:
    return TodoStore(current_app.config["DOCUMENT_ROOT"])


def _actor() -> str:
    return str(g.user["username"])


def _parse_rrule(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in str(value or "").upper().split(";"):
        if "=" not in item:
            continue
        key, raw = item.split("=", 1)
        if key and raw:
            result[key] = raw
    return result


def _parse_anchor(value: str) -> tuple[datetime, bool] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    date_only = len(raw) == 10 and raw[4:5] == "-" and raw[7:8] == "-"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed, date_only
    except ValueError:
        return None


def _shift_month(value: datetime, months: int) -> datetime:
    absolute = value.year * 12 + (value.month - 1) + months
    year, month_index = divmod(absolute, 12)
    month = month_index + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _format_anchor(value: datetime, date_only: bool) -> str:
    if date_only:
        return value.date().isoformat()
    return value.isoformat().replace("+00:00", "Z")


def _parse_until(value: str, reference: datetime) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 8 and raw.isdigit():
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=reference.tzinfo)
        if raw.endswith("Z") and "T" in raw:
            return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=reference.tzinfo)
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def next_recurrence_values(task: dict[str, Any]) -> dict[str, str] | None:
    """Advance common RRULE tasks without inventing instances for unsupported rules."""
    rule = _parse_rrule(str(task.get("rrule", "")))
    frequency = rule.get("FREQ", "")
    if frequency not in {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}:
        return None
    # COUNT needs a durable occurrence counter. Until TodoStore has one, do not
    # silently violate a finite recurrence rule.
    if "COUNT" in rule:
        return None
    try:
        interval = max(1, min(int(rule.get("INTERVAL", "1")), 366))
    except ValueError:
        return None
    anchor_info = _parse_anchor(str(task.get("due") or task.get("start") or ""))
    if anchor_info is None:
        return None
    anchor, date_only = anchor_info
    if frequency == "DAILY":
        next_anchor = anchor + timedelta(days=interval)
    elif frequency == "WEEKLY":
        next_anchor = anchor + timedelta(weeks=interval)
    elif frequency == "MONTHLY":
        next_anchor = _shift_month(anchor, interval)
    else:
        try:
            next_anchor = anchor.replace(year=anchor.year + interval)
        except ValueError:
            next_anchor = anchor.replace(year=anchor.year + interval, day=28)
    until = _parse_until(rule.get("UNTIL", ""), anchor)
    if until is not None:
        try:
            if next_anchor > until:
                return None
        except TypeError:
            return None
    updates: dict[str, str] = {"status": "needs-action", "percent_complete": "0", "completed": ""}
    due_info = _parse_anchor(str(task.get("due", "")))
    start_info = _parse_anchor(str(task.get("start", "")))
    delta = next_anchor - anchor
    if due_info:
        updates["due"] = _format_anchor(due_info[0] + delta, due_info[1])
    if start_info:
        updates["start"] = _format_anchor(start_info[0] + delta, start_info[1])
    return updates


def _visible_rows(actor: str) -> list[dict[str, Any]]:
    rows = _store().items(actor)
    query = request.args.get("q", "").strip().casefold()
    list_id = request.args.get("list_id", "").strip()
    assigned = request.args.get("assigned", "").strip().casefold()
    if query:
        rows = [
            row for row in rows
            if query in " ".join([
                str(row.get("title", "")), str(row.get("description", "")),
                " ".join(row.get("categories", [])), str(row.get("project_id", "")),
                str(row.get("contact_id", "")),
            ]).casefold()
        ]
    if list_id:
        rows = [row for row in rows if row.get("list_id") == list_id]
    if assigned:
        rows = [row for row in rows if assigned in [str(value).casefold() for value in row.get("assigned_to", [])]]
    return rows


@bp.get("/")
@login_required
def board():
    actor = _actor()
    rows = _visible_rows(actor)
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        parent_uid = str(row.get("parent_uid", ""))
        if parent_uid:
            by_parent.setdefault(parent_uid, []).append(row)
    top_level = [row for row in rows if not row.get("parent_uid")]
    grouped = {status: [row for row in top_level if row.get("status", "needs-action") == status] for status, _de, _en in BOARD_COLUMNS}
    known_statuses = set(grouped)
    grouped["needs-action"].extend(row for row in top_level if row.get("status", "needs-action") not in known_statuses)
    return render_template(
        "tasks/board.html",
        columns=BOARD_COLUMNS,
        grouped=grouped,
        children=by_parent,
        lists=_store().lists(actor),
        query=request.args.get("q", "").strip(),
        selected_list=request.args.get("list_id", "").strip(),
        assigned=request.args.get("assigned", "").strip(),
    )


@bp.post("/")
@login_required
def create_task():
    actor = _actor()
    try:
        values = request.form.to_dict()
        values["categories"] = request.form.getlist("categories") or values.get("categories", "")
        values["assigned_to"] = request.form.getlist("assigned_to") or values.get("assigned_to", "")
        _store().add(values.get("title", ""), actor, values)
        flash("Aufgabe angelegt.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))


@bp.post("/<item_id>/move")
@login_required
def move_task(item_id: str):
    try:
        status = request.form.get("status", "needs-action")
        if status not in {item[0] for item in BOARD_COLUMNS}:
            raise ValueError("Ungültiger Aufgabenstatus")
        values: dict[str, Any] = {"status": status}
        if status == "completed":
            values["percent_complete"] = 100
        elif status == "needs-action":
            values["percent_complete"] = 0
        _store().update(item_id, values, _actor())
        flash("Aufgabenstatus aktualisiert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))


@bp.post("/<item_id>/subtasks")
@login_required
def create_subtask(item_id: str):
    actor = _actor()
    try:
        parent = _store().get(item_id, actor)
        title = request.form.get("title", "").strip()
        values = {
            "list_id": parent.get("list_id", "personal"),
            "parent_uid": parent.get("uid", ""),
            "project_id": parent.get("project_id", ""),
            "project_phase": parent.get("project_phase", ""),
            "activity": parent.get("activity", ""),
            "contact_id": parent.get("contact_id", ""),
            "priority": parent.get("priority", 0),
            "categories": parent.get("categories", []),
            "assigned_to": parent.get("assigned_to", []),
            "due": request.form.get("due", "") or parent.get("due", ""),
        }
        _store().add(title, actor, values)
        flash("Unteraufgabe angelegt.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))


@bp.post("/<item_id>/complete-occurrence")
@login_required
def complete_occurrence(item_id: str):
    actor = _actor()
    try:
        task = _store().get(item_id, actor)
        if not task.get("rrule"):
            _store().update(item_id, {"status": "completed", "percent_complete": 100}, actor)
            flash("Aufgabe erledigt.")
        else:
            updates = next_recurrence_values(task)
            if updates is None:
                _store().update(item_id, {"status": "completed", "percent_complete": 100}, actor)
                flash("Serie beendet oder Regel nicht automatisch fortschaltbar; Aufgabe wurde abgeschlossen.")
            else:
                _store().update(item_id, updates, actor)
                flash("Vorkommen erledigt; Aufgabe auf den nächsten Termin verschoben.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))


@bp.post("/<item_id>/update")
@login_required
def update_task(item_id: str):
    try:
        values = request.form.to_dict()
        values["categories"] = request.form.getlist("categories") or values.get("categories", "")
        values["assigned_to"] = request.form.getlist("assigned_to") or values.get("assigned_to", "")
        _store().update(item_id, values, _actor())
        flash("Aufgabe gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))
