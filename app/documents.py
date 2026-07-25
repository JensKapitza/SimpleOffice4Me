"""Authenticated web pages for document versions, notes and audit history."""

from __future__ import annotations

import io
import json
import shutil
from urllib.parse import urlencode
from calendar import month_name, monthcalendar
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .document_store import DocumentStore
from .contact_store import ContactStore
from .calendar_store import CalendarStore
from .todo_store import TodoStore
from .settings_store import SettingsStore
from .db import get_db


bp = Blueprint("documents", __name__, url_prefix="/documents")


def _store() -> DocumentStore:
    return DocumentStore(current_app.config["DOCUMENT_ROOT"])


def _contacts() -> ContactStore:
    return ContactStore(current_app.config["DOCUMENT_ROOT"])


def _calendar() -> CalendarStore:
    return CalendarStore(current_app.config["DOCUMENT_ROOT"])


def _todos() -> TodoStore:
    return TodoStore(current_app.config["DOCUMENT_ROOT"])


def _settings() -> SettingsStore:
    return SettingsStore(current_app.config["DOCUMENT_ROOT"])


def _system_overview() -> dict:
    root = _store().root
    storage = shutil.disk_usage(root)
    devices=[]
    for mount in _store()._mounted_roots():
        try:
            usage=shutil.disk_usage(mount); devices.append({"path":str(mount),"total":usage.total,"free":usage.free})
        except OSError: pass
    return {"time": datetime.now().astimezone(), "root": str(root), "storage": storage, "devices": devices}


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


def _is_unprocessed(document: dict) -> bool:
    """Inbox contains only new files without a human note, state or relation."""
    return document.get("state", "new") == "new" and not document.get("notes") and not document.get("relationships")


@bp.route("/")
@login_required
def index():
    return render_template("documents/index.html", documents=_store().list_documents(), defaults=_settings().settings())


@bp.get("/dashboard")
@login_required
def dashboard():
    documents = _store().list_documents()
    return render_template("documents/dashboard.html", system=_system_overview(), inbox=[item for item in documents if _is_unprocessed(item)], todos=_todos().items(), pending=_calendar().pending_bookings())


@bp.route("/settings")
@login_required
def settings():
    return render_template("documents/settings.html", settings=_settings().settings())


@bp.post("/settings")
@login_required
def save_settings():
    values = {
        "interface": {"default_language": request.form.get("default_language", "de"), "timezone": request.form.get("timezone", "Europe/Berlin")},
        "documents": {"default_state": request.form.get("default_state", "new"), "default_tags": request.form.get("default_tags", "").split(","), "upload_to_archive": request.form.get("upload_to_archive") == "1"},
        "calendar": {"default_visibility": request.form.get("default_visibility", "private"), "default_public_notice": request.form.get("default_public_notice", "Belegt"), "default_duration_minutes": request.form.get("default_duration_minutes", "60")},
        "sharing": {"default_expiry_days": request.form.get("default_expiry_days", "7")},
    }
    try:
        _settings().save(values, str(g.user["username"]))
        flash("Standardwerte gespeichert. Bestehende Daten wurden nicht verändert.")
    except (TypeError, ValueError) as exc:
        flash(str(exc))
    return redirect(url_for("documents.settings"))


@bp.post("/settings/language")
@login_required
def set_language():
    language = request.form.get("language", "de")
    if language in {"de", "en"}:
        from flask import session
        session["simpleoffice_language"] = language
    return redirect(request.referrer or url_for("documents.settings"))


