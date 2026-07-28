"""Read-only Google People and Calendar import with stable source identifiers."""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen

from flask import current_app

from .calendar_store import CalendarStore
from .contact_store import ContactStore


PEOPLE_URL = "https://people.googleapis.com/v1/people/me/connections"
CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"


def _get_json(url: str, access_token: str) -> dict:
    request = Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Google API returned invalid JSON")
    return payload


def _first_value(values: list[dict], key: str = "value") -> str:
    return str(values[0].get(key, "")).strip() if values else ""


def _event_time(value: dict) -> str:
    raw = str(value.get("dateTime") or value.get("date") or "").strip()
    if not raw:
        return ""
    if len(raw) == 10:
        return raw + "T00:00"
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None).isoformat(timespec="minutes")


def sync_google_account(access_token: str, actor: str, account_subject: str) -> dict[str, int]:
    """Import contacts and all readable calendars. Repeated calls update by Google IDs."""
    root = current_app.config["DOCUMENT_ROOT"]
    contacts = ContactStore(root)
    calendar = CalendarStore(root)
    result = {"contacts": 0, "events": 0, "calendars": 0}

    page_token = ""
    while True:
        query = urlencode({"personFields": "names,emailAddresses,phoneNumbers,organizations", "pageSize": 1000, **({"pageToken": page_token} if page_token else {})})
        payload = _get_json(f"{PEOPLE_URL}?{query}", access_token)
        for person in payload.get("connections", []):
            resource_name = str(person.get("resourceName", "")).strip()
            name = _first_value(person.get("names", []), "displayName")
            if not resource_name or not name:
                continue
            contacts.upsert({"display_name": name, "email": _first_value(person.get("emailAddresses", [])), "phone": _first_value(person.get("phoneNumbers", [])), "company": _first_value(person.get("organizations", []), "name")}, actor, source={"provider": "google_people", "account": account_subject, "source_id": resource_name})
            result["contacts"] += 1
        page_token = str(payload.get("nextPageToken", ""))
        if not page_token:
            break

    calendars = _get_json(f"{CALENDAR_LIST_URL}?{urlencode({'minAccessRole': 'reader'})}", access_token).get("items", [])
    for item in calendars:
        calendar_id = str(item.get("id", "")).strip()
        if not calendar_id:
            continue
        result["calendars"] += 1
        page_token = ""
        while True:
            query = urlencode({"singleEvents": "true", "showDeleted": "true", "maxResults": 2500, **({"pageToken": page_token} if page_token else {})})
            payload = _get_json(f"{CALENDAR_EVENTS_URL.format(calendar_id=quote(calendar_id, safe=''))}?{query}", access_token)
            for item_event in payload.get("items", []):
                event_id = str(item_event.get("id", "")).strip()
                start = _event_time(item_event.get("start", {}))
                if not event_id or not start:
                    continue
                calendar.upsert_external_event({"title": str(item_event.get("summary", "")), "reason": str(item_event.get("description", "")), "start": start, "end": _event_time(item_event.get("end", {})), "status": str(item_event.get("status", "confirmed"))}, actor, {"provider": "google_calendar", "account": account_subject, "calendar_id": calendar_id, "calendar_name": str(item.get("summary", calendar_id)), "source_id": f"{calendar_id}:{event_id}"})
                result["events"] += 1
            page_token = str(payload.get("nextPageToken", ""))
            if not page_token:
                break
    return result
