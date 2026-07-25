"""Authenticated web pages for document versions, notes and audit history."""

from __future__ import annotations

import json

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .document_store import DocumentStore
from .contact_store import ContactStore
from .calendar_store import CalendarStore


bp = Blueprint("documents", __name__, url_prefix="/documents")


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _contacts() -> ContactStore:
    return ContactStore(current_app.config["DOCUMENT_ROOT"])


def _calendar() -> CalendarStore:
    return CalendarStore(current_app.config["DOCUMENT_ROOT"])


def _calendar_tags() -> list[dict[str, str]]:
    return [
        {"name": tag.strip(), "visibility": visibility}
        for visibility in ("private", "family", "external")
        for tag in request.form.get(f"{visibility}_tags", "").split(",")
        if tag.strip()
    ]


def _document_or_404(document_id: str) -> dict:
    try:
        return _store().get_document(document_id)
    except ValueError:
        abort(404)


@bp.route("/")
@login_required
def index():
    return render_template("documents/index.html", documents=_store().list_documents())


@bp.post("/upload")
@login_required
def upload():
    files = [item for item in request.files.getlist("files") if item and item.filename]
    if not files:
        flash("Bitte mindestens eine Datei auswählen.")
        return redirect(url_for("documents.index"))
    stored = 0
    for item in files:
        try:
            _store().import_upload(item, item.filename, str(g.user["username"]), request.form.get("archive") == "1")
            stored += 1
        except (OSError, ValueError) as exc:
            flash(f"{item.filename}: {exc}")
    if stored:
        flash(f"{stored} Datei(en) vollständig und hashbasiert importiert.")
    return redirect(url_for("documents.index"))


@bp.route("/<document_id>")
@login_required
def detail(document_id: str):
    document = _document_or_404(document_id)
    store = _store()
    return render_template(
        "documents/detail.html",
        document=document,
        versions=store.versions(document_id),
        logbook=store.logbook(document_id),
    )


@bp.post("/<document_id>/notes")
@login_required
def add_note(document_id: str):
    _document_or_404(document_id)
    try:
        _store().add_note(document_id, request.form.get("text", ""), str(g.user["username"]))
        flash("Notiz wurde als eigene Revision gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.get("/<document_id>/notes/<note_id>/snapshot.pdf")
@login_required
def download_note_snapshot(document_id: str, note_id: str):
    try:
        path = _store().note_snapshot(document_id, note_id)
        return send_file(path, as_attachment=True, download_name=f"notiz-{note_id}.pdf", mimetype="application/pdf")
    except (OSError, RuntimeError, ValueError):
        abort(404)


@bp.post("/<document_id>/state")
@login_required
def set_state(document_id: str):
    _document_or_404(document_id)
    try:
        _store().set_state(document_id, request.form.get("state", ""), str(g.user["username"]))
        flash("Zustand wurde als eigene Revision gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/<document_id>/share")
@login_required
def create_share(document_id: str):
    _document_or_404(document_id)
    try:
        share = _store().create_share(
            document_id,
            request.form.get("password", ""),
            int(request.form.get("expires_days", "7")),
            str(g.user["username"]),
            request.form.get("note_id", ""),
        )
        flash(f"HTTPS-Link (nur jetzt vollständig sichtbar): {url_for('documents.open_share', share_id=share['share_id'], _external=True)}")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.post("/<document_id>/offload-versions")
@login_required
def offload_versions(document_id: str):
    _document_or_404(document_id)
    if request.form.get("confirm") != "AUSLAGERN":
        flash("Zum Auslagern muss AUSLAGERN bestätigt werden.")
        return redirect(url_for("documents.detail", document_id=document_id))
    try:
        result = _store().offload_old_versions(document_id, request.form.get("archive_path", ""), str(g.user["username"]))
        flash(f"{len(result['moved_document_ids'])} alte Version(en) auf {result['archive']['label']} ausgelagert. Die aktuelle Version bleibt lokal.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id))


@bp.route("/share/<share_id>", methods=("GET", "POST"))
def open_share(share_id: str):
    if request.method == "GET":
        return render_template("documents/share.html", share_id=share_id)
    try:
        opened = _store().open_share(share_id, request.form.get("password", ""))
    except ValueError as exc:
        return render_template("documents/share.html", share_id=share_id, error=str(exc)), 403
    if "note" in opened:
        return render_template("documents/shared_note.html", note=opened["note"], document=opened["document"], share=opened["share"])
    return send_file(opened["path"], as_attachment=True, download_name=opened["path"].name)


@bp.route("/wiki/notes")
@login_required
def notes_wiki():
    return render_template("documents/notes.html", notes=_store().note_wiki())


@bp.route("/logbook")
@login_required
def logbook():
    return render_template("documents/logbook.html", events=_store().logbook())


@bp.route("/archives")
@login_required
def archives():
    return render_template("documents/archives.html", archives=_store().archives())


@bp.post("/archives/register")
@login_required
def register_archive():
    try:
        tags = [tag.strip() for tag in request.form.get("tags", "").split(",") if tag.strip()]
        _store().register_external_archive(request.form.get("path", ""), request.form.get("label", ""), tags, str(g.user["username"]))
        flash("Externes Archiv wurde markiert und registriert.")
    except (OSError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.archives"))


@bp.post("/archives/discover")
@login_required
def discover_archives():
    _store().discover_archives(str(g.user["username"]))
    flash("Angeschlossene Laufwerke wurden nach Archivmarkern geprüft.")
    return redirect(url_for("documents.archives"))


@bp.route("/sources/ssh")
@login_required
def ssh_sources():
    return render_template("documents/ssh_sources.html", sources=_store().ssh_sources())


@bp.post("/sources/ssh")
@login_required
def register_ssh_source():
    try:
        _store().register_ssh_source(
            request.form.get("name", ""), request.form.get("host", ""), request.form.get("username", ""),
            request.form.get("remote_path", ""), request.form.get("key_path", ""), str(g.user["username"]),
        )
        flash("SSH-Quelle registriert. Für die Synchronisation wird ein SSH-Schlüssel verwendet.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.ssh_sources"))


@bp.post("/sources/ssh/<source_id>/sync")
@login_required
def sync_ssh_source(source_id: str):
    try:
        imported = _store().sync_ssh_source(source_id, str(g.user["username"]))
        flash(f"SSH-Import abgeschlossen: {imported} Datei(en) übernommen.")
    except (OSError, RuntimeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.ssh_sources"))


@bp.route("/contacts")
@login_required
def contacts():
    return render_template("documents/contacts.html", contacts=_contacts().contacts(), schema=_contacts().schema(), carddav=_contacts().carddav())


@bp.post("/contacts")
@login_required
def save_contact():
    try:
        _contacts().upsert(request.form.to_dict(), str(g.user["username"]), request.form.get("contact_id", ""))
        flash("Kontakt gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contacts"))


@bp.post("/contacts/schema")
@login_required
def save_contact_schema():
    try:
        aliases = json.loads(request.form.get("aliases", "{}"))
        if not isinstance(aliases, dict) or not all(isinstance(value, list) for value in aliases.values()):
            raise ValueError("aliases must be a JSON object whose values are lists")
        _contacts().save_schema(request.form.get("required", "").split(","), aliases, str(g.user["username"]))
        flash("Kontaktfeld-Zuordnung gespeichert.")
    except (json.JSONDecodeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.contacts"))


@bp.post("/contacts/carddav")
@login_required
def activate_carddav():
    try:
        _contacts().activate_carddav(str(g.user["username"]), request.form.get("password", ""), str(g.user["username"]))
        endpoint = url_for("carddav.endpoint", path=f"addressbooks/{g.user['username']}/default/", _external=True)
        flash(f"CardDAV aktiviert. Thunderbird-URL: {endpoint}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contacts"))


@bp.route("/calendar")
@login_required
def calendar():
    return render_template("documents/calendar.html", events=_calendar().events(), contacts=_contacts().contacts(), booking=_calendar().booking_settings(), pending=_calendar().pending_bookings())


@bp.post("/calendar")
@login_required
def add_calendar_event():
    try:
        _calendar().add(request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), str(g.user["username"]), request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags())
        flash("Kalendertermin gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>")
@login_required
def update_calendar_event(event_id: str):
    try:
        _calendar().update(event_id, request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), str(g.user["username"]), request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags())
        flash("Kalendertermin geändert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>/delete")
