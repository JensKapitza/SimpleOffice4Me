"""Dedicated mobile-friendly VTODO task management on top of TodoStore."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from .auth import login_required
from .calendar_collections import CalendarCollections
from .calendar_followups import readable_followup_source
from .calendar_store import CalendarStore
from .document_store import DocumentStore
from .tasks_suggest import suggest_task_next
from .todo_store import (
    TodoConflict,
    TodoDueDateBeforeStart,
    TodoDueDateError,
    TodoStore,
    parse_task_day,
    supports_quick_due_change,
)


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


def _task_by_id(item_id: str, actor: str) -> dict[str, Any]:
    task = next((row for row in _store().items(actor) if str(row.get("id", "")) == str(item_id)), None)
    if task is None:
        raise ValueError("Unbekannte Aufgabe")
    return task


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
    updates: dict[str, str] = {
        "status": "needs-action",
        "percent_complete": "0",
        "completed_at": "",
        "result": "",
    }
    due_info = _parse_anchor(str(task.get("due", "")))
    start_info = _parse_anchor(str(task.get("start", "")))
    delta = next_anchor - anchor
    if due_info:
        updates["due"] = _format_anchor(due_info[0] + delta, due_info[1])
    if start_info:
        updates["start"] = _format_anchor(start_info[0] + delta, start_info[1])
    return updates


def _complete_task_occurrence(item_id: str, actor: str) -> str:
    task = _task_by_id(item_id, actor)
    if task.get("status") == "completed" and not task.get("rrule"):
        raise ValueError("Aufgabe ist bereits erledigt")

    next_due = ""
    message = "Aufgabe erledigt."
    if not task.get("rrule"):
        _store().update(item_id, {"status": "completed", "percent_complete": 100}, actor)
    else:
        updates = next_recurrence_values(task)
        if updates is None:
            _store().update(item_id, {"status": "completed", "percent_complete": 100}, actor)
            message = "Serie beendet oder Regel nicht automatisch fortschaltbar; Aufgabe wurde abgeschlossen."
        else:
            _store().update(item_id, updates, actor)
            next_due = str(updates.get("due", ""))
            message = "Vorkommen erledigt; Aufgabe auf den nächsten Termin verschoben."

    try:
        from .inventory import record_inventory_task_completion

        linked = record_inventory_task_completion(
            current_app.config["DOCUMENT_ROOT"], task, actor, next_due
        )
        if linked:
            message += " Inventar-Prüfhistorie aktualisiert."
    except (OSError, ValueError) as exc:
        message += f" Warnung: Inventar-Prüfhistorie konnte nicht aktualisiert werden: {exc}"
    return message


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


def _visible_calendar_sources(rows: list[dict[str, Any]], actor: str) -> dict[str, str]:
    source_by_task = _store().calendar_followup_sources({row["id"] for row in rows})
    if not source_by_task:
        return {}
    root = current_app.config["DOCUMENT_ROOT"]
    calendar_ids = {calendar["calendar_id"] for calendar in CalendarCollections(root).calendars(actor)}
    reference_ids = set(source_by_task.values())
    available = {
        event["event_id"] for event in CalendarStore(root).events(actor)
        if event["event_id"] in reference_ids and readable_followup_source(event, calendar_ids)
    }
    return {task_id: event_id for task_id, event_id in source_by_task.items() if event_id in available}


def _task_source_documents(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Resolve source IDs through request-scoped document visibility checks."""
    store = DocumentStore(current_app.config["DOCUMENT_ROOT"])
    sources: dict[str, list[dict[str, Any]]] = {}
    for task in rows:
        raw_ids = task.get("document_ids", [])
        ids = list(raw_ids) if isinstance(raw_ids, (list, tuple)) else []
        source_document_id = str(task.get("source_document_id", "")).strip()
        email_document_id = str(task.get("email_document_id", "")).strip()
        if email_document_id:
            ids.append(email_document_id)
        task_sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_id in ids:
            document_id = str(raw_id or "").strip()
            if not document_id or document_id == source_document_id or document_id in seen:
                continue
            seen.add(document_id)
            try:
                document = store.get_document(document_id)
            except (OSError, ValueError):
                task_sources.append({"available": False})
                continue
            path = str(document.get("last_path") or "")
            if not path or document.get("system_state") == "webdav_deleted":
                task_sources.append({"available": False})
                continue
            task_sources.append({
                "available": True,
                "document_id": document_id,
                "name": path.rsplit("/", 1)[-1] or "Dokument",
            })
        if task_sources:
            sources[str(task.get("id", ""))] = task_sources
    return sources


