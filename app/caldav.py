"""Authenticated CalDAV collections with conditional writes and incremental sync."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from xml.etree import ElementTree
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Response, current_app, request, url_for

from .calendar_collections import CalendarCollections, CalendarConflict


bp = Blueprint("caldav", __name__)
DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"
MAX_XML = 1024 * 1024
MAX_HREFS = 500


def _store() -> CalendarCollections:
    return CalendarCollections(current_app.config["DOCUMENT_ROOT"])


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
    return f'<d:resourcetype><d:collection/><cal:calendar/></d:resourcetype><d:displayname>{escape(calendar["name"])}</d:displayname><cal:calendar-description>{escape(calendar.get("description", ""))}</cal:calendar-description><cal:calendar-timezone-id>{escape(calendar.get("timezone", "UTC"))}</cal:calendar-timezone-id><cal:supported-calendar-data><cal:calendar-data content-type="text/calendar" version="2.0"/></cal:supported-calendar-data><cal:supported-calendar-component-set><cal:comp name="VEVENT"/></cal:supported-calendar-component-set><d:sync-token>{escape(token)}</d:sync-token>{_privileges(CalendarCollections.can_write(calendar, actor))}'


def _event_ics(event: dict) -> str:
    if event.get("raw_ics"):
        return event["raw_ics"].replace("\n", "\r\n").rstrip("\r\n") + "\r\n"
    def stamp(value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
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
    if event.get("status") == "cancelled": lines.append("STATUS:CANCELLED")
    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    return "\r\n".join(lines)


def _parse_ics(content: str) -> dict:
    if len(content.encode("utf-8")) > 1024 * 1024:
        raise ValueError("calendar resource exceeds 1 MiB")
    unfolded: list[str] = []
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and unfolded: unfolded[-1] += line[1:]
        else: unfolded.append(line)
    if sum(line.upper() == "BEGIN:VEVENT" for line in unfolded) != 1 or sum(line.upper() == "END:VEVENT" for line in unfolded) != 1:
        raise ValueError("one calendar resource must contain exactly one VEVENT")
    fields: dict[str, tuple[str, str]] = {}; attendee_fields: list[tuple[str, str]] = []; inside = False
    for line in unfolded:
        if line.upper() == "BEGIN:VEVENT": inside = True; continue
        if line.upper() == "END:VEVENT": inside = False; continue
        if not inside or ":" not in line: continue
        left, value = line.split(":", 1); key = left.split(";", 1)[0].upper()
        if key == "ATTENDEE": attendee_fields.append((left, value))
        elif key in {"UID", "SUMMARY", "DESCRIPTION", "DTSTART", "DTEND", "CATEGORIES", "STATUS", "SEQUENCE", "ORGANIZER"}: fields[key] = (left, value)
    if not fields.get("UID", ("", ""))[1].strip() or not fields.get("DTSTART", ("", ""))[1].strip():
        raise ValueError("VEVENT requires UID and DTSTART")
    unescape = lambda value: re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1).lower() == "n" else m.group(1), value)
    def parse_time(entry: tuple[str, str]) -> str:
        left, value = entry; value = value.strip(); tzid = ""
        for parameter in left.split(";")[1:]:
            if parameter.upper().startswith("TZID="): tzid = parameter.split("=", 1)[1].strip('"')
        if len(value) == 8: return datetime.strptime(value, "%Y%m%d").isoformat(timespec="minutes")
        zulu = value.endswith("Z"); raw = value[:-1] if zulu else value
        parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S" if len(raw) == 15 else "%Y%m%dT%H%M")
        if zulu: parsed = parsed.replace(tzinfo=timezone.utc)
        elif tzid:
            try: parsed = parsed.replace(tzinfo=ZoneInfo(tzid))
            except ZoneInfoNotFoundError as exc: raise ValueError(f"unknown TZID: {tzid}") from exc
        return parsed.isoformat(timespec="minutes")
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
    status = fields.get("STATUS", ("", ""))[1].upper()
    participants = [person(value, True) for value in attendee_fields]
    if len(participants) > 200 or len({row["email"] for row in participants}) != len(participants): raise ValueError("VEVENT participant list is invalid")
    return {"uid": unescape(fields["UID"][1]).strip(), "title": unescape(fields.get("SUMMARY", ("", "Ohne Titel"))[1]).strip() or "Ohne Titel", "description": unescape(fields.get("DESCRIPTION", ("", ""))[1]), "start": parse_time(fields["DTSTART"]), "end": parse_time(fields["DTEND"]) if "DTEND" in fields else "", "status": "cancelled" if status == "CANCELLED" else "active", "sequence": int(fields.get("SEQUENCE", ("", "0"))[1] or 0), "tags": [{"name": unescape(tag).strip(), "visibility": "private"} for tag in fields.get("CATEGORIES", ("", ""))[1].split(",") if tag.strip()], "organizer": person(fields["ORGANIZER"]) if "ORGANIZER" in fields else {}, "participants": participants, "raw_ics": content}


def _xml_root() -> ElementTree.Element:
    body = request.get_data(cache=True)
    if len(body) > MAX_XML: raise ValueError("DAV XML request exceeds 1 MiB")
    try: return ElementTree.fromstring(body or b"<empty/>")
    except ElementTree.ParseError as exc: raise ValueError("invalid DAV XML") from exc


@bp.route("/.well-known/caldav", methods=["OPTIONS", "PROPFIND", "GET"])
def well_known():
    return Response("", 307, {"Location": url_for("caldav.endpoint", path="", _external=True), "Cache-Control": "public, max-age=3600"})


@bp.route("/caldav/", defaults={"path": ""}, methods=["OPTIONS", "PROPFIND", "REPORT", "MKCALENDAR", "GET", "PUT", "DELETE"])
@bp.route("/caldav/<path:path>", methods=["OPTIONS", "PROPFIND", "REPORT", "MKCALENDAR", "GET", "PUT", "DELETE"])
def endpoint(path: str):
    actor = _auth()
    if actor is None: return _unauthorized()
    normalized = path.strip("/"); store = _store()
    if request.method == "OPTIONS": return Response("", 204, {"DAV": "1, 3, calendar-access, sync-collection", "Allow": "OPTIONS, PROPFIND, REPORT, MKCALENDAR, GET, PUT, DELETE"})
    if normalized.startswith("principals/") and normalized != f"principals/{actor}": return Response("not found", 404)
    if normalized.startswith("calendars/") and normalized != f"calendars/{actor}" and not normalized.startswith(f"calendars/{actor}/"): return Response("not found", 404)
    home = f"/caldav/calendars/{actor}/"; principal = f"/caldav/principals/{actor}/"
    parts = normalized.split("/") if normalized else []
    if request.method == "PROPFIND":
        if not normalized: return _multistatus([(request.path, f"<d:resourcetype><d:collection/></d:resourcetype><d:current-user-principal><d:href>{principal}</d:href></d:current-user-principal>", "HTTP/1.1 200 OK")])
        if normalized == f"principals/{actor}": return _multistatus([(principal, f"<d:resourcetype><d:principal/></d:resourcetype><d:displayname>{escape(actor)}</d:displayname><cal:calendar-home-set><d:href>{home}</d:href></cal:calendar-home-set>", "HTTP/1.1 200 OK")])
        if normalized == f"calendars/{actor}":
            items = [(home, "<d:resourcetype><d:collection/></d:resourcetype><d:displayname>SimpleOffice Kalender</d:displayname>", "HTTP/1.1 200 OK")]
            if request.headers.get("Depth", "0") != "0": items += [(home + c["calendar_id"] + "/", _calendar_properties(c, actor), "HTTP/1.1 200 OK") for c in store.calendars(actor)]
            return _multistatus(items)
        if len(parts) >= 3 and parts[:2] == ["calendars", actor]:
            try: calendar = store.get(parts[2], actor)
            except ValueError: return Response("not found", 404)
            if len(parts) == 3:
                items = [(request.path.rstrip("/") + "/", _calendar_properties(calendar, actor), "HTTP/1.1 200 OK")]
                if request.headers.get("Depth", "0") != "0": items += [(home + parts[2] + "/" + (e.get("caldav_resource") or e["event_id"] + ".ics"), f"<d:getetag>{store.etag(e)}</d:getetag><d:getcontenttype>text/calendar; charset=utf-8</d:getcontenttype>", "HTTP/1.1 200 OK") for e in store.resource_events(parts[2], actor)]
                return _multistatus(items)
            resource = parts[3]
            event = next((e for e in store.resource_events(parts[2], actor) if (e.get("caldav_resource") or e["event_id"] + ".ics") == resource), None)
            return _multistatus([(request.path, f"<d:getetag>{store.etag(event)}</d:getetag><d:getcontenttype>text/calendar; charset=utf-8</d:getcontenttype>", "HTTP/1.1 200 OK")]) if event else Response("not found", 404)
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
                def overlaps(event):
                    start = datetime.fromisoformat(event["start"].replace("Z", "+00:00")); end = datetime.fromisoformat((event.get("end") or event["start"]).replace("Z", "+00:00"))
                    if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
                    if end.tzinfo is None: end = end.replace(tzinfo=timezone.utc)
                    return start < upper and end >= lower
                events = [event for event in events if overlaps(event)]
        items = []
        for event in events:
            resource = event.get("caldav_resource") or event["event_id"] + ".ics"
            items.append((home + calendar_id + "/" + resource, f"<d:getetag>{store.etag(event)}</d:getetag><cal:calendar-data>{escape(_event_ics(event))}</cal:calendar-data>", "HTTP/1.1 200 OK"))
        return _multistatus(items)
    if len(parts) != 4: return Response("method requires an event resource", 405)
    resource = parts[3]; events = store.resource_events(calendar_id, actor); event = next((e for e in events if (e.get("caldav_resource") or e["event_id"] + ".ics") == resource), None)
    if request.method == "GET":
        return Response(_event_ics(event), 200, {"Content-Type": "text/calendar; charset=utf-8", "ETag": store.etag(event)}) if event else Response("not found", 404)
    if request.method == "PUT":
        current = store.etag(event) if event else ""; if_match = request.headers.get("If-Match")
        if request.headers.get("If-None-Match") == "*" and event: return Response("CalDAV precondition failed", 412, {"ETag": current})
        if if_match and (not event or if_match != current): return Response("CalDAV precondition failed", 412, {"ETag": current} if current else {})
        try: saved, created = store.put_event(calendar_id, resource, _parse_ics(request.get_data(as_text=True)), actor, current if if_match else None, request.headers.get("If-None-Match") == "*")
        except CalendarConflict as exc: return Response("CalDAV precondition failed", 412, {"ETag": store.etag(exc.event)} if exc.event else {})
        except ValueError as exc: return Response(str(exc), 409 if "UID already" in str(exc) else 400)
        return Response("", 201 if created else 204, {"ETag": store.etag(saved), "Location": request.path})
    if request.method == "DELETE":
        if not event: return Response("not found", 404)
        current = store.etag(event); if_match = request.headers.get("If-Match")
        if if_match and if_match != current: return Response("CalDAV precondition failed", 412, {"ETag": current})
        try: store.delete_event(calendar_id, resource, actor, current if if_match else None)
        except CalendarConflict as exc: return Response("CalDAV precondition failed", 412, {"ETag": store.etag(exc.event)} if exc.event else {})
        except ValueError as exc: return Response(str(exc), 403)
        return Response("", 204)
    return Response("not found", 404)
