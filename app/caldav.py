"""Authenticated CalDAV collections with conditional writes and incremental sync."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Response, current_app, request, url_for

from .calendar_collections import CalendarCollections, CalendarConflict
from .recurrence import RecurrenceError, event_overlaps, parse_ical_datetime, parse_ical_list, validate_recurrence
from .calendar_alarms import parse_valarm, serialize_alarm
from .calendar_metadata import metadata_lines, normalize_metadata
from .caldav_scheduling import SchedulingAccess, freebusy_ics, freebusy_periods, local_calendar_address, parse_freebusy_request
from .db import get_db
from .document_store import utc_now
from .revision_history import RevisionHistory


bp = Blueprint("caldav", __name__)
DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"
MAX_XML = 1024 * 1024
MAX_HREFS = 500


def _store() -> CalendarCollections:
    return CalendarCollections(current_app.config["DOCUMENT_ROOT"])


def _scheduling() -> SchedulingAccess:
    return SchedulingAccess(current_app.config["DOCUMENT_ROOT"])


def _itip():
    # Imported lazily because the iTIP serializer reuses this module's ICS
    # parser and exporter.
    from .itip import ItipStore
    return ItipStore(current_app.config["DOCUMENT_ROOT"])


def _calendar_users() -> dict[str, list[str]]:
    """Map local and verified mail addresses without exposing other profiles."""
    try:
        rows = get_db().execute("SELECT username, email FROM user ORDER BY username").fetchall()
    except sqlite3.Error:
        rows = []
    values: dict[str, list[str]] = {}
    verified_counts: dict[str, int] = {}
    for row in rows:
        email = str(row["email"] or "").strip().casefold()
        if email:
            verified_counts[email] = verified_counts.get(email, 0) + 1
    for row in rows:
        username = str(row["username"])
        addresses = [local_calendar_address(username)]
        email = str(row["email"] or "").strip().casefold()
        if email and verified_counts[email] == 1:
            addresses.append(email)
        values[username] = addresses
    return values


def _resolve_calendar_user(address: str) -> str | None:
    wanted = address.removeprefix("mailto:").strip().casefold()
    matches = [username for username, addresses in _calendar_users().items() if wanted in {value.casefold() for value in addresses}]
    return matches[0] if len(matches) == 1 else None


def _schedule_tag(event: dict) -> str:
    scheduling = {
        key: event.get(key)
        for key in ("source_uid", "sequence", "organizer", "participants", "start", "end", "status", "updated_at")
    }
    canonical = json.dumps(scheduling, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return '"' + hashlib.sha256(canonical.encode()).hexdigest() + '"'


def _scheduling_collection_properties(kind: str, actor: str) -> str:
    resource = "schedule-inbox" if kind == "inbox" else "schedule-outbox"
    privilege = "schedule-deliver" if kind == "inbox" else "schedule-send"
    return (
        f"<d:resourcetype><d:collection/><cal:{resource}/></d:resourcetype>"
        f"<d:displayname>Scheduling {kind.title()}</d:displayname>"
        "<cal:supported-calendar-data><cal:calendar-data content-type=\"text/calendar\" version=\"2.0\"/></cal:supported-calendar-data>"
        "<cal:supported-calendar-component-set><cal:comp name=\"VEVENT\"/><cal:comp name=\"VFREEBUSY\"/></cal:supported-calendar-component-set>"
        f"<d:current-user-privilege-set><d:privilege><cal:{privilege}/></d:privilege></d:current-user-privilege-set>"
    )


def _scheduling_error(name: str, status: int = 403) -> Response:
    return Response(
        f'<?xml version="1.0" encoding="utf-8"?><d:error xmlns:d="{DAV}" xmlns:cal="{CAL}"><cal:{name}/></d:error>',
        status,
        {"Content-Type": "application/xml; charset=utf-8"},
    )


def _auth() -> str | None:
    credentials = request.authorization
    if credentials and credentials.type.lower() == "basic" and _store().authenticate(credentials.username, credentials.password):
        return credentials.username
    return None


def _unauthorized() -> Response:
    return Response("CalDAV authentication required", 401, {"WWW-Authenticate": 'Basic realm="SimpleOffice4Me CalDAV"'})


def _multistatus(items: list[tuple[str, str, str]], sync_token: str = "") -> Response:
    responses = []
    for href, properties, status in items:
        if status.endswith("404 Not Found"):
            responses.append(f"<d:response><d:href>{escape(href)}</d:href><d:status>{status}</d:status></d:response>")
        else:
            responses.append(f"<d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>{properties}</d:prop><d:status>{status}</d:status></d:propstat></d:response>")
    token = f"<d:sync-token>{escape(sync_token)}</d:sync-token>" if sync_token else ""
    body = f'<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="{DAV}" xmlns:cal="{CAL}">{"".join(responses)}{token}</d:multistatus>'
    return Response(body, 207, {"Content-Type": "application/xml; charset=utf-8"})


def _privileges(write: bool) -> str:
    values = "<d:privilege><d:read/></d:privilege>"
    if write:
        values += "<d:privilege><d:write-content/></d:privilege><d:privilege><d:bind/></d:privilege><d:privilege><d:unbind/></d:privilege>"
    return f"<d:current-user-privilege-set>{values}</d:current-user-privilege-set>"


def _calendar_properties(calendar: dict, actor: str) -> str:
    token = f"urn:simpleoffice:caldav:{calendar['calendar_id']}:{int(calendar.get('sync_revision', 0))}"
    return f'<d:resourcetype><d:collection/><cal:calendar/></d:resourcetype><d:displayname>{escape(calendar["name"])}</d:displayname><cal:calendar-description>{escape(calendar.get("description", ""))}</cal:calendar-description><cal:calendar-timezone-id>{escape(calendar.get("timezone", "UTC"))}</cal:calendar-timezone-id><cal:supported-calendar-data><cal:calendar-data content-type="text/calendar" version="2.0"/></cal:supported-calendar-data><cal:supported-calendar-component-set><cal:comp name="VEVENT"/></cal:supported-calendar-component-set><cal:schedule-calendar-transp><cal:opaque/></cal:schedule-calendar-transp><d:sync-token>{escape(token)}</d:sync-token>{_privileges(CalendarCollections.can_write(calendar, actor))}'


def _event_ics(event: dict) -> str:
    if event.get("raw_ics"):
        normalized = event["raw_ics"].replace("\r\n", "\n").replace("\r", "\n")
        return normalized.replace("\n", "\r\n").rstrip("\r\n") + "\r\n"
    def stamp(value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None and event.get("timezone"):
            parsed = parsed.replace(tzinfo=ZoneInfo(event["timezone"]))
        if parsed.tzinfo:
            return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return parsed.strftime("%Y%m%dT%H%M%S")
    esc = lambda value: str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    uid = event.get("source_uid") or event["event_id"] + "@simpleoffice.local"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SimpleOffice4Me//CalDAV//EN", "CALSCALE:GREGORIAN", "BEGIN:VEVENT", f"UID:{esc(uid)}", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}", f"SEQUENCE:{int(event.get('sequence', 0))}", f"DTSTART:{stamp(event['start'])}"]
    if event.get("end"): lines.append(f"DTEND:{stamp(event['end'])}")
    lines.extend([f"SUMMARY:{esc(event['title'])}", f"DESCRIPTION:{esc(event.get('reason', ''))}"])
    tags = [tag.get("name", "") for tag in event.get("tags", []) if tag.get("name")]
    if tags: lines.append("CATEGORIES:" + ",".join(esc(tag) for tag in tags))
    metadata_event = {**event, "ical_status": "cancelled" if event.get("status") == "cancelled" else event.get("ical_status", "confirmed")}
    lines.extend(metadata_lines(metadata_event, esc))
    if event.get("organizer"):
        organizer = event["organizer"]
        prefix = f';CN="{esc(organizer.get("name", ""))}"' if organizer.get("name") else ""
        lines.append(f"ORGANIZER{prefix}:mailto:{organizer['email']}")
    for attendee in event.get("participants", []):
        parameters = [f'CN="{esc(attendee.get("name", ""))}"'] if attendee.get("name") else []
        role_names = {"required": "REQ-PARTICIPANT", "optional": "OPT-PARTICIPANT", "chair": "CHAIR", "non-participant": "NON-PARTICIPANT"}
        parameters.extend([f"ROLE={role_names.get(attendee.get('role'), 'REQ-PARTICIPANT')}", f"PARTSTAT={attendee.get('status', 'needs-action').upper()}"])
        if attendee.get("rsvp"): parameters.append("RSVP=TRUE")
        lines.append("ATTENDEE;" + ";".join(parameters) + ":mailto:" + attendee["email"])
    recurrence = event.get("recurrence", {})
    if recurrence.get("rrule"): lines.append("RRULE:" + recurrence["rrule"])
    if recurrence.get("rdates"): lines.append("RDATE:" + ",".join(stamp(value) for value in recurrence["rdates"]))
    if recurrence.get("exdates"): lines.append("EXDATE:" + ",".join(stamp(value) for value in recurrence["exdates"]))
    for alarm in event.get("alarms", []):
        lines.extend(serialize_alarm(alarm))
    lines.append("END:VEVENT")
    for override in event.get("recurrence_overrides", []):
        lines.extend(["BEGIN:VEVENT", f"UID:{esc(uid)}", f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}", f"SEQUENCE:{int(event.get('sequence', 0))}", f"RECURRENCE-ID:{stamp(override['recurrence_id'])}"])
        if override.get("status") == "cancelled":
            lines.append("STATUS:CANCELLED")
        else:
            override_start = override.get("start") or override["recurrence_id"]
            lines.extend([f"DTSTART:{stamp(override_start)}", f"DTEND:{stamp(override.get('end') or override_start)}", f"SUMMARY:{esc(override.get('title') or event['title'])}", f"DESCRIPTION:{esc(override.get('reason') or event.get('reason', ''))}"])
        lines.append("END:VEVENT")
    lines.extend(["END:VCALENDAR", ""])
    return "\r\n".join(lines)


def _actor_addresses(actor: str) -> set[str]:
    return {value.casefold() for value in _calendar_users().get(actor, [local_calendar_address(actor)])}


def _validate_scheduling_write(actor: str, previous: dict | None, values: dict) -> None:
    """Enforce RFC 6638 organizer/attendee roles only for opted-in users."""
    if not _scheduling().get(actor)["enabled"] or not values.get("organizer", {}).get("email"):
        return
    addresses = _actor_addresses(actor)
    organizer = values["organizer"]["email"].casefold()
    participants = {row.get("email", "").casefold(): row for row in values.get("participants", [])}
    if organizer in addresses:
        return
    own_addresses = addresses.intersection(participants)
    if not own_addresses:
        raise PermissionError("authenticated user is neither organizer nor attendee")
    if previous is None:
        return
    if previous.get("organizer", {}).get("email", "").casefold() != organizer:
        raise PermissionError("attendee cannot replace organizer")
    protected = ("source_uid", "title", "reason", "start", "end", "status", "sequence", "tags", "ical_status", "transparency", "classification", "priority", "location", "event_url", "resources", "conferences")
    normalized = {**values, "source_uid": values.get("uid"), "reason": values.get("description", "")}
    if any(previous.get(key) != normalized.get(key) for key in protected):
        raise PermissionError("attendee changed organizer-controlled event data")
    previous_participants = {row.get("email", "").casefold(): row for row in previous.get("participants", [])}
    if set(previous_participants) != set(participants):
        raise PermissionError("attendee cannot add or remove participants")
    for address, participant in participants.items():
        old = previous_participants[address]
        if address not in own_addresses and old != participant:
            raise PermissionError("attendee cannot change another participant")
        if address in own_addresses:
            old_without_status = {key: value for key, value in old.items() if key != "status"}
            new_without_status = {key: value for key, value in participant.items() if key != "status"}
            if old_without_status != new_without_status:
                raise PermissionError("attendee may only change their own PARTSTAT")


def _deliver_scheduling(actor: str, previous: dict | None, event: dict, forced_method: str = "") -> list[dict]:
    """Deliver local iTIP copies only where the recipient explicitly opted in."""
    if not _scheduling().get(actor)["enabled"] or request.headers.get("Schedule-Reply", "T").upper() == "F":
        return []
    addresses = _actor_addresses(actor)
    organizer_address = event.get("organizer", {}).get("email", "").casefold()
    method = forced_method
    attendee_address = ""
    recipients: list[tuple[str, str]] = []
    if organizer_address in addresses and event.get("owner") == actor:
        method = method or ("CANCEL" if event.get("status") == "cancelled" else "REQUEST")
        for participant in event.get("participants", []):
            target = _resolve_calendar_user(participant.get("email", ""))
            if target and target != actor:
                recipients.append((target, participant.get("email", "")))
    elif previous is not None:
        for participant in event.get("participants", []):
            email = participant.get("email", "").casefold()
            if email not in addresses:
                continue
            old = next((row for row in previous.get("participants", []) if row.get("email", "").casefold() == email), None)
            if old and old.get("status") != participant.get("status"):
                attendee_address = email
                target = _resolve_calendar_user(organizer_address)
                if target and target != actor:
                    recipients.append((target, organizer_address))
                    method = "REPLY"
            break
    if not method or not recipients:
        return []
    try:
        payload = _itip().export(
            event["event_id"],
            actor,
            method,
            attendee_address,
            next((row.get("status", "") for row in event.get("participants", []) if row.get("email", "").casefold() == attendee_address), ""),
            attendee_address,
        )
    except ValueError:
        return []
    delivered = []
    for recipient, address in recipients:
        if not _scheduling().can_deliver(actor, recipient):
            delivered.append({"recipient": address, "status": "3.8", "delivered": False})
            continue
        message = _itip().receive(payload, recipient, f"caldav:{actor}")
        delivered.append({"recipient": address, "status": "2.0", "delivered": True, "message_id": message["message_id"]})
    RevisionHistory(Path(current_app.config["DOCUMENT_ROOT"])).record(
        "caldav_scheduling_delivery_completed",
        actor,
        "calendar-scheduling-delivery",
        event["event_id"],
        {
            "event_id": event["event_id"],
            "uid": event.get("source_uid", ""),
            "method": method,
            "results": delivered,
            "at": utc_now(),
        },
    )
    return delivered


def _freebusy_response(actor: str, content: str) -> Response:
    try:
        values = parse_freebusy_request(content)
    except ValueError:
        return _scheduling_error("valid-scheduling-message", 400)
    if values["organizer"] not in _actor_addresses(actor):
        return _scheduling_error("valid-organizer")
    responses = []
    for address in values["attendees"]:
        recipient = _resolve_calendar_user(address)
        if recipient is None:
            responses.append((address, "3.7;Invalid Calendar User", ""))
            continue
        if not _scheduling().can_query_freebusy(actor, recipient):
            responses.append((address, "3.8;No authority", ""))
            continue
        periods = freebusy_periods(current_app.config["DOCUMENT_ROOT"], recipient, values["start"], values["end"])
        responses.append((address, "2.0;Success", freebusy_ics(values, address, periods)))
    parts = []
    for address, status, calendar_data in responses:
        data = f"<cal:calendar-data>{escape(calendar_data)}</cal:calendar-data>" if calendar_data else ""
        parts.append(f"<cal:response><cal:recipient><d:href>mailto:{escape(address)}</d:href></cal:recipient><cal:request-status>{escape(status)}</cal:request-status>{data}</cal:response>")
    body = f'<?xml version="1.0" encoding="utf-8"?><cal:schedule-response xmlns:d="{DAV}" xmlns:cal="{CAL}">{"".join(parts)}</cal:schedule-response>'
    RevisionHistory(Path(current_app.config["DOCUMENT_ROOT"])).record(
        "caldav_freebusy_queried",
        actor,
        "calendar-freebusy",
        values["uid"],
        {
            "uid": values["uid"],
            "start": values["start"].isoformat(),
            "end": values["end"].isoformat(),
            "recipients": [
                {"address": address, "request_status": status.split(";", 1)[0]}
                for address, status, _ in responses
            ],
            "at": utc_now(),
        },
    )
    return Response(body, 200, {"Content-Type": "application/xml; charset=utf-8", "Cache-Control": "no-store"})


def _parse_ics(content: str) -> dict:
    if len(content.encode("utf-8")) > 1024 * 1024:
        raise ValueError("calendar resource exceeds 1 MiB")
    unfolded: list[str] = []
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and unfolded: unfolded[-1] += line[1:]
        else: unfolded.append(line)
    begin_count = sum(line.upper() == "BEGIN:VEVENT" for line in unfolded)
    if begin_count < 1 or begin_count != sum(line.upper() == "END:VEVENT" for line in unfolded) or begin_count > 501:
        raise ValueError("calendar resource requires one master and at most 500 exception VEVENTs")
    component_lines: list[dict[str, list]] = []; current: list[str] | None = None; alarms: list[list[str]] = []; current_alarm: list[str] | None = None
    for line in unfolded:
        upper_line = line.upper()
        if upper_line == "BEGIN:VEVENT":
            if current is not None: raise ValueError("nested VEVENT is invalid")
            current = []; alarms = []; continue
        if upper_line == "BEGIN:VALARM" and current is not None:
            if current_alarm is not None: raise ValueError("nested VALARM is invalid")
            current_alarm = []; continue
        if upper_line == "END:VALARM" and current is not None:
            if current_alarm is None: raise ValueError("unmatched VALARM end")
            alarms.append(current_alarm); current_alarm = None; continue
        if upper_line == "END:VEVENT" and current is not None:
            if current_alarm is not None: raise ValueError("unterminated VALARM")
            component_lines.append({"lines": current, "alarms": alarms}); current = None; continue
        if current_alarm is not None: current_alarm.append(line)
        elif current is not None: current.append(line)
    unescape = lambda value: re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1).lower() == "n" else m.group(1), value)
    def person(entry: tuple[str, str], attendee: bool = False) -> dict:
        left, value = entry; params = {}
        for parameter in left.split(";")[1:]:
            key, separator, raw = parameter.partition("=")
            if separator: params[key.upper()] = raw.strip('"')
        address = value.strip()
        if address.lower().startswith("mailto:"): address = address[7:]
        if "@" not in address: raise ValueError("organizer and attendees require mailto email addresses")
        result = {"email": address.lower(), "name": unescape(params.get("CN", ""))[:120]}
        if attendee:
            result.update({"status": params.get("PARTSTAT", "NEEDS-ACTION").lower(), "role": params.get("ROLE", "REQ-PARTICIPANT").lower().replace("req-participant", "required").replace("opt-participant", "optional"), "rsvp": params.get("RSVP", "FALSE").upper() == "TRUE"})
        return result
    components: list[dict] = []
    for component in component_lines:
        lines = component["lines"]
        fields: dict[str, tuple[str, str]] = {}; repeated: dict[str, list[tuple[str, str]]] = {"ATTENDEE": [], "RDATE": [], "EXDATE": [], "CONFERENCE": []}
        for line in lines:
            if ":" not in line: continue
            left, value = line.split(":", 1); key = left.split(";", 1)[0].upper()
            if key in repeated: repeated[key].append((left, value))
            elif key in {"UID", "SUMMARY", "DESCRIPTION", "DTSTART", "DTEND", "CATEGORIES", "STATUS", "SEQUENCE", "ORGANIZER", "RRULE", "RECURRENCE-ID", "TRANSP", "CLASS", "PRIORITY", "LOCATION", "URL", "RESOURCES"}:
                if key in fields and key in {"UID", "DTSTART", "DTEND", "RRULE", "RECURRENCE-ID"}:
                    raise ValueError(f"{key} must not occur more than once in a VEVENT")
                fields[key] = (left, value)
        if not fields.get("UID", ("", ""))[1].strip(): raise ValueError("every VEVENT requires UID")
        components.append({"fields": fields, "repeated": repeated, "alarm_lines": component["alarms"]})
    masters = [item for item in components if "RECURRENCE-ID" not in item["fields"]]
    if len(masters) != 1 or "DTSTART" not in masters[0]["fields"]:
        raise ValueError("calendar resource requires exactly one master VEVENT with DTSTART")
    master = masters[0]; fields = master["fields"]; repeated = master["repeated"]
    uid = unescape(fields["UID"][1]).strip()
    if any(unescape(item["fields"]["UID"][1]).strip() != uid for item in components):
        raise ValueError("all recurrence components in one resource must share UID")
    start, tzid, _ = parse_ical_datetime(*fields["DTSTART"])
    end = parse_ical_datetime(*fields["DTEND"], tzid)[0] if "DTEND" in fields else ""
    if any(item["alarm_lines"] for item in components if item is not master):
        raise ValueError("alarms on recurrence exception VEVENTs are not supported")
    alarms = [parse_valarm(lines, end) for lines in master["alarm_lines"]]
    if len(alarms) > 8:
        raise ValueError("at most 8 VALARM components are allowed per event")
    rdates: list[str] = []; exdates: list[str] = []
    for entry in repeated["RDATE"]:
        values, _ = parse_ical_list(*entry, tzid); rdates.extend(values)
    for entry in repeated["EXDATE"]:
        values, _ = parse_ical_list(*entry, tzid); exdates.extend(values)
    recurrence = validate_recurrence({"rrule": fields.get("RRULE", ("", ""))[1], "rdates": rdates, "exdates": exdates, "timezone": tzid}, start)
    overrides: list[dict] = []
    for item in components:
        exception_fields = item["fields"]
        if "RECURRENCE-ID" not in exception_fields: continue
        if ";RANGE=" in exception_fields["RECURRENCE-ID"][0].upper():
            raise ValueError("RECURRENCE-ID RANGE=THISANDFUTURE is not supported")
        recurrence_id = parse_ical_datetime(*exception_fields["RECURRENCE-ID"], tzid)[0]
        status = exception_fields.get("STATUS", ("", ""))[1].upper()
        if status != "CANCELLED" and "DTSTART" not in exception_fields:
            raise ValueError("active recurrence exception requires DTSTART")
        override_start = parse_ical_datetime(*exception_fields["DTSTART"], tzid)[0] if "DTSTART" in exception_fields else ""
        override_end = parse_ical_datetime(*exception_fields["DTEND"], tzid)[0] if "DTEND" in exception_fields else ""
        overrides.append({"recurrence_id": recurrence_id, "status": "cancelled" if status == "CANCELLED" else "active", "start": override_start, "end": override_end, "title": unescape(exception_fields.get("SUMMARY", ("", ""))[1]), "reason": unescape(exception_fields.get("DESCRIPTION", ("", ""))[1])})
    participants = [person(value, True) for value in repeated["ATTENDEE"]]
    if len(participants) > 200 or len({row["email"] for row in participants}) != len(participants): raise ValueError("VEVENT participant list is invalid")
    status = fields.get("STATUS", ("", ""))[1].upper()
    try: sequence = int(fields.get("SEQUENCE", ("", "0"))[1] or 0)
    except ValueError as exc: raise ValueError("SEQUENCE must be an integer") from exc
    conferences = []
    for left, value in repeated["CONFERENCE"]:
        params = {}
        for parameter in left.split(";")[1:]:
            key, separator, raw = parameter.partition("=")
            if separator: params[key.upper()] = raw.strip('"')
        conferences.append({"uri": value.strip(), "label": unescape(params.get("LABEL", "")), "features": [item.strip().lower() for item in params.get("FEATURE", "").split(",") if item.strip()]})
    metadata = normalize_metadata({
        "ical_status": status.lower() if status else "confirmed",
        "transparency": fields.get("TRANSP", ("", "OPAQUE"))[1].lower(),
        "classification": fields.get("CLASS", ("", "PRIVATE"))[1].lower(),
        "priority": fields.get("PRIORITY", ("", "0"))[1],
        "location": unescape(fields.get("LOCATION", ("", ""))[1]),
        "event_url": fields.get("URL", ("", ""))[1].strip(),
        "resources": [unescape(item).strip() for item in fields.get("RESOURCES", ("", ""))[1].split(",") if item.strip()],
        "conferences": conferences,
    })
    return {"uid": uid, "title": unescape(fields.get("SUMMARY", ("", "Ohne Titel"))[1]).strip() or "Ohne Titel", "description": unescape(fields.get("DESCRIPTION", ("", ""))[1]), "start": start, "end": end, "timezone": tzid, "recurrence": recurrence, "recurrence_overrides": overrides, "alarms": alarms, "status": "cancelled" if status == "CANCELLED" else "active", "sequence": sequence, "tags": [{"name": unescape(tag).strip(), "visibility": "private"} for tag in fields.get("CATEGORIES", ("", ""))[1].split(",") if tag.strip()], "organizer": person(fields["ORGANIZER"]) if "ORGANIZER" in fields else {}, "participants": participants, "raw_ics": content, **metadata}


def _xml_root() -> ElementTree.Element:
    body = request.get_data(cache=True)
    if len(body) > MAX_XML: raise ValueError("DAV XML request exceeds 1 MiB")
    try: return ElementTree.fromstring(body or b"<empty/>")
    except ElementTree.ParseError as exc: raise ValueError("invalid DAV XML") from exc


@bp.route("/.well-known/caldav", methods=["OPTIONS", "PROPFIND", "GET"])
def well_known():
    return Response("", 307, {"Location": url_for("caldav.endpoint", path="", _external=True), "Cache-Control": "public, max-age=3600"})


@bp.route("/caldav/", defaults={"path": ""}, methods=["OPTIONS", "PROPFIND", "REPORT", "MKCALENDAR", "GET", "PUT", "DELETE", "POST"])
@bp.route("/caldav/<path:path>", methods=["OPTIONS", "PROPFIND", "REPORT", "MKCALENDAR", "GET", "PUT", "DELETE", "POST"])
def endpoint(path: str):
    actor = _auth()
    if actor is None: return _unauthorized()
    normalized = path.strip("/"); store = _store()
    scheduling_enabled = _scheduling().get(actor)["enabled"]
    dav_features = "1, 3, calendar-access, sync-collection" + (", calendar-auto-schedule" if scheduling_enabled else "")
    if request.method == "OPTIONS": return Response("", 204, {"DAV": dav_features, "Allow": "OPTIONS, PROPFIND, REPORT, MKCALENDAR, GET, PUT, DELETE, POST"})
    if normalized.startswith("principals/") and normalized != f"principals/{actor}": return Response("not found", 404)
    if normalized.startswith("calendars/") and normalized != f"calendars/{actor}" and not normalized.startswith(f"calendars/{actor}/"): return Response("not found", 404)
    if normalized.startswith("scheduling/") and normalized != f"scheduling/{actor}" and not normalized.startswith(f"scheduling/{actor}/"): return Response("not found", 404)
    home = f"/caldav/calendars/{actor}/"; principal = f"/caldav/principals/{actor}/"
    inbox = f"/caldav/scheduling/{actor}/inbox/"; outbox = f"/caldav/scheduling/{actor}/outbox/"
    parts = normalized.split("/") if normalized else []
    if len(parts) >= 3 and parts[:2] == ["scheduling", actor]:
        if not scheduling_enabled:
            return Response("scheduling is disabled", 404)
        kind = parts[2]
        if kind not in {"inbox", "outbox"}:
            return Response("not found", 404)
        collection_href = inbox if kind == "inbox" else outbox
        if request.method == "PROPFIND":
            if len(parts) == 3:
                items = [(collection_href, _scheduling_collection_properties(kind, actor), "HTTP/1.1 200 OK")]
                if kind == "inbox" and request.headers.get("Depth", "0") != "0":
                    items += [
                        (
                            inbox + message["message_id"] + ".ics",
                            f'<d:getetag>"{message.get("sha256", "")}"</d:getetag><d:getcontenttype>text/calendar; charset=utf-8</d:getcontenttype>',
                            "HTTP/1.1 200 OK",
                        )
                        for message in _itip().inbox_messages(actor)
                    ]
                return _multistatus(items)
            if kind != "inbox" or len(parts) != 4 or not parts[3].endswith(".ics"):
                return Response("not found", 404)
            message_id = parts[3][:-4]
            try:
                message, _ = _itip().inbox_content(message_id, actor)
            except ValueError:
                return Response("not found", 404)
            return _multistatus([(request.path, f'<d:getetag>"{message.get("sha256", "")}"</d:getetag><d:getcontenttype>text/calendar; charset=utf-8</d:getcontenttype>', "HTTP/1.1 200 OK")])
        if request.method == "GET" and kind == "inbox" and len(parts) == 4 and parts[3].endswith(".ics"):
            try:
                message, content = _itip().inbox_content(parts[3][:-4], actor)
            except ValueError:
                return Response("not found", 404)
            return Response(content, 200, {"Content-Type": "text/calendar; charset=utf-8", "ETag": f'"{message.get("sha256", "")}"', "Cache-Control": "no-store"})
        if request.method == "DELETE" and kind == "inbox" and len(parts) == 4 and parts[3].endswith(".ics"):
            try:
                message, _ = _itip().inbox_content(parts[3][:-4], actor)
                current = f'"{message.get("sha256", "")}"'
                if request.headers.get("If-Match") and request.headers["If-Match"] != current:
                    return Response("CalDAV scheduling inbox precondition failed", 412, {"ETag": current})
                _itip().archive_inbox(parts[3][:-4], actor)
            except ValueError:
                return Response("not found", 404)
            return Response("", 204)
        if request.method == "REPORT" and kind == "inbox" and len(parts) == 3:
            try:
                root = _xml_root()
            except ValueError as exc:
                return Response(str(exc), 400)
            if root.tag not in {f"{{{CAL}}}calendar-query", f"{{{CAL}}}calendar-multiget"}:
                return Response("unsupported scheduling inbox report", 403)
            messages = _itip().inbox_messages(actor)
            hrefs = [node.text or "" for node in root.findall(f".//{{{DAV}}}href")]
            if len(hrefs) > MAX_HREFS:
                return Response("too many DAV hrefs", 413)
            if root.tag == f"{{{CAL}}}calendar-multiget":
                wanted = {href.rstrip("/").rsplit("/", 1)[-1].removesuffix(".ics") for href in hrefs}
                messages = [message for message in messages if message["message_id"] in wanted]
            else:
                time_range = root.find(f".//{{{CAL}}}time-range")
                if time_range is not None:
                    try:
                        lower = datetime.strptime(time_range.attrib.get("start", "19700101T000000Z"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                        upper = datetime.strptime(time_range.attrib.get("end", "99991231T235959Z"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                        if lower >= upper:
                            raise ValueError
                    except ValueError:
                        return Response("invalid CalDAV scheduling time-range", 400)

                    def inbox_overlaps(message: dict) -> bool:
                        try:
                            begins = datetime.fromisoformat(str(message.get("start", "")).replace("Z", "+00:00"))
                            finishes = datetime.fromisoformat(str(message.get("end") or message.get("start", "")).replace("Z", "+00:00"))
                        except ValueError:
                            return False
                        if begins.tzinfo is None:
                            begins = begins.replace(tzinfo=timezone.utc)
                        if finishes.tzinfo is None:
                            finishes = finishes.replace(tzinfo=timezone.utc)
                        return begins.astimezone(timezone.utc) < upper and finishes.astimezone(timezone.utc) >= lower

                    messages = [message for message in messages if inbox_overlaps(message)]
            items = []
            for message in messages:
                _, content = _itip().inbox_content(message["message_id"], actor)
                items.append((inbox + message["message_id"] + ".ics", f'<d:getetag>"{message.get("sha256", "")}"</d:getetag><cal:calendar-data>{escape(content)}</cal:calendar-data>', "HTTP/1.1 200 OK"))
            return _multistatus(items)
        if request.method == "POST" and kind == "outbox" and len(parts) == 3:
            if request.mimetype != "text/calendar":
                return Response("CalDAV scheduling requires text/calendar", 415)
            return _freebusy_response(actor, request.get_data(as_text=True))
        return Response("method not allowed on scheduling collection", 405)
    if request.method == "PROPFIND":
        if not normalized: return _multistatus([(request.path, f"<d:resourcetype><d:collection/></d:resourcetype><d:current-user-principal><d:href>{principal}</d:href></d:current-user-principal>", "HTTP/1.1 200 OK")])
        if normalized == f"principals/{actor}":
            address_properties = "".join(f"<d:href>mailto:{escape(value)}</d:href>" for value in _calendar_users().get(actor, [local_calendar_address(actor)]))
            scheduling_properties = f"<cal:schedule-inbox-URL><d:href>{inbox}</d:href></cal:schedule-inbox-URL><cal:schedule-outbox-URL><d:href>{outbox}</d:href></cal:schedule-outbox-URL>" if scheduling_enabled else ""
            return _multistatus([(principal, f"<d:resourcetype><d:principal/></d:resourcetype><d:displayname>{escape(actor)}</d:displayname><cal:calendar-home-set><d:href>{home}</d:href></cal:calendar-home-set><cal:calendar-user-address-set>{address_properties}</cal:calendar-user-address-set>{scheduling_properties}", "HTTP/1.1 200 OK")])
        if normalized == f"calendars/{actor}":
            items = [(home, "<d:resourcetype><d:collection/></d:resourcetype><d:displayname>SimpleOffice Kalender</d:displayname>", "HTTP/1.1 200 OK")]
            if request.headers.get("Depth", "0") != "0": items += [(home + c["calendar_id"] + "/", _calendar_properties(c, actor), "HTTP/1.1 200 OK") for c in store.calendars(actor)]
            return _multistatus(items)
        if len(parts) >= 3 and parts[:2] == ["calendars", actor]:
            try: calendar = store.get(parts[2], actor)
            except ValueError: return Response("not found", 404)
            if len(parts) == 3:
                items = [(request.path.rstrip("/") + "/", _calendar_properties(calendar, actor), "HTTP/1.1 200 OK")]
                if request.headers.get("Depth", "0") != "0": items += [(home + parts[2] + "/" + (e.get("caldav_resource") or e["event_id"] + ".ics"), f"<d:getetag>{store.etag(e)}</d:getetag><cal:schedule-tag>{_schedule_tag(e)}</cal:schedule-tag><d:getcontenttype>text/calendar; charset=utf-8</d:getcontenttype>", "HTTP/1.1 200 OK") for e in store.resource_events(parts[2], actor)]
                return _multistatus(items)
            resource = parts[3]
            event = next((e for e in store.resource_events(parts[2], actor) if (e.get("caldav_resource") or e["event_id"] + ".ics") == resource), None)
            return _multistatus([(request.path, f"<d:getetag>{store.etag(event)}</d:getetag><cal:schedule-tag>{_schedule_tag(event)}</cal:schedule-tag><d:getcontenttype>text/calendar; charset=utf-8</d:getcontenttype>", "HTTP/1.1 200 OK")]) if event else Response("not found", 404)
        return Response("not found", 404)
    if len(parts) < 3 or parts[:2] != ["calendars", actor]: return Response("not found", 404)
    calendar_id = parts[2]
    if request.method == "MKCALENDAR":
        if len(parts) != 3: return Response("invalid calendar collection path", 409)
        try:
            root = _xml_root(); name = root.findtext(f".//{{{DAV}}}displayname") or calendar_id
            description = root.findtext(f".//{{{CAL}}}calendar-description") or ""
            timezone_id = root.findtext(f".//{{{CAL}}}calendar-timezone-id") or "UTC"
            store.create(name, actor, "#2563eb", timezone_id, description, calendar_id)
        except ValueError as exc: return Response(str(exc), 405 if "already exists" in str(exc) else 400)
        return Response("", 201, {"Location": request.path.rstrip("/") + "/"})
    if request.method == "DELETE" and len(parts) == 3:
        try: store.delete(calendar_id, actor)
        except ValueError as exc: return Response(str(exc), 409 if "empty" in str(exc) or "default" in str(exc) else 403)
        return Response("", 204)
    if request.method == "REPORT":
        try: root = _xml_root(); store.get(calendar_id, actor)
        except ValueError as exc: return Response(str(exc), 400)
        if root.tag == f"{{{DAV}}}sync-collection":
            token = (root.findtext(f"{{{DAV}}}sync-token") or "").strip()
            try: changes, new_token = store.sync_changes(calendar_id, actor, token)
            except ValueError: return Response(f'<d:error xmlns:d="{DAV}"><d:valid-sync-token/></d:error>', 403, {"Content-Type": "application/xml"})
            items = []
            current = {e.get("caldav_resource") or e["event_id"] + ".ics": e for e in store.resource_events(calendar_id, actor)}
            for change in changes:
                href = home + calendar_id + "/" + change["resource"]; event = current.get(change["resource"])
                if change.get("deleted") or event is None: items.append((href, "", "HTTP/1.1 404 Not Found"))
                else: items.append((href, f"<d:getetag>{store.etag(event)}</d:getetag>", "HTTP/1.1 200 OK"))
            return _multistatus(items, new_token)
        events = store.resource_events(calendar_id, actor); hrefs = [node.text or "" for node in root.findall(f".//{{{DAV}}}href")]
        if len(hrefs) > MAX_HREFS: return Response("too many DAV hrefs", 413)
        if root.tag == f"{{{CAL}}}calendar-multiget":
            wanted = {href.rstrip("/").rsplit("/", 1)[-1] for href in hrefs}; events = [e for e in events if (e.get("caldav_resource") or e["event_id"] + ".ics") in wanted]
        elif root.tag == f"{{{CAL}}}calendar-query":
            time_range = root.find(f".//{{{CAL}}}time-range")
            if time_range is not None:
                try:
                    lower = datetime.strptime(time_range.attrib.get("start", "19700101T000000Z"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                    upper = datetime.strptime(time_range.attrib.get("end", "99991231T235959Z"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                    if lower >= upper: raise ValueError
                except ValueError: return Response("invalid CalDAV time-range", 400)
                try: events = [event for event in events if event_overlaps(event, lower, upper)]
                except RecurrenceError as exc: return Response(str(exc), 400)
        items = []
        for event in events:
            resource = event.get("caldav_resource") or event["event_id"] + ".ics"
            items.append((home + calendar_id + "/" + resource, f"<d:getetag>{store.etag(event)}</d:getetag><cal:schedule-tag>{_schedule_tag(event)}</cal:schedule-tag><cal:calendar-data>{escape(_event_ics(event))}</cal:calendar-data>", "HTTP/1.1 200 OK"))
        return _multistatus(items)
    if len(parts) != 4: return Response("method requires an event resource", 405)
    resource = parts[3]; events = store.resource_events(calendar_id, actor); event = next((e for e in events if (e.get("caldav_resource") or e["event_id"] + ".ics") == resource), None)
    if request.method == "GET":
        return Response(_event_ics(event), 200, {"Content-Type": "text/calendar; charset=utf-8", "ETag": store.etag(event), "Schedule-Tag": _schedule_tag(event)}) if event else Response("not found", 404)
    if request.method == "PUT":
        current = store.etag(event) if event else ""; if_match = request.headers.get("If-Match")
        if request.headers.get("If-None-Match") == "*" and event: return Response("CalDAV precondition failed", 412, {"ETag": current})
        if if_match and (not event or if_match != current): return Response("CalDAV precondition failed", 412, {"ETag": current} if current else {})
        if_schedule_match = request.headers.get("If-Schedule-Tag-Match")
        if if_schedule_match and (not event or if_schedule_match != _schedule_tag(event)):
            return Response("CalDAV scheduling precondition failed", 412, {"Schedule-Tag": _schedule_tag(event)} if event else {})
        try:
            values = _parse_ics(request.get_data(as_text=True))
            _validate_scheduling_write(actor, event, values)
            saved, created = store.put_event(calendar_id, resource, values, actor, current if if_match else None, request.headers.get("If-None-Match") == "*")
        except CalendarConflict as exc: return Response("CalDAV precondition failed", 412, {"ETag": store.etag(exc.event)} if exc.event else {})
        except PermissionError: return _scheduling_error("allowed-attendee-scheduling-object-change")
        except ValueError as exc: return Response(str(exc), 409 if "UID already" in str(exc) else 400)
        _deliver_scheduling(actor, event, saved)
        return Response("", 201 if created else 204, {"ETag": store.etag(saved), "Schedule-Tag": _schedule_tag(saved), "Location": request.path})
    if request.method == "DELETE":
        if not event: return Response("not found", 404)
        current = store.etag(event); if_match = request.headers.get("If-Match")
        if if_match and if_match != current: return Response("CalDAV precondition failed", 412, {"ETag": current})
        if_schedule_match = request.headers.get("If-Schedule-Tag-Match")
        if if_schedule_match and if_schedule_match != _schedule_tag(event): return Response("CalDAV scheduling precondition failed", 412, {"Schedule-Tag": _schedule_tag(event)})
        try: deleted = store.delete_event(calendar_id, resource, actor, current if if_match else None)
        except CalendarConflict as exc: return Response("CalDAV precondition failed", 412, {"ETag": store.etag(exc.event)} if exc.event else {})
        except ValueError as exc: return Response(str(exc), 403)
        _deliver_scheduling(actor, event, deleted, "CANCEL")
        return Response("", 204)
    return Response("not found", 404)
