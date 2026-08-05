"""Optional, explicit Google Calendar API synchronization.

The remote side is read-only. Applying a preview writes mapped events to the
selected local calendar, but never pushes local data to Google.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from .calendar_metadata import normalize_metadata
from .calendar_store import CalendarStore
from .calendar_collections import CalendarCollections
from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory

TOKEN_URL = "https://oauth2.googleapis.com/token"
API_ROOT = "https://www.googleapis.com/calendar/v3"
MAX_BYTES = 5 * 1024 * 1024
MAX_PAGES = 20
MAX_EVENTS = 5000
TIMEOUT = 10


class GoogleCalendarError(ValueError):
    pass


class GoogleGone(GoogleCalendarError):
    pass


Transport = Callable[[str, str, dict[str, str], bytes | None], dict[str, Any]]


class GoogleCalendarSync:
    def __init__(self, root: str | Path, transport: Transport | None = None):
        self.root = Path(root).expanduser().resolve()
        self.state_path = self.root / CONTROL_DIR / "google-calendar-sync.json"
        self.events = CalendarStore(self.root)
        self.calendars = CalendarCollections(self.root)
        self.history = RevisionHistory(self.root)
        self.transport = transport or self._http

    @staticmethod
    def _accounts() -> dict[str, dict[str, str]]:
        raw = os.environ.get("SIMPLEOFFICE_GOOGLE_CALENDAR_ACCOUNTS_JSON", "")
        if not raw:
            return {}
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GoogleCalendarError("Google calendar account configuration is invalid JSON") from exc
        if not isinstance(values, dict):
            raise GoogleCalendarError("Google calendar account configuration must be an object")
        return values

    def configured(self, actor: str) -> bool:
        try:
            cfg = self._config(actor)
            return bool(cfg)
        except GoogleCalendarError:
            return False

    def status(self, actor: str) -> dict[str, Any]:
        state = self._state().get(actor, {})
        try:
            cfg = self._config(actor, required=False)
            error = ""
        except GoogleCalendarError as exc:
            cfg = {}
            error = str(exc)
        return {
            "configured": bool(cfg),
            "calendar_id": cfg.get("calendar_id", "") if cfg else "",
            "target_calendar_id": cfg.get("target_calendar_id", "default") if cfg else "default",
            "last_sync": state.get("last_sync", ""),
            "has_sync_token": bool(state.get("sync_token")),
            "last_result": state.get("last_result", {}),
            "configuration_error": error,
        }

    def _config(self, actor: str, required: bool = True) -> dict[str, str]:
        value = self._accounts().get(actor, {})
        required_keys = ("client_id", "client_secret", "refresh_token", "calendar_id")
        if not value or any(not str(value.get(key, "")).strip() for key in required_keys):
            if required:
                raise GoogleCalendarError("Google Calendar is not configured for this user")
            return {}
        return {
            key: str(value.get(key, "")).strip()
            for key in (*required_keys, "target_calendar_id")
        } | {"target_calendar_id": str(value.get("target_calendar_id", "default")).strip() or "default"}

    @staticmethod
    def _http(method: str, url: str, headers: dict[str, str], body: bytes | None) -> dict[str, Any]:
        if not (url.startswith(TOKEN_URL) or url.startswith(API_ROOT + "/")):
            raise GoogleCalendarError("Google request host is not allowed")
        request = Request(url, data=body, method=method, headers={**headers, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=TIMEOUT) as response:
                payload = response.read(MAX_BYTES + 1)
        except HTTPError as exc:
            if exc.code == 410:
                raise GoogleGone("Google sync token expired") from exc
            raise GoogleCalendarError(f"Google Calendar returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise GoogleCalendarError("Google Calendar is temporarily unavailable") from exc
        if len(payload) > MAX_BYTES:
            raise GoogleCalendarError("Google Calendar response exceeds 5 MiB")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GoogleCalendarError("Google Calendar returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise GoogleCalendarError("Google Calendar returned an invalid response")
        return value

    def _token(self, cfg: dict[str, str]) -> str:
        body = urlencode({
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": cfg["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()
        result = self.transport("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        token = str(result.get("access_token", "")).strip()
        if not token:
            raise GoogleCalendarError("Google OAuth did not return an access token")
        return token

    def _fetch(self, actor: str, sync_token: str = "") -> tuple[list[dict[str, Any]], str]:
        cfg = self._config(actor)
        access_token = self._token(cfg)
        events: list[dict[str, Any]] = []
        page_token = ""
        for _ in range(MAX_PAGES):
            query = {"maxResults": "2500", "showDeleted": "true", "singleEvents": "false"}
            if sync_token:
                query["syncToken"] = sync_token
            if page_token:
                query["pageToken"] = page_token
            url = f"{API_ROOT}/calendars/{quote(cfg['calendar_id'], safe='')}/events?{urlencode(query)}"
            result = self.transport("GET", url, {"Authorization": f"Bearer {access_token}"}, None)
            items = result.get("items", [])
            if not isinstance(items, list):
                raise GoogleCalendarError("Google Calendar event list is invalid")
            events.extend(item for item in items if isinstance(item, dict))
            if len(events) > MAX_EVENTS:
                raise GoogleCalendarError("Google Calendar sync exceeds 5000 changed events")
            page_token = str(result.get("nextPageToken", ""))
            if not page_token:
                next_sync = str(result.get("nextSyncToken", ""))
                if not next_sync:
                    raise GoogleCalendarError("Google Calendar did not return nextSyncToken")
                return events, next_sync
        raise GoogleCalendarError("Google Calendar sync exceeds 20 pages")

    @staticmethod
    def _date(value: dict[str, Any]) -> tuple[str, str]:
        date_time = str(value.get("dateTime", "")).strip()
        if date_time:
            return date_time, str(value.get("timeZone", ""))
        day = str(value.get("date", "")).strip()
        if day:
            return day + "T00:00:00", str(value.get("timeZone", ""))
        return "", ""

    @classmethod
    def map_event(cls, value: dict[str, Any]) -> dict[str, Any]:
        event_id = str(value.get("id", "")).strip()
        if not event_id:
            raise GoogleCalendarError("Google event has no immutable id")
        start, timezone_id = cls._date(value.get("start", {}))
        end, end_timezone = cls._date(value.get("end", {}))
        cancelled = value.get("status") == "cancelled"
        if not cancelled and not start:
            raise GoogleCalendarError("Google event has no start")
        conferences = []
        for entry in value.get("conferenceData", {}).get("entryPoints", []):
            uri = str(entry.get("uri", "")).strip()
            if uri and urlsplit(uri).scheme.lower() in {"https", "http", "tel"}:
                kind = str(entry.get("entryPointType", "")).lower()
                feature = {"video": "video", "phone": "phone"}.get(kind, "audio" if kind == "sip" else "")
                conferences.append({"uri": uri, "label": entry.get("label", ""), "features": [feature] if feature else []})
        metadata = normalize_metadata({
            "ical_status": "cancelled" if cancelled else ("tentative" if value.get("status") == "tentative" else "confirmed"),
            "transparency": "transparent" if value.get("transparency") == "transparent" else "opaque",
            "classification": value.get("visibility", "private") if value.get("visibility") in {"public", "private", "confidential"} else "private",
            "location": value.get("location", ""),
            "event_url": value.get("htmlLink", ""),
            "conferences": conferences,
        })
        participants = []
        seen_attendees: set[str] = set()
        for row in value.get("attendees", []):
            email = str(row.get("email", "")).strip().lower()
            if not email or "@" not in email or email in seen_attendees:
                continue
            seen_attendees.add(email)
            participants.append({
                "email": email[:254],
                "name": str(row.get("displayName", ""))[:120],
                "status": {"needsAction": "needs-action", "accepted": "accepted", "declined": "declined", "tentative": "tentative"}.get(row.get("responseStatus"), "needs-action"),
                "role": "optional" if row.get("optional") else "required",
                "rsvp": row.get("responseStatus") == "needsAction",
            })
        recurrence = next((str(item)[6:] for item in value.get("recurrence", []) if str(item).upper().startswith("RRULE:")), "")
        return {
            "title": str(value.get("summary") or "Ohne Titel")[:300],
            "reason": str(value.get("description") or "Aus Google Kalender importiert")[:2000],
            "start": start, "end": end, "timezone": timezone_id or end_timezone,
            "status": "cancelled" if cancelled else "confirmed",
            "participants": participants[:200],
            "organizer": {"email": str(value.get("organizer", {}).get("email", "")).lower(), "name": str(value.get("organizer", {}).get("displayName", ""))[:120]} if value.get("organizer", {}).get("email") else {},
            "recurrence": {"rrule": recurrence, "rdates": [], "exdates": [], "timezone": timezone_id} if recurrence else {},
            "_deleted_stub": bool(cancelled and not start),
            **metadata,
            "_source": {"provider": "google-calendar", "source_id": event_id, "etag": str(value.get("etag", "")), "updated": str(value.get("updated", "")), "ical_uid": str(value.get("iCalUID", ""))},
        }

    def synchronize(self, actor: str, apply: bool = False, conflict_policy: str = "") -> dict[str, Any]:
        """Preview or apply one serialized pull operation for ``actor``."""
        if conflict_policy not in {"", "google", "local"}:
            raise GoogleCalendarError("invalid Google Calendar conflict policy")
        if conflict_policy and not apply:
            raise GoogleCalendarError("conflicts can only be resolved while applying")
        with exclusive_file_lock(self.state_path.parent / f".google-calendar-sync-{actor}.lock"):
            return self._synchronize(actor, apply, conflict_policy)

    @staticmethod
    def _cancelled_values(item: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any] | None:
        if not item.pop("_deleted_stub", False):
            return item
        if previous is None:
            return None
        source_copy = item["_source"]
        return {
            key: previous.get(key)
            for key in ("title", "reason", "start", "end", "timezone", "participants", "organizer", "recurrence", "ical_status", "transparency", "classification", "priority", "location", "event_url", "resources", "conferences")
        } | {"status": "cancelled", "ical_status": "cancelled", "_source": source_copy}

    def _synchronize(self, actor: str, apply: bool, conflict_policy: str) -> dict[str, Any]:
        cfg = self._config(actor)
        target = self.calendars.get(cfg["target_calendar_id"], actor, write=True)
        state = self._state(); account_state = state.get(actor, {})
        token = str(account_state.get("sync_token", ""))
        reset = False
        try:
            raw, next_token = self._fetch(actor, token)
        except GoogleGone:
            raw, next_token = self._fetch(actor, "")
            reset = True
        mapped = [self.map_event(item) for item in raw]
        local = {
            item.get("source", {}).get("source_id"): item
            for item in self.events.events(actor)
            if isinstance(item.get("source"), dict) and item["source"].get("provider") == "google-calendar"
        }
        conflicts = []
        applicable = []
        keep_local = []
        for item in mapped:
            source = item["_source"]; previous = local.get(source["source_id"])
            locally_changed = previous and previous.get("updated_by") != f"google:{actor}" and previous.get("updated_at", "") > previous.get("source", {}).get("synced_at", "")
            remotely_changed = not previous or previous.get("source", {}).get("etag") != source["etag"]
            if locally_changed and remotely_changed:
                conflict = {"source_id": source["source_id"], "title": previous.get("title", ""), "remote_title": item["title"], "local_updated_at": previous.get("updated_at", ""), "remote_updated_at": source["updated"]}
                if conflict_policy == "google":
                    candidate = self._cancelled_values(item, previous)
                    if candidate is not None:
                        applicable.append(candidate)
                elif conflict_policy == "local":
                    keep_local.append((previous, source))
                else:
                    conflicts.append(conflict)
            elif remotely_changed:
                # Deleted incremental resources do not necessarily carry
                # DTSTART/DTEND. Preserve the last known interval so the local
                # lifecycle can be changed without inventing a timestamp.
                candidate = self._cancelled_values(item, previous)
                if candidate is not None:
                    applicable.append(candidate)
        result = {"received": len(mapped), "applicable": len(applicable), "conflicts": conflicts, "reset": reset, "applied": 0, "kept_local": 0, "conflict_policy": conflict_policy}
        if apply:
            for item in applicable:
                item = dict(item)
                source = {**item.pop("_source"), "synced_at": utc_now()}
                event = self.events.upsert_external_event(item, f"google:{actor}", source, owner=target["owner"], calendar_id=cfg["target_calendar_id"], access_actor=actor)
                self.calendars.record_event_move(event, cfg["target_calendar_id"], actor)
                result["applied"] += 1
            for previous, remote_source in keep_local:
                source = {**remote_source, "synced_at": utc_now()}
                self.events.acknowledge_external_version(previous["event_id"], actor, source, previous.get("updated_at", ""))
                result["kept_local"] += 1
            if not conflicts:
                state[actor] = {"sync_token": next_token, "last_sync": utc_now(), "last_result": {key: value for key, value in result.items() if key != "conflicts"}}
                atomic_json_write(self.state_path, state)
            else:
                state[actor] = {**account_state, "last_result": {**{key: value for key, value in result.items() if key != "conflicts"}, "conflict_count": len(conflicts), "conflict_titles": [item["title"] for item in conflicts[:20]]}}
                atomic_json_write(self.state_path, state)
            self.history.record("google_calendar_sync_applied", actor, "google-calendar-sync", actor, result)
        else:
            self.history.record("google_calendar_sync_previewed", actor, "google-calendar-sync", actor, result)
        return result

    def disable(self, actor: str) -> None:
        with exclusive_file_lock(self.state_path.parent / f".google-calendar-sync-{actor}.lock"):
            state = self._state(); state.pop(actor, None); atomic_json_write(self.state_path, state)
            self.history.record("google_calendar_sync_state_cleared", actor, "google-calendar-sync", actor, {"at": utc_now()})

    def _state(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
