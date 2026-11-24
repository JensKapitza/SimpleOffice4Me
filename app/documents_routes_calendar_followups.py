"""Create an open task from an authorized calendar appointment."""
from __future__ import annotations

from datetime import date

from flask import abort, flash, g, redirect, request, url_for

from .auth import login_required
from .calendar_followups import readable_followup_source, single_followup_source
from .documents_core import _calendar, _calendars, _todos, bp


def _source_event(event_id: str, actor: str) -> tuple[dict, set[str]]:
    try:
        event = _calendar().get(event_id, actor)
    except ValueError:
        abort(404)
    readable_calendars = {item["calendar_id"] for item in _calendars().calendars(actor)}
    if not readable_followup_source(event, readable_calendars):
        abort(404)
    return event, readable_calendars


@bp.get("/calendar/<event_id>/followup-source")
@login_required
def open_calendar_followup_source(event_id: str):
    event, _ = _source_event(event_id, str(g.user["username"]))
    return redirect(url_for("documents.calendar", _anchor=f"calendar-event-{event['event_id']}"))


def _form_text(name: str) -> str:
    return request.form.get(name, "").replace("\r\n", "\n").replace("\r", "\n").strip()


@bp.post("/calendar/<event_id>/followup")
@login_required
def create_calendar_followup(event_id: str):
    actor = str(g.user["username"])
    english = g.language == "en"
    event, readable_calendars = _source_event(event_id, actor)
    if not single_followup_source(event, readable_calendars):
        abort(404)

    raw_date = request.form.get("due", "")
    try:
        if len(raw_date) != 10 or date.fromisoformat(raw_date).isoformat() != raw_date:
            raise ValueError("invalid date")
    except ValueError:
        flash("Choose a valid due date." if english else "Bitte ein gültiges Fälligkeitsdatum wählen.")
        return redirect(url_for("documents.calendar"), code=303)

    task_title = _form_text("task_title") or (
        "Follow up on appointment" if english else "Wiedervorlage zum Termin"
    )
    note = _form_text("note")
    if len(task_title) > 300 or len(note) > 2000:
        flash("The task title or note is too long." if english else "Der Aufgabentitel oder die Notiz ist zu lang.")
        return redirect(url_for("documents.calendar"), code=303)
    task = _todos().add(
        task_title, actor, {"due": raw_date, "description": note, "categories": ["Wiedervorlage"]},
        calendar_followup_event_id=event["event_id"],
    )
    return redirect(url_for("tasks.board", _anchor=f"task-{task['id']}"), code=303)