@bp.post("/todo")
@login_required
def add_todo():
    try: _todos().add(request.form.get("title", ""), str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.dashboard"))


@bp.post("/todo/<item_id>/toggle")
@login_required
def toggle_todo(item_id: str):
    try: _todos().toggle(item_id, str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(url_for("documents.dashboard"))


@bp.route("/inbox")
@login_required
def inbox():
    return render_template("documents/index.html", documents=[item for item in _store().list_documents() if _is_unprocessed(item)], inbox_only=True, defaults=_settings().settings())


@bp.route("/images")
@login_required
def images():
    tag = request.args.get("tag", "").strip()
    period = request.args.get("period", "all")
    if period not in {"all", "week", "month", "year"}:
        period = "all"
    now = datetime.now(timezone.utc)

    def in_period(item: dict) -> bool:
        if period == "all":
            return True
        try:
            seen = datetime.fromisoformat(item.get("first_seen_at", "").replace("Z", "+00:00"))
        except ValueError:
            return False
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        if period == "week":
            return seen >= now - timedelta(days=7)
        if period == "month":
            return seen.year == now.year and seen.month == now.month
        return seen.year == now.year

    pictures = [
        item for item in _store().list_documents()
        if item.get("last_path", "").lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp"))
        and (not tag or any(_store().tag_matches(tag, item_tag) for item_tag in item.get("tags", []))) and in_period(item)
    ]
    return render_template("documents/images.html", pictures=pictures, tag=tag, period=period)


@bp.get("/<document_id>/preview")
@login_required
def image_preview(document_id: str):
    document = _document_or_404(document_id); path = _store().root / document.get("last_path", "")
    if not path.is_file() or path.is_symlink(): abort(404)
    return send_file(path)


@bp.post("/<document_id>/tags")
@login_required
def set_document_tags(document_id: str):
    try: _store().set_tags(document_id, request.form.get("tags", "").split(","), str(g.user["username"]))
    except ValueError as exc: flash(str(exc))
    return redirect(request.referrer or url_for("documents.images"))


@bp.post("/upload")
@login_required
def upload():
    files = [item for item in request.files.getlist("files") if item and item.filename]
    if not files:
        flash("Bitte mindestens eine Datei auswählen.")
        return redirect(url_for("documents.index"))
    stored = 0
    defaults = _settings().settings()["documents"]
    for item in files:
        try:
            metadata = _store().import_upload(item, item.filename, str(g.user["username"]), request.form.get("archive") == "1")
            if defaults["default_tags"]:
                _store().set_tags(metadata["document_id"], [*metadata.get("tags", []), *defaults["default_tags"]], str(g.user["username"]))
            if defaults["default_state"] != "new":
                _store().set_state(metadata["document_id"], defaults["default_state"], str(g.user["username"]))
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
    query = request.args.get("link_query", "").strip()
    linked_documents = {item["document_id"]: item for item in store._all_documents()}
    relationships = [{**relationship, "target": linked_documents.get(relationship.get("target_document_id"))} for relationship in document.get("relationships", [])]
    return render_template(
        "documents/detail.html",
        document=document,
        versions=store.versions(document_id),
        logbook=store.logbook(document_id),
        relationships=relationships,
        link_query=query,
        link_matches=[item for item in store.find_matches(query) if item["document_id"] != document_id] if query else [],
        defaults=_settings().settings(),
    )


@bp.post("/<document_id>/relationships")
@login_required
def add_document_relationship(document_id: str):
    _document_or_404(document_id)
    try:
        relation_type = request.form.get("custom_relation_type", "").strip() or request.form.get("relation_type", "related")
        if request.form.get("target", "").strip():
            _store().add_link(document_id, request.form["target"], relation_type, request.form.get("label", ""), str(g.user["username"]))
        else:
            _store().add_text_link(document_id, request.form.get("target_text", ""), relation_type, request.form.get("label", ""), str(g.user["username"]))
        flash("Dokumentverknüpfung gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.detail", document_id=document_id, link_query=request.form.get("link_query", "")))


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
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    filters = {key: request.args.get(key, "").strip() for key in ("q", "actor", "action", "from_at", "to_at")}
    result = _store().logbook_page(page=page, query=filters["q"], actor=filters["actor"], action=filters["action"], from_at=filters["from_at"], to_at=filters["to_at"])
    return render_template("documents/logbook.html", events=result["events"], page=result["page"], has_next=result["has_next"], filters=filters)


@bp.route("/archives")
@login_required
def archives():
    return render_template("documents/archives.html", main_archive=_store().main_archive(), archives=_store().archives())


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


@bp.post("/sources/ssh/<source_id>/remove")
@login_required
def remove_ssh_source(source_id: str):
    try:
        _store().remove_ssh_source(source_id, str(g.user["username"]))
        flash("SSH-Quelle entfernt. Auf dem entfernten System und im Archiv wurden keine Dateien gelöscht.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.ssh_sources"))


