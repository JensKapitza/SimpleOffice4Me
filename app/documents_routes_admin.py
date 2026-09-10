"""Document HTTP route group extracted from app.documents."""
from __future__ import annotations

from .documents_core import *  # noqa: F401,F403

@bp.post("/calendar/<event_id>/invite-email")
@login_required
def send_calendar_invitation(event_id: str):
    actor = str(g.user["username"])
    recipient = request.form.get("recipient", "").strip()
    try:
        event = next(item for item in _calendar().events(actor) if item.get("event_id") == event_id)
        if not _calendar()._can_edit(event, actor):
            raise PermissionError("Für diesen Termin fehlt das Bearbeitungsrecht.")
        store = _mail()
        account = store.smtp_account(actor, request.form.get("account_id", ""), request.form.get("smtp_password", ""))
        calendar_data = _itip().export(event_id, actor, "REQUEST", recipient, "", str(g.user["email"] or account.get("smtp_from", "")))
        result = SmtpSubmission(store).send(
            actor, account, recipient, f"Termineinladung: {event.get('title', 'Termin')}",
            request.form.get("body", "Bitte den beigefügten Termin prüfen."), calendar_data,
        )
        flash(f"Einladung an {result['recipients']} Empfänger versandt und als EML archiviert.")
    except StopIteration:
        flash("Termin wurde nicht gefunden.")
    except Exception as exc:
        current_app.logger.warning("Calendar invitation submission failed for %s: %s", actor, type(exc).__name__)
        flash(f"Einladung konnte nicht versandt werden: {exc}")
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.post("/calendar/google/preview")
@login_required
def preview_google_calendar_sync():
    actor = str(g.user["username"])
    try:
        status = _google_calendar().status(actor)
        _calendars().get(status["target_calendar_id"], actor, write=True)
        result = _google_calendar().synchronize(actor, apply=False)
        flash(f"Google-Vorschau: {result['received']} Änderungen empfangen, {result['applicable']} anwendbar, {len(result['conflicts'])} Konflikte. Kalenderdaten und Sync-Token blieben unverändert.")
    except (GoogleCalendarError, ValueError) as exc:
        flash(f"Google-Kalender konnte nicht geprüft werden: {exc}")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/google/sync")
@login_required
def apply_google_calendar_sync():
    actor = str(g.user["username"])
    try:
        status = _google_calendar().status(actor)
        _calendars().get(status["target_calendar_id"], actor, write=True)
        result = _google_calendar().synchronize(actor, apply=True)
        if result["conflicts"]:
            flash(f"Google-Abgleich: {result['applied']} Änderungen gespeichert; {len(result['conflicts'])} lokale Konflikte blieben unverändert. Bitte zuerst manuell auflösen.")
        else:
            flash(f"Google-Abgleich abgeschlossen: {result['applied']} Änderungen gespeichert, keine Konflikte.")
    except (GoogleCalendarError, ValueError) as exc:
        flash(f"Google-Kalender wurde nicht geändert: {exc}")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/google/conflicts/<strategy>")
@login_required
def resolve_google_calendar_conflicts(strategy: str):
    actor = str(g.user["username"])
    try:
        status = _google_calendar().status(actor)
        _calendars().get(status["target_calendar_id"], actor, write=True)
        result = _google_calendar().synchronize(actor, apply=True, conflict_policy=strategy)
        flash(f"Google-Konflikte aufgelöst: {result['applied']} Google-Versionen übernommen, {result['kept_local']} lokale Versionen beibehalten.")
    except (GoogleCalendarError, ValueError) as exc:
        flash(f"Google-Konflikte wurden nicht aufgelöst: {exc}")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/google/reset")
@login_required
def reset_google_calendar_sync():
    _google_calendar().disable(str(g.user["username"]))
    flash("Google-Sync-Zustand entfernt. Importierte Termine bleiben erhalten; der nächste Abgleich prüft den Kalender vollständig.")
    return redirect(url_for("documents.calendar") + "#google-calendar-sync")