RESUBMIT_VIEWS = (
    ("all", "Alle", "All"),
    ("today", "Heute", "Today"),
    ("overdue", "Überfällig", "Overdue"),
    ("week", "Nächste 7 Tage", "Next 7 days"),
    ("waiting", "Warten auf Rückmeldung", "Waiting for feedback"),
    ("done", "Erledigt", "Completed"),
)


def _resubmit_view_match(view: str, row: dict[str, Any], today: str, week_end: str) -> bool:
    status = str(row.get("status", "needs-action"))
    finished = status in {"completed", "cancelled"}
    due = str(row.get("due", "") or "")[:10]
    if view == "today":
        return due == today and not finished
    if view == "overdue":
        return bool(due) and due < today and not finished
    if view == "week":
        return bool(due) and today <= due <= week_end and not finished
    if view == "waiting":
        return status == "in-process"
    if view == "done":
        return status == "completed"
    return True


def _board_filters() -> dict[str, str]:
    filters = {
        key: request.args.get(key, "").strip()
        for key in ("q", "list_id", "assigned")
    }
    view = request.args.get("view", "all")
    filters["view"] = view if view in {item[0] for item in RESUBMIT_VIEWS} else "all"
    return {key: value for key, value in filters.items() if value}


@bp.get("/")
@login_required
def board():
    actor = _actor()
    rows = _visible_rows(actor)
    filters = _board_filters()
    view = filters["view"]
    today = date.today().isoformat()
    week_end = (date.today() + timedelta(days=7)).isoformat()
    view_counts = {
        item[0]: sum(1 for row in rows if _resubmit_view_match(item[0], row, today, week_end))
        for item in RESUBMIT_VIEWS
    }
    if view != "all":
        rows = [row for row in rows if _resubmit_view_match(view, row, today, week_end)]
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        parent_uid = str(row.get("parent_uid", ""))
        if parent_uid:
            by_parent.setdefault(parent_uid, []).append(row)
    document_store = DocumentStore(current_app.config["DOCUMENT_ROOT"])
    source_documents = {}
    for row in rows:
        source_id = str(row.get("source_document_id") or "")
        if source_id and source_id not in source_documents:
            try:
                document = document_store.get_document(source_id)
            except (ValueError, OSError):
                pass
            else:
                if document.get("last_path") and document.get("system_state") != "webdav_deleted":
                    source_documents[source_id] = document
    top_level = [row for row in rows if not row.get("parent_uid")]
    grouped = {status: [row for row in top_level if row.get("status", "needs-action") == status] for status, _de, _en in BOARD_COLUMNS}
    known_statuses = set(grouped)
    grouped["needs-action"].extend(row for row in top_level if row.get("status", "needs-action") not in known_statuses)
    task_source_documents = _task_source_documents(rows)
    histories: dict[str, list[dict[str, Any]]] = {}
    try:
        from .revision_history import RevisionHistory

        for event in RevisionHistory(current_app.config["DOCUMENT_ROOT"]).events(category="todo", limit=1000):
            key = str(event.get("key", ""))
            if key:
                histories.setdefault(key, []).append(event)
    except (OSError, ValueError):
        histories = {}
    store = _store()
    lists = store.lists(actor)
    editable_lists = {row["list_id"] for row in lists if store._can(row, actor, "edit")}
    due_date_etags = {
        row["id"]: store.etag(row)
        for row in top_level
        if row.get("list_id") in editable_lists and supports_quick_due_change(row)
    }
    return render_template(
        "tasks/board.html",
        columns=BOARD_COLUMNS,
        grouped=grouped,
        children=by_parent,
        source_documents=source_documents,
        lists=lists,
        board_filters=filters,
        due_date_etags=due_date_etags,
        query=request.args.get("q", "").strip(),
        selected_list=request.args.get("list_id", "").strip(),
        assigned=request.args.get("assigned", "").strip(),
        task_source_documents=task_source_documents,
        next_up=suggest_task_next(rows),
        resubmit_views=RESUBMIT_VIEWS,
        selected_view=view,
        view_counts=view_counts,
        histories=histories,
        calendar_sources=_visible_calendar_sources(top_level, actor),
    )