@bp.route("/contacts")
@login_required
def contacts():
    actor = str(g.user["username"])
    contacts = _contacts().contacts(actor)
    address_values = sorted({address.get("value", "") for contact in contacts for address in contact.get("addresses", []) if address.get("value")}, key=str.casefold)
    carddav_endpoint = url_for("carddav.endpoint", path=f"addressbooks/{g.user['username']}/default/", _external=True)
    return render_template("documents/contacts.html", contacts=contacts, schema=_contacts().schema(), carddav=_contacts().carddav(), carddav_endpoint=carddav_endpoint, address_matches=_contacts().address_matches(), address_values=address_values)


@bp.get("/contacts/<contact_id>")
@login_required
def contact_detail(contact_id: str):
    actor = str(g.user["username"])
    try:
        contact = _contacts().get(contact_id, actor)
    except ValueError:
        abort(404)
    users = [row["username"] for row in get_db().execute("SELECT username FROM user ORDER BY username COLLATE NOCASE").fetchall()]
    return render_template("documents/contact_detail.html", contact=contact, users=users, is_owner=not contact.get("owner") or contact.get("owner") == actor)


@bp.post("/contacts")
@login_required
def save_contact():
    contact_id = request.form.get("contact_id", "")
    try:
        contact = _contacts().upsert(request.form.to_dict(), str(g.user["username"]), contact_id)
        flash("Kontakt gespeichert.")
    except ValueError as exc:
        flash(str(exc))
        contact = None
    if contact_id and contact is not None:
        return redirect(url_for("documents.contact_detail", contact_id=contact["contact_id"]))
    return redirect(url_for("documents.contacts"))


@bp.get("/contacts/export.vcf")
@login_required
def export_contacts():
    payload = _contacts().export_vcards(str(g.user["username"])).encode("utf-8")
    return send_file(io.BytesIO(payload), as_attachment=True, download_name="simpleoffice-kontakte.vcf", mimetype="text/vcard; charset=utf-8")


@bp.post("/contacts/import")
@login_required
def import_contacts():
    uploaded = request.files.get("contacts_file")
    if uploaded is None or not uploaded.filename:
        flash("Bitte eine .vcf-Datei auswählen.")
        return redirect(url_for("documents.contacts"))
    try:
        imported = _contacts().import_vcards(uploaded.read().decode("utf-8-sig"), str(g.user["username"]))
        flash(f"{imported} Kontakt(e) importiert.")
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Kontaktimport fehlgeschlagen: {exc}")
    return redirect(url_for("documents.contacts"))


@bp.post("/contacts/<contact_id>/addresses")
@login_required
def add_contact_address(contact_id: str):
    try:
        _contacts().add_address(contact_id, request.form.get("label", ""), request.form.get("address", ""), str(g.user["username"]))
        flash("Adresse gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contact_detail", contact_id=contact_id))