@bp.post("/calendar/scheduling/import")
@login_required
def import_itip_message():
    uploaded = request.files.get("itip_file")
    try:
        if uploaded is None or not uploaded.filename:
            raise ValueError("Bitte eine iTIP-/ICS-Datei auswählen.")
        payload = uploaded.stream.read(MAX_MESSAGE_BYTES + 1)
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ValueError("iTIP message exceeds 1 MiB")
        message = _itip().receive(payload.decode("utf-8-sig"), str(g.user["username"]), "file-import")
        flash(f"{message['method']}-Nachricht geprüft und zur Bestätigung vorgemerkt.")
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Termin-Nachricht abgewiesen: {exc}")
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.post("/calendar/scheduling/<message_id>/apply")
@login_required
def apply_itip_message(message_id: str):
    try:
        _itip().apply(message_id, str(g.user["username"]), request.form.get("calendar_id", "default"))
        flash("Termin-Nachricht angewendet und revisionssicher protokolliert.")
    except (ItipConflict, ValueError) as exc:
        flash(f"Termin-Nachricht konnte nicht angewendet werden: {exc}")
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.post("/calendar/scheduling/<message_id>/reject")
@login_required
def reject_itip_message(message_id: str):
    try:
        _itip().reject(message_id, str(g.user["username"]), request.form.get("reason", ""))
        flash("Termin-Nachricht abgelehnt; Kalenderdaten blieben unverändert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#scheduling")


@bp.get("/calendar/<event_id>/scheduling.ics")
@login_required
def export_itip_message(event_id: str):
    method = request.args.get("method", "REQUEST")
    try:
        payload = _itip().export(event_id, str(g.user["username"]), method, request.args.get("attendee", ""), request.args.get("partstat", ""), str(g.user["email"] or ""))
    except ValueError as exc:
        return Response("calendar export is not permitted", 403, {"Content-Type": "text/plain; charset=utf-8"})
    return send_file(io.BytesIO(payload.encode()), as_attachment=True, download_name=f"termin-{method.casefold()}-{event_id}.ics", mimetype=f"text/calendar; method={method.upper()}; charset=utf-8")


@bp.post("/calendar/scheduling/access")
@login_required
def update_caldav_scheduling_access():
    actor = str(g.user["username"])
    users = {str(row["username"]) for row in get_db().execute("SELECT username FROM user").fetchall()}
    try:
        _scheduling_access().update(
            actor,
            request.form.get("enabled") == "1",
            [username for username in users if request.form.get(f"messages_{username}") == "1"],
            [username for username in users if request.form.get(f"freebusy_{username}") == "1"],
            users,
        )
        flash("CalDAV-Terminplanung und Freigaben wurden gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#scheduling-access")


@bp.post("/calendar/caldav")
@login_required
def activate_caldav():
    actor = str(g.user["username"])
    try:
        _calendars().activate(actor, request.form.get("password", ""), actor)
        flash(f"CalDAV aktiviert. Thunderbird-URL: {url_for('caldav.endpoint', path='', _external=True)}")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#caldav")


@bp.post("/calendar/collections")
@login_required
def create_calendar_collection():
    actor = str(g.user["username"])
    try:
        _calendars().create(request.form.get("name", ""), actor, request.form.get("color", "#2563eb"), request.form.get("timezone", "Europe/Berlin"), request.form.get("description", ""))
        flash("Kalender angelegt.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#caldav")


