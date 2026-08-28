"""Authenticated CalDAV collections with conditional writes and incremental sync."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
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
from .todo_store import TodoConflict, TodoStore


bp = Blueprint("caldav", __name__)
DAV = "DAV:"
CAL = "urn:ietf:params:xml:ns:caldav"
MAX_XML = 1024 * 1024
MAX_HREFS = 500


def _store() -> CalendarCollections:
    return CalendarCollections(current_app.config["DOCUMENT_ROOT"])


def _todos() -> TodoStore:
    return TodoStore(current_app.config["DOCUMENT_ROOT"])


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


def _task_path(item: dict, actor: str) -> str:
    return "tasks" if item["list_id"] == TodoStore.default_list_id(actor) else "tasks-" + item["list_id"]


def _task_list_id(calendar_id: str, actor: str) -> str:
    return TodoStore.default_list_id(actor) if calendar_id == "tasks" else calendar_id.removeprefix("tasks-")


def _task_collection_properties(actor: str, item: dict | None = None) -> str:
    item = item or next(row for row in _todos().lists(actor) if row["list_id"] == TodoStore.default_list_id(actor))
    _, token = _todos().sync_changes(actor, list_id=item["list_id"])
    write = item.get("owner") == actor or bool(set((item.get("permissions") or {}).get(actor, [])) & {"create", "edit", "complete", "delete", "manage"})
    return f'<d:resourcetype><d:collection/><cal:calendar/></d:resourcetype><d:displayname>{escape(item["name"])}</d:displayname><cal:calendar-description>{escape(item.get("description", ""))}</cal:calendar-description><cal:calendar-color>{escape(item.get("color", "#2563eb"))}</cal:calendar-color><cal:supported-calendar-data><cal:calendar-data content-type="text/calendar" version="2.0"/></cal:supported-calendar-data><cal:supported-calendar-component-set><cal:comp name="VTODO"/></cal:supported-calendar-component-set><d:sync-token>{escape(token)}</d:sync-token>{_privileges(write)}'


def _todo_ics(item: dict) -> str:
    def esc(value: Any) -> str:
        return str(value).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def stamp(value: str) -> tuple[str, str]:
        value = str(value or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value): return ";VALUE=DATE", value.replace("-", "")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        return "", parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    try:
        updated = datetime.fromisoformat(str(item.get("updated_at") or item.get("created_at") or "").replace("Z", "+00:00"))
        if updated.tzinfo is None: updated = updated.replace(tzinfo=timezone.utc)
        now = updated.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    except ValueError:
        now = "19700101T000000Z"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SimpleOffice4Me//CalDAV Tasks//EN"]
    lines.extend(str(line) for line in item.get("calendar_extra_lines", []))
    lines.extend(["BEGIN:VTODO", f"UID:{esc(item.get('uid') or item['id'] + '@simpleoffice.local')}", f"DTSTAMP:{item.get('ical_dtstamp') or now}"])
    if item.get("ical_created"): lines.append(f"CREATED:{item['ical_created']}")
    if item.get("ical_last_modified"): lines.append(f"LAST-MODIFIED:{item['ical_last_modified']}")
    lines.extend([f"SEQUENCE:{int(item.get('sequence', 0) or 0)}", f"SUMMARY:{esc(item.get('title', ''))}"])
    if item.get("description"): lines.append(f"DESCRIPTION:{esc(item['description'])}")
    if item.get("start"):
        parameter, value = stamp(item["start"]); lines.append(f"DTSTART{parameter}:{value}")
    if item.get("due"):
        parameter, value = stamp(item["due"]); lines.append(f"DUE{parameter}:{value}")
    status = str(item.get("status", "needs-action")).upper(); lines.append(f"STATUS:{status}")
    lines.append(f"PERCENT-COMPLETE:{int(item.get('percent_complete', 100 if status == 'COMPLETED' else 0))}")
    if int(item.get("priority", 0) or 0): lines.append(f"PRIORITY:{int(item['priority'])}")
    if item.get("completed_at"):
        _, value = stamp(item["completed_at"]); lines.append(f"COMPLETED:{value}")
    if item.get("categories"): lines.append("CATEGORIES:" + ",".join(esc(value) for value in item["categories"]))
    if item.get("classification"): lines.append("CLASS:" + esc(item["classification"]))
    if item.get("url"): lines.append("URL:" + esc(item["url"]))
    organizer = str(item.get("organizer", ""))
    if organizer: lines.append(organizer if organizer.upper().startswith("ORGANIZER") else "ORGANIZER:" + organizer)
    lines.extend(line if str(line).upper().startswith("ATTENDEE") else "ATTENDEE:" + str(line) for line in item.get("attendees", []))
    relations = list(item.get("related_to", []))
    if item.get("parent_uid") and not relations: relations.append("RELATED-TO;RELTYPE=PARENT:" + item["parent_uid"])
    lines.extend(line if str(line).upper().startswith("RELATED-TO") else "RELATED-TO:" + str(line) for line in relations)
    if item.get("rrule"): lines.append("RRULE:" + str(item["rrule"]))
    lines.extend(line if str(line).upper().startswith("RDATE") else "RDATE:" + str(line) for line in item.get("rdates", []))
    lines.extend(line if str(line).upper().startswith("EXDATE") else "EXDATE:" + str(line) for line in item.get("exdates", []))
    if item.get("project_id"): lines.append("X-SIMPLEOFFICE-PROJECT-ID:" + esc(item["project_id"]))
    if item.get("contact_id"): lines.append("X-SIMPLEOFFICE-CONTACT-ID:" + esc(item["contact_id"]))
    for document_id in item.get("document_ids", []): lines.append("X-SIMPLEOFFICE-DOCUMENT-ID:" + esc(document_id))
    lines.extend(str(line) for line in item.get("extra_lines", []) if str(line).upper() not in {"BEGIN:VTODO", "END:VTODO"})
    return "\r\n".join([*lines, "END:VTODO", "END:VCALENDAR", ""])


def _parse_vtodo(content: str) -> dict[str, Any]:
    from .calendar_description import split_content_line
    if len(content.encode("utf-8")) > 1024 * 1024: raise ValueError("task resource exceeds 1 MiB")
    unfolded: list[str] = []
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and unfolded: unfolded[-1] += line[1:]
        else: unfolded.append(line)
    if sum(line.upper() == "BEGIN:VTODO" for line in unfolded) != 1 or sum(line.upper() == "END:VTODO" for line in unfolded) != 1:
        raise ValueError("task resource requires exactly one VTODO")
    lines: list[str] = []; active = False
    for line in unfolded:
        if line.upper() == "BEGIN:VTODO": active = True; continue
        if line.upper() == "END:VTODO": active = False; continue
        if active: lines.append(line)
    unescape = lambda value: re.sub(r"\\([nN,;\\])", lambda match: "\n" if match.group(1).lower() == "n" else match.group(1), value)
    known = {"UID", "SUMMARY", "DESCRIPTION", "STATUS", "PERCENT-COMPLETE", "PRIORITY", "DTSTART", "DUE", "COMPLETED", "CATEGORIES", "DTSTAMP", "CREATED", "LAST-MODIFIED", "SEQUENCE", "CLASS", "URL", "ORGANIZER", "RRULE"}
    repeated = {"ATTENDEE", "RELATED-TO", "RDATE", "EXDATE", "X-SIMPLEOFFICE-DOCUMENT-ID"}
    fields: dict[str, tuple[str, str]] = {}; multiples: dict[str, list[str]] = {key: [] for key in repeated}; extra: list[str] = []; alarm = False
    for line in lines:
        upper = line.upper()
        if upper == "BEGIN:VALARM": alarm = True; extra.append(line); continue
        if alarm:
            extra.append(line)
            if upper == "END:VALARM": alarm = False
            continue
        if ":" not in line: continue
        left, value = split_content_line(line); key = left.split(";", 1)[0].upper()
        if key in repeated:
            multiples[key].append(line)
        elif key in known:
            if key in fields: raise ValueError(f"{key} must not occur more than once in a VTODO")
            fields[key] = (left, value)
        elif key not in {"X-SIMPLEOFFICE-PROJECT-ID", "X-SIMPLEOFFICE-CONTACT-ID"}: extra.append(line)
        else: fields[key] = (left, value)
    uid = unescape(fields.get("UID", ("", ""))[1]).strip()
    if not uid: raise ValueError("every VTODO requires UID")
    title = unescape(fields.get("SUMMARY", ("", ""))[1]).strip()
    if not title: raise ValueError("every VTODO requires SUMMARY")
    status = fields.get("STATUS", ("", "NEEDS-ACTION"))[1].strip().lower()
    if status not in {"needs-action", "in-process", "completed", "cancelled"}: raise ValueError("invalid VTODO STATUS")
    try: percent = max(0, min(100, int(fields.get("PERCENT-COMPLETE", ("", "100" if status == "completed" else "0"))[1])))
    except ValueError as exc: raise ValueError("invalid VTODO PERCENT-COMPLETE") from exc
    try: priority = max(0, min(9, int(fields.get("PRIORITY", ("", "0"))[1] or 0)))
    except ValueError as exc: raise ValueError("invalid VTODO PRIORITY") from exc

    def date_value(entry: tuple[str, str] | None) -> str:
        if not entry: return ""
        left, value = entry
        if "VALUE=DATE" in left.upper():
            try: return datetime.strptime(value, "%Y%m%d").date().isoformat()
            except ValueError as exc: raise ValueError("invalid VTODO date") from exc
        return parse_ical_datetime(left, value)[0]

    start_value = date_value(fields.get("DTSTART"))
    if fields.get("RRULE", ("", ""))[1] or multiples["RDATE"]:
        if not start_value: raise ValueError("recurring VTODO requires DTSTART")
        timezone_id = parse_ical_datetime(*fields["DTSTART"])[1]
        rdate_values: list[str] = []; exdate_values: list[str] = []
        for line in multiples["RDATE"]:
            left, value = split_content_line(line); parsed, _ = parse_ical_list(left, value, timezone_id); rdate_values.extend(parsed)
        for line in multiples["EXDATE"]:
            left, value = split_content_line(line); parsed, _ = parse_ical_list(left, value, timezone_id); exdate_values.extend(parsed)
        try: validate_recurrence({"rrule": fields.get("RRULE", ("", ""))[1], "rdates": rdate_values, "exdates": exdate_values, "timezone": timezone_id}, start_value)
        except RecurrenceError as exc: raise ValueError(str(exc)) from exc

    try: sequence = max(0, int(fields.get("SEQUENCE", ("", "0"))[1] or 0))
    except ValueError as exc: raise ValueError("invalid VTODO SEQUENCE") from exc
    calendar_extra = []
    active_calendar = True
    for line in unfolded:
        if line.upper() == "BEGIN:VTODO": active_calendar = False
        elif line.upper() == "END:VTODO": active_calendar = True
        elif active_calendar and line and line.upper() not in {"BEGIN:VCALENDAR", "END:VCALENDAR", "VERSION:2.0"} and not line.upper().startswith("PRODID:"):
            calendar_extra.append(line)
    completed_value = date_value(fields.get("COMPLETED"))
    if completed_value: status = "completed"; percent = 100
    return {"uid": uid, "title": title, "description": unescape(fields.get("DESCRIPTION", ("", ""))[1]), "status": status, "percent_complete": 100 if status == "completed" else percent, "priority": priority, "start": start_value, "due": date_value(fields.get("DUE")), "completed_at": completed_value, "categories": [unescape(value).strip() for value in fields.get("CATEGORIES", ("", ""))[1].split(",") if value.strip()], "classification": unescape(fields.get("CLASS", ("", ""))[1]), "url": unescape(fields.get("URL", ("", ""))[1]), "organizer": (fields.get("ORGANIZER", ("", ""))[0] + ":" + fields.get("ORGANIZER", ("", ""))[1]) if "ORGANIZER" in fields else "", "attendees": multiples["ATTENDEE"], "related_to": multiples["RELATED-TO"], "rrule": fields.get("RRULE", ("", ""))[1], "rdates": multiples["RDATE"], "exdates": multiples["EXDATE"], "sequence": sequence, "ical_created": fields.get("CREATED", ("", ""))[1], "ical_last_modified": fields.get("LAST-MODIFIED", ("", ""))[1], "ical_dtstamp": fields.get("DTSTAMP", ("", ""))[1], "project_id": unescape(fields.get("X-SIMPLEOFFICE-PROJECT-ID", ("", ""))[1]), "contact_id": unescape(fields.get("X-SIMPLEOFFICE-CONTACT-ID", ("", ""))[1]), "document_ids": [unescape(line.split(":", 1)[1]) for line in multiples["X-SIMPLEOFFICE-DOCUMENT-ID"]], "calendar_extra_lines": calendar_extra, "extra_lines": extra, "raw_ics": content}


def _task_endpoint(actor: str, parts: list[str], home: str) -> Response:
    """Expose the existing SimpleOffice task list as a CalDAV VTODO calendar."""
    store = _todos()
    list_id = _task_list_id(parts[2], actor)
    try: task_list = next(row for row in store.lists(actor) if row["list_id"] == list_id)
    except StopIteration: return Response("not found", 404)
    collection = home + parts[2] + "/"
    if len(parts) == 3:
        if request.method == "PROPFIND":
            items = [(collection, _task_collection_properties(actor, task_list), "HTTP/1.1 200 OK")]
            if request.headers.get("Depth", "0") != "0":
                items.extend(
                    (
                        collection + store.resource(item),
                        f"<d:getetag>{store.etag(item)}</d:getetag><d:getcontenttype>text/calendar; charset=utf-8</d:getcontenttype>",
                        "HTTP/1.1 200 OK",
                    )
                    for item in store.items(actor, list_id=list_id)
                )
            return _multistatus(items)
        if request.method == "REPORT":
            try:
                root = _xml_root()
            except ValueError as exc:
                return Response(str(exc), 400)
            if root.tag == f"{{{DAV}}}sync-collection":
                token = (root.findtext(f"{{{DAV}}}sync-token") or "").strip()
                try:
                    changes, new_token = store.sync_changes(actor, token, list_id)
                except ValueError:
                    return Response(f'<d:error xmlns:d="{DAV}"><d:valid-sync-token/></d:error>', 403, {"Content-Type": "application/xml"})
                current = {store.resource(item): item for item in store.items(actor, list_id=list_id)}
                rows = []
                for change in changes:
                    href = collection + change["resource"]
                    item = current.get(change["resource"])
                    if change.get("deleted") or item is None:
                        rows.append((href, "", "HTTP/1.1 404 Not Found"))
                    else:
                        rows.append((href, f"<d:getetag>{store.etag(item)}</d:getetag>", "HTTP/1.1 200 OK"))
                return _multistatus(rows, new_token)
            if root.tag not in {f"{{{CAL}}}calendar-query", f"{{{CAL}}}calendar-multiget"}:
                return Response("unsupported task report", 403)
            tasks = store.items(actor, list_id=list_id)
            hrefs = [node.text or "" for node in root.findall(f".//{{{DAV}}}href")]
            if len(hrefs) > MAX_HREFS:
                return Response("too many DAV hrefs", 413)
            if root.tag == f"{{{CAL}}}calendar-multiget":
                wanted = {href.rstrip("/").rsplit("/", 1)[-1] for href in hrefs}
                tasks = [item for item in tasks if store.resource(item) in wanted]
            else:
                component_names = {node.attrib.get("name", "").upper() for node in root.findall(f".//{{{CAL}}}comp-filter")}
                if "VEVENT" in component_names and "VTODO" not in component_names:
                    tasks = []
                time_range = root.find(f".//{{{CAL}}}time-range")
                if time_range is not None:
                    try:
                        lower = datetime.strptime(time_range.attrib.get("start", "19700101T000000Z"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                        upper = datetime.strptime(time_range.attrib.get("end", "99991231T235959Z"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                        if lower >= upper:
                            raise ValueError
                    except ValueError:
                        return Response("invalid CalDAV task time-range", 400)

                    def in_range(item: dict[str, Any]) -> bool:
                        raw = item.get("due") or item.get("start")
                        if not raw:
                            return False
                        try:
                            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                        except ValueError:
                            return False
                        if value.tzinfo is None:
                            value = value.replace(tzinfo=timezone.utc)
                        return lower <= value.astimezone(timezone.utc) < upper

                    tasks = [item for item in tasks if in_range(item)]
            return _multistatus([
                (
                    collection + store.resource(item),
                    f"<d:getetag>{store.etag(item)}</d:getetag><cal:calendar-data>{escape(_todo_ics(item))}</cal:calendar-data>",
                    "HTTP/1.1 200 OK",
                )
                for item in tasks
            ])
        if request.method == "MKCALENDAR":
            return Response("the built-in task calendar already exists", 405)
        if request.method == "DELETE":
            return Response("the built-in task calendar cannot be deleted", 409)
        return Response("method requires a task resource", 405)

    if len(parts) != 4:
        return Response("invalid task resource path", 404)
    resource = parts[3]
    item = store.get_resource(resource, actor, list_id)
    if request.method == "PROPFIND":
        if item is None:
            return Response("not found", 404)
        return _multistatus([(request.path, f"<d:getetag>{store.etag(item)}</d:getetag><d:getcontenttype>text/calendar; charset=utf-8</d:getcontenttype>", "HTTP/1.1 200 OK")])
    if request.method == "GET":
        if item is None:
            return Response("not found", 404)
        return Response(_todo_ics(item), 200, {"Content-Type": "text/calendar; charset=utf-8", "ETag": store.etag(item), "Cache-Control": "no-store"})
    if request.method == "PUT":
        current = store.etag(item) if item else ""
        if request.headers.get("If-None-Match") == "*" and item:
            return Response("CalDAV task precondition failed", 412, {"ETag": current})
        if_match = request.headers.get("If-Match")
        if if_match and (not item or if_match != current):
            return Response("CalDAV task precondition failed", 412, {"ETag": current} if current else {})
        try:
            values = _parse_vtodo(request.get_data(as_text=True))
            saved, created = store.put_resource(resource, values, actor, current if if_match else None, request.headers.get("If-None-Match") == "*", list_id)
        except TodoConflict as exc:
            return Response("CalDAV task precondition failed", 412, {"ETag": store.etag(exc.item)} if exc.item else {})
        except ValueError as exc:
            return Response(str(exc), 409 if "UID already" in str(exc) else 400)
        return Response("", 201 if created else 204, {"ETag": store.etag(saved), "Location": request.path})
    if request.method == "DELETE":
        if item is None:
            return Response("not found", 404)
        current = store.etag(item)
        if_match = request.headers.get("If-Match")
        if if_match and if_match != current:
            return Response("CalDAV task precondition failed", 412, {"ETag": current})
        try:
            store.delete_resource(resource, actor, current if if_match else None, list_id)
        except TodoConflict as exc:
            return Response("CalDAV task precondition failed", 412, {"ETag": store.etag(exc.item)} if exc.item else {})
        except ValueError as exc:
            return Response(str(exc), 404)
        return Response("", 204)
    return Response("method not allowed on task resource", 405)


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
    if event.get("description_html"):
        lines.append(f"X-ALT-DESC;FMTTYPE=text/html:{esc(event['description_html'])}")
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
    protected = ("source_uid", "title", "reason", "description_html", "description_format", "start", "end", "status", "sequence", "tags", "ical_status", "transparency", "classification", "priority", "location", "event_url", "resources", "conferences")
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
    from .calendar_description import html_to_text, sanitize_calendar_html, split_content_line
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
            left, value = split_content_line(line); key = left.split(";", 1)[0].upper()
            if key in repeated: repeated[key].append((left, value))
            elif key in {"UID", "SUMMARY", "DESCRIPTION", "X-ALT-DESC", "DTSTART", "DTEND", "CATEGORIES", "STATUS", "SEQUENCE", "ORGANIZER", "RRULE", "RECURRENCE-ID", "TRANSP", "CLASS", "PRIORITY", "LOCATION", "URL", "RESOURCES"}:
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
    rich_entry = fields.get("X-ALT-DESC", ("", ""))
    description_entry = fields.get("DESCRIPTION", ("", ""))
    rich_html = sanitize_calendar_html(unescape(rich_entry[1])) if "FMTTYPE=TEXT/HTML" in rich_entry[0].upper() else ""
    if not rich_html and "FMTTYPE=TEXT/HTML" in description_entry[0].upper():
        rich_html = sanitize_calendar_html(unescape(description_entry[1]))
    plain = ("" if "FMTTYPE=TEXT/HTML" in description_entry[0].upper() else unescape(description_entry[1])) or html_to_text(rich_html)
    return {"uid": uid, "title": unescape(fields.get("SUMMARY", ("", "Ohne Titel"))[1]).strip() or "Ohne Titel", "description": plain, "description_html": rich_html, "description_format": "html" if rich_html else "text", "start": start, "end": end, "timezone": tzid, "recurrence": recurrence, "recurrence_overrides": overrides, "alarms": alarms, "status": "cancelled" if status == "CANCELLED" else "active", "sequence": sequence, "tags": [{"name": unescape(tag).strip(), "visibility": "private"} for tag in fields.get("CATEGORIES", ("", ""))[1].split(",") if tag.strip()], "organizer": person(fields["ORGANIZER"]) if "ORGANIZER" in fields else {}, "participants": participants, "raw_ics": content, **metadata}


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
            if request.headers.get("Depth", "0") != "0":
                items += [(home + c["calendar_id"] + "/", _calendar_properties(c, actor), "HTTP/1.1 200 OK") for c in store.calendars(actor)]
                items += [(home + _task_path(task_list, actor) + "/", _task_collection_properties(actor, task_list), "HTTP/1.1 200 OK") for task_list in _todos().lists(actor) if not task_list.get("archived")]
            return _multistatus(items)
        if len(parts) >= 3 and parts[:2] == ["calendars", actor]:
            if parts[2] == "tasks" or parts[2].startswith("tasks-"):
                return _task_endpoint(actor, parts, home)
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
    if calendar_id == "tasks" or calendar_id.startswith("tasks-"):
        if request.method == "MKCALENDAR" and calendar_id != "tasks":
            try:
                root = _xml_root(); name = root.findtext(f".//{{{DAV}}}displayname") or calendar_id.removeprefix("tasks-")
                description = root.findtext(f".//{{{CAL}}}calendar-description") or ""
                _todos().create_list({"name": name, "description": description}, actor, calendar_id.removeprefix("tasks-"))
            except ValueError as exc: return Response(str(exc), 405 if "already exists" in str(exc) else 400)
            return Response("", 201, {"Location": request.path.rstrip("/") + "/"})
        return _task_endpoint(actor, parts, home)
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