@bp.post("/contacts/<contact_id>/sharing")
@login_required
def share_contact(contact_id: str):
    actor = str(g.user["username"])
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    managers = request.form.getlist("managers")
    unknown = sorted(set(managers) - valid_users)
    try:
        if unknown:
            raise ValueError(f"unknown users: {', '.join(unknown)}")
        _contacts().share(contact_id, managers, actor)
        flash("Verwaltungsfreigabe gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.contact_detail", contact_id=contact_id))


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
    actor = str(g.user["username"])
    requested_month = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        shown_month = date.fromisoformat(f"{requested_month}-01")
    except ValueError:
        shown_month = date.today().replace(day=1)
    events_by_day: dict[int, list[dict]] = {}
    events = [event for event in _calendar().events(actor) if event.get("status", "active") not in {"cancelled", "deleted", "moved"}]
    for event in events:
        try:
            event_day = datetime.fromisoformat(event["start"].replace("Z", "+00:00")).date()
        except (KeyError, ValueError):
            continue
        if event_day.year == shown_month.year and event_day.month == shown_month.month:
            events_by_day.setdefault(event_day.day, []).append(event)
    previous = (shown_month.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    following = (shown_month.replace(day=28) + timedelta(days=4)).replace(day=1).strftime("%Y-%m")
    users = [row["username"] for row in get_db().execute("SELECT username FROM user ORDER BY username COLLATE NOCASE").fetchall()]
    for event in events:
        if event.get("status") == "confirmed" and event.get("requester_email"):
            ics_url = url_for("documents.download_booking_confirmation", event_id=event["event_id"], _external=True)
            subject = f"Terminbestätigung: {event['title']}"
            body = f"Hallo {event.get('requester_name') or ''},\n\ndein Termin wurde bestätigt. Die Kalendereinladung kannst du hier herunterladen:\n{ics_url}\n"
            event["confirmation_mailto"] = "mailto:" + event["requester_email"] + "?" + urlencode({"subject": subject, "body": body})
    return render_template("documents/calendar.html", events=events, contacts=_contacts().contacts(actor), users=users, current_username=actor, booking=_calendar().booking_settings(), pending=_calendar().pending_bookings(), defaults=_settings().settings(), calendar_weeks=monthcalendar(shown_month.year, shown_month.month), calendar_events=events_by_day, shown_month=shown_month.strftime("%Y-%m"), shown_month_name=f"{month_name[shown_month.month]} {shown_month.year}", previous_month=previous, following_month=following)


@bp.get("/calendar/export.ics")
@login_required
def export_calendar():
    payload = _calendar().export_ics(str(g.user["username"])).encode("utf-8")
    return send_file(io.BytesIO(payload), as_attachment=True, download_name="simpleoffice-kalender.ics", mimetype="text/calendar; charset=utf-8")


@bp.post("/calendar/import")
@login_required
def import_calendar():
    uploaded = request.files.get("calendar_file")
    if uploaded is None or not uploaded.filename:
        flash("Bitte eine .ics-Datei auswählen.")
        return redirect(url_for("documents.calendar"))
    try:
        imported = _calendar().import_ics(uploaded.read().decode("utf-8-sig"), str(g.user["username"]))
        flash(f"{imported} Kalendertermin(e) importiert.")
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Kalenderimport fehlgeschlagen: {exc}")
    return redirect(url_for("documents.calendar"))


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


@bp.post("/calendar/<event_id>/sharing")
@login_required
def share_calendar_event(event_id: str):
    actor = str(g.user["username"])
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    managers = request.form.getlist("managers")
    unknown = sorted(set(managers) - valid_users)
    try:
        if unknown:
            raise ValueError(f"unknown users: {', '.join(unknown)}")
        _calendar().share(event_id, managers, actor)
        flash("Verwaltungsfreigabe für den Termin gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


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
        event = _calendar().confirm_booking(event_id, str(g.user["username"]))
        if event.get("confirmation_delivery", {}).get("status") == "sent":
            flash("Buchung bestätigt und ICS-E-Mail versendet.")
        else:
            flash("Buchung bestätigt und verbindlich blockiert. E-Mail-Versand ist ausstehend; die ICS-Datei kann im Termin heruntergeladen werden.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.get("/calendar/bookings/<event_id>/confirmation.ics")
@login_required
def download_booking_confirmation(event_id: str):
    try:
        payload = _calendar().booking_ics(event_id, str(g.user["username"])).encode("utf-8")
    except ValueError:
        abort(404)
    return send_file(io.BytesIO(payload), as_attachment=True, download_name=f"terminbestaetigung-{event_id}.ics", mimetype="text/calendar; charset=utf-8")


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
        card = _contacts().vcard(contact_id, str(g.user["username"]))
    except ValueError:
        abort(404)
    return Response(card, mimetype="text/vcard", headers={"Content-Disposition": f'attachment; filename="contact-{contact_id}.vcf"'})