@bp.post("/calendar/collections/<calendar_id>/sharing")
@login_required
def share_calendar_collection(calendar_id: str):
    actor = str(g.user["username"]); valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    try:
        _calendars().update_sharing(calendar_id, {user: request.form.get(f"access_{user}", "") for user in valid_users}, actor)
        flash("Kalenderfreigaben gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + "#caldav")


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


@bp.post("/calendar/import/preview")
@login_required
def preview_calendar_import():
    uploaded = request.files.get("calendar_file")
    if uploaded is None or not uploaded.filename:
        flash("Bitte eine .ics-Datei auswählen.")
        return redirect(url_for("documents.calendar") + "#calendar-import")
    try:
        payload = uploaded.stream.read(MAX_PREVIEW_BYTES + 1)
        if len(payload) > MAX_PREVIEW_BYTES:
            raise ValueError(f"iCalendar preview is limited to {MAX_PREVIEW_BYTES // 1024} KiB")
        preview = preview_ics(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        flash(f"Kalendervorschau fehlgeschlagen: {exc}")
        return redirect(url_for("documents.calendar") + "#calendar-import")
    return render_template(
        "documents/calendar_import_preview.html",
        preview=preview,
        filename=uploaded.filename,
    )


@bp.post("/calendar")
@login_required
def add_calendar_event():
    actor = str(g.user["username"])
    owner = request.form.get("owner", actor).strip() or actor
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    try:
        if owner not in valid_users:
            raise ValueError("unknown owner")
        calendar_id = request.form.get("calendar_id", "default")
        _calendars().get(calendar_id, actor, write=True)
        metadata = {**_calendar_metadata(), "description_html": request.form.get("description_html", ""), "description_format": request.form.get("description_format", "text")}
        event = _calendar().add(request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), actor, request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags(), owner, calendar_id, metadata)
        if request.form.get("rrule", "").strip() or request.form.get("rdates", "").strip():
            event = _calendar().set_recurrence(event["event_id"], {"rrule": request.form.get("rrule", ""), "rdates": request.form.get("rdates", "").splitlines(), "exdates": request.form.get("exdates", "").splitlines(), "timezone": request.form.get("recurrence_timezone", "Europe/Berlin")}, actor, event.get("updated_at", ""))
        _calendars().record_event_move(event, calendar_id, actor)
        flash("Kalendertermin gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>")
@login_required
def update_calendar_event(event_id: str):
    try:
        actor = str(g.user["username"]); calendar_id = request.form.get("calendar_id", "")
        if calendar_id: _calendars().get(calendar_id, actor, write=True)
        source_calendar_id = _calendar().get(event_id, actor).get("calendar_id") or "default"
        metadata = {**_calendar_metadata(), "description_html": request.form.get("description_html", ""), "description_format": request.form.get("description_format", "text")}
        event = _calendar().update(event_id, request.form.get("title", ""), request.form.get("reason", ""), request.form.get("start", ""), request.form.get("end", ""), request.form.get("contact_id", ""), actor, request.form.get("visibility", "private"), request.form.get("public_notice", ""), _calendar_tags(), calendar_id, metadata)
        _calendars().record_event_move(event, source_calendar_id, actor)
        flash("Kalendertermin geändert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>/participants")
@login_required
def update_calendar_participants(event_id: str):
    participants = []
    try:
        for line in request.form.get("participants", "").splitlines():
            if not line.strip(): continue
            email, name, role, status, rsvp = (line.split("|") + ["", "", "", "", ""])[:5]
            participants.append({"email": email.strip(), "name": name.strip(), "role": role.strip() or "required", "status": status.strip() or "needs-action", "rsvp": rsvp.strip().lower() in {"1", "true", "ja", "yes"}})
        _calendar().set_participants(event_id, participants, str(g.user["username"]))
        flash("Teilnehmer gespeichert.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.post("/calendar/<event_id>/recurrence")
@login_required
def update_calendar_recurrence(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor); calendar_id = previous.get("calendar_id") or "default"
        event = _calendar().set_recurrence(event_id, {"rrule": request.form.get("rrule", ""), "rdates": request.form.get("rdates", "").splitlines(), "exdates": request.form.get("exdates", "").splitlines(), "timezone": request.form.get("recurrence_timezone", "")}, actor, request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, calendar_id, actor)
        flash("Serienregel gespeichert und für CalDAV synchronisiert.")
    except ValueError as exc:
        flash(f"Serienregel nicht gespeichert: {exc}")
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.post("/calendar/<event_id>/occurrence")
@login_required
def update_calendar_occurrence(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor); calendar_id = previous.get("calendar_id") or "default"
        event = _calendar().set_occurrence_exception(event_id, request.form.get("recurrence_id", ""), actor, status=request.form.get("occurrence_status", "active"), start=request.form.get("occurrence_start", ""), end=request.form.get("occurrence_end", ""), title=request.form.get("occurrence_title", ""), reason=request.form.get("occurrence_reason", ""), expected_updated_at=request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, calendar_id, actor)
        flash("Einzelne Serieninstanz revisionssicher geändert.")
    except ValueError as exc:
        flash(f"Serieninstanz nicht geändert: {exc}")
    return redirect(url_for("documents.calendar") + f"#event-{event_id}")


@bp.post("/calendar/<event_id>/alarms")
@login_required
def add_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor)
        minutes = int(request.form.get("minutes", "15"))
        if not 0 <= minutes <= 527040:
            raise ValueError("Erinnerungsabstand muss zwischen 0 und 527040 Minuten liegen.")
        direction = request.form.get("direction", "before")
        related = request.form.get("related", "start")
        if direction not in {"before", "after"} or related not in {"start", "end"}:
            raise ValueError("Ungültiger Erinnerungsbezug.")
        alarms = list(previous.get("alarms", []))
        alarms.append({"action": "DISPLAY", "description": request.form.get("description", "").strip() or previous.get("title", "Erinnerung"), "trigger": {"kind": "relative", "seconds": minutes * 60 * (-1 if direction == "before" else 1), "related": related}})
        event = _calendar().set_alarms(event_id, alarms, actor, request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Lokale Kalendererinnerung gespeichert und für CalDAV synchronisiert.")
    except (TypeError, ValueError) as exc:
        flash(f"Erinnerung nicht gespeichert: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.post("/calendar/<event_id>/alarms/delete")
@login_required
def delete_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor); alarm_uid = request.form.get("alarm_uid", "")
        alarms = [item for item in previous.get("alarms", []) if item.get("uid") != alarm_uid]
        if len(alarms) == len(previous.get("alarms", [])):
            raise ValueError("Unbekannte Kalendererinnerung.")
        event = _calendar().set_alarms(event_id, alarms, actor, request.form.get("expected_updated_at", ""))
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Kalendererinnerung entfernt.")
    except ValueError as exc:
        flash(f"Erinnerung nicht entfernt: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.post("/calendar/<event_id>/alarms/acknowledge")
@login_required
def acknowledge_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor)
        event = _calendar().acknowledge_alarm(event_id, request.form.get("alarm_uid", ""), actor)
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Erinnerung bestätigt.")
    except ValueError as exc:
        flash(f"Erinnerung nicht bestätigt: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.post("/calendar/<event_id>/alarms/snooze")
@login_required
def snooze_calendar_alarm(event_id: str):
    actor = str(g.user["username"])
    try:
        previous = _calendar().get(event_id, actor)
        event = _calendar().snooze_alarm(event_id, request.form.get("alarm_uid", ""), actor, int(request.form.get("minutes", "10")))
        _calendars().record_event_move(event, previous.get("calendar_id") or "default", actor)
        flash("Erinnerung wurde verschoben.")
    except (TypeError, ValueError) as exc:
        flash(f"Erinnerung nicht verschoben: {exc}")
    return redirect(url_for("documents.calendar") + "#reminders")


@bp.get("/calendar/reminders.json")
@login_required
def calendar_reminders_json():
    now = datetime.now(timezone.utc)
    try:
        lower = datetime.fromisoformat(request.args.get("from", "").replace("Z", "+00:00")) if request.args.get("from") else now - timedelta(hours=12)
        upper = datetime.fromisoformat(request.args.get("to", "").replace("Z", "+00:00")) if request.args.get("to") else now + timedelta(days=7)
        rows = _calendar().due_alarms(str(g.user["username"]), lower, upper, request.args.get("calendar_id", ""))
        return Response(json.dumps({"generated_at": now.isoformat(timespec="seconds"), "reminders": rows}, ensure_ascii=False), mimetype="application/json")
    except ValueError as exc:
        return Response(json.dumps({"error": str(exc)}, ensure_ascii=False), 400, mimetype="application/json")


@bp.post("/calendar/<event_id>/delete")
@login_required
def delete_calendar_event(event_id: str):
    try:
        result = _calendar().delete(event_id, str(g.user["username"]))
        flash("Terminserie ab heute gelöscht; vergangene Termine bleiben erhalten." if result == "series_truncated" else "Kalendertermin gelöscht.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("documents.calendar"))


@bp.post("/calendar/<event_id>/sharing")
@login_required
def share_calendar_event(event_id: str):
    actor = str(g.user["username"])
    valid_users = {row["username"] for row in get_db().execute("SELECT username FROM user").fetchall()}
    permissions = {username: request.form.get(f"access_{username}", "") for username in valid_users}
    unknown = sorted(set(request.form.getlist("users")) - valid_users)
    try:
        if unknown:
            raise ValueError(f"unknown users: {', '.join(unknown)}")
        _calendar().share(event_id, permissions, actor)
        flash("Lesen- und Bearbeitungsrechte für den Termin gespeichert.")
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
        _calendar().save_booking_settings(request.form.get("enabled") == "1", int(request.form.get("duration_minutes", "60")), request.form.get("start_time", "09:00"), request.form.get("end_time", "17:00"), str(g.user["username"]), request.form.get("timezone", "Europe/Berlin"))
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