@bp.post("/")
@login_required
def create_task():
    actor = _actor()
    try:
        values = request.form.to_dict()
        values.pop("source_document_id", None)
        values["categories"] = request.form.getlist("categories") or values.get("categories", "")
        values["assigned_to"] = request.form.getlist("assigned_to") or values.get("assigned_to", "")
        _store().add(values.get("title", ""), actor, values)
        flash("Aufgabe angelegt.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))


@bp.post("/<item_id>/due-date")
@login_required
def reschedule_due_date(item_id: str):
    def message(de: str, en: str) -> str:
        return en if g.language == "en" else de

    day = parse_task_day(request.form.get("due", ""))
    if day is None:
        flash(message("Bitte ein gültiges Fälligkeitsdatum wählen.", "Choose a valid due date."), "due-date-warning")
        return redirect(url_for("tasks.board", **_board_filters()), code=303)

    try:
        _store().reschedule_due_date(
            item_id, day, _actor(), expected_etag=request.form.get("task_etag", ""),
        )
        flash(message("Fälligkeit gespeichert.", "Due date saved."), "due-date-success")
    except TodoConflict:
        flash(message("Die Aufgabe wurde inzwischen geändert. Bitte erneut versuchen.",
                      "The task changed in the meantime. Please try again."), "due-date-warning")
    except TodoDueDateBeforeStart:
        flash(message("Die Fälligkeit muss nach dem Startdatum liegen.",
                      "The due date must be after the start date."), "due-date-warning")
    except TodoDueDateError:
        flash(message("Diese Aufgabe lässt sich hier nicht verschieben.",
                      "This task cannot be rescheduled here."), "due-date-warning")
    except ValueError:
        flash(message("Die Aufgabe ist nicht verfügbar oder nicht bearbeitbar.",
                      "The task is unavailable or cannot be edited."), "due-date-warning")
    return redirect(url_for("tasks.board", **_board_filters()), code=303)


@bp.post("/<item_id>/move")
@login_required
def move_task(item_id: str):
    actor = _actor()
    try:
        status = request.form.get("status", "needs-action")
        if status not in {item[0] for item in BOARD_COLUMNS}:
            raise ValueError("Ungültiger Aufgabenstatus")
        if status == "completed":
            flash(_complete_task_occurrence(item_id, actor))
            return redirect(url_for("tasks.board"))
        values: dict[str, Any] = {"status": status}
        if status == "needs-action":
            values["percent_complete"] = 0
        _store().update(item_id, values, actor)
        flash("Aufgabenstatus aktualisiert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))


@bp.post("/<item_id>/documents")
@login_required
def attach_task_document(item_id: str):
    from .document_store import DocumentStore

    try:
        document_id = request.form.get("document_id", "").strip()
        if not document_id:
            raise ValueError("document is required")
        document = DocumentStore(current_app.config["DOCUMENT_ROOT"]).get_document(document_id)
        store = _store()
        rows = store.items(_actor())
        task = next((row for row in rows if row.get("id") == item_id), None)
        if task is None:
            raise ValueError("task not found")
        linked = [entry for entry in task.get("document_ids", []) if entry != document["document_id"]]
        linked.append(document["document_id"])
        store.update(item_id, {"document_ids": linked[:50]}, _actor())
        flash("Dokument verknüpft.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))


@bp.post("/<item_id>/subtasks")
@login_required
def create_subtask(item_id: str):
    actor = _actor()
    try:
        parent = _task_by_id(item_id, actor)
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
        _store().add(
            title,
            actor,
            values,
            smart_folder_id=str(parent.get("smart_folder_id", "")),
        )
        flash("Unteraufgabe angelegt.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))


@bp.post("/<item_id>/complete-occurrence")
@login_required
def complete_occurrence(item_id: str):
    try:
        flash(_complete_task_occurrence(item_id, _actor()))
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("tasks.board"))


@bp.post("/<item_id>/comments")
@login_required
def add_task_comment(item_id: str):
    try:
        _store().add_comment(item_id, request.form.get("text", ""), _actor())
        flash("Kommentar gespeichert.")
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