@login_required
def delete_calendar_event(event_id: str):
    try:
        _calendar().delete(event_id, str(g.user["username"]))
        flash("Kalendertermin gelöscht.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.get("/calendar/published/<audience>")
def published_calendar(audience: str):
    try:
        return render_template("documents/published_calendar.html", audience=audience, events=_calendar().visible_events(audience))
    except ValueError:
        abort(404)


@bp.post("/calendar/booking-settings")
@login_required
def save_booking_settings():
    try:
        _calendar().save_booking_settings(request.form.get("enabled") == "1", int(request.form.get("duration_minutes", "60")), request.form.get("start_time", "09:00"), request.form.get("end_time", "17:00"), str(g.user["username"]))
        flash("Externe Buchungseinstellungen gespeichert.")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/bookings/<event_id>/confirm")
@login_required
def confirm_booking(event_id: str):
    try:
        _calendar().confirm_booking(event_id, str(g.user["username"]))
        flash("Buchung bestätigt und ICS-E-Mail versendet.")
    except (RuntimeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.route("/calendar/book", methods=("GET", "POST"))
def book_calendar_slot():
    from datetime import date
    selected_day = request.values.get("date", date.today().isoformat())
    try:
        slots = _calendar().available_slots(date.fromisoformat(selected_day))
        if request.method == "POST":
            _calendar().request_booking(request.form.get("title", ""), request.form.get("reason", ""), request.form.get("name", ""), request.form.get("email", ""), request.form.get("start", ""), request.form.get("end", ""))
            return render_template("documents/book_calendar.html", date=selected_day, slots=slots, sent=True)
        return render_template("documents/book_calendar.html", date=selected_day, slots=slots)
    except ValueError as exc:
        return render_template("documents/book_calendar.html", date=selected_day, slots=[], error=str(exc)), 400


@bp.get("/contacts/<contact_id>.vcf")
@login_required
def download_contact_vcard(contact_id: str):
    try:
        card = _contacts().vcard(contact_id)
    except ValueError:
        abort(404)
    return Response(card, mimetype="text/vcard", headers={"Content-Disposition": f'attachment; filename="contact-{contact_id}.vcf"'})
