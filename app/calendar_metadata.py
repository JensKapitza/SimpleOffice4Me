"""Validated RFC 5545/7986 metadata shared by web, ICS and CalDAV."""

from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlsplit


ICAL_STATUSES = {"tentative", "confirmed", "cancelled"}
TRANSPARENCIES = {"opaque", "transparent"}
CLASSIFICATIONS = {"public", "private", "confidential"}
SAFE_URI_SCHEMES = {"https", "http", "mailto", "tel"}
CONFERENCE_FEATURES = {"audio", "chat", "feed", "moderator", "phone", "screen", "video"}


def _text(value: Any, limit: int) -> str:
    result = str(value or "").strip()
    if len(result) > limit:
        raise ValueError(f"calendar metadata value exceeds {limit} characters")
    return result


def safe_uri(value: Any, *, required: bool = False) -> str:
    uri = _text(value, 2048)
    if not uri:
        if required:
            raise ValueError("calendar URI is required")
        return ""
    parsed = urlsplit(uri)
    if parsed.scheme.lower() not in SAFE_URI_SCHEMES:
        raise ValueError("calendar URI must use https, http, mailto or tel")
    if parsed.username or parsed.password:
        raise ValueError("calendar URI must not contain credentials")
    if parsed.scheme.lower() in {"https", "http"} and not parsed.hostname:
        raise ValueError("calendar web URI requires a host")
    return uri


def normalize_conferences(values: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in values or []:
        uri = safe_uri(value.get("uri"), required=True)
        label = _text(value.get("label"), 200)
        features = sorted({
            str(feature).strip().lower()
            for feature in value.get("features", [])
            if str(feature).strip()
        })
        if any(feature not in CONFERENCE_FEATURES for feature in features):
            raise ValueError("unsupported conference feature")
        result.append({"uri": uri, "label": label, "features": features})
    if len(result) > 8:
        raise ValueError("at most 8 conference links are allowed")
    if len({item["uri"] for item in result}) != len(result):
        raise ValueError("conference links must be unique")
    return result


def normalize_metadata(values: dict[str, Any] | None, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    values = values or {}
    existing = existing or {}
    status = str(values.get("ical_status", existing.get("ical_status", "confirmed"))).strip().lower()
    transparency = str(values.get("transparency", existing.get("transparency", "opaque"))).strip().lower()
    classification = str(values.get("classification", existing.get("classification", "private"))).strip().lower()
    if status not in ICAL_STATUSES:
        raise ValueError("invalid iCalendar event status")
    if transparency not in TRANSPARENCIES:
        raise ValueError("invalid iCalendar time transparency")
    if classification not in CLASSIFICATIONS:
        raise ValueError("invalid iCalendar classification")
    try:
        priority = int(values.get("priority", existing.get("priority", 0)) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("calendar priority must be an integer") from exc
    if not 0 <= priority <= 9:
        raise ValueError("calendar priority must be between 0 and 9")
    resources = [
        _text(value, 200)
        for value in values.get("resources", existing.get("resources", []))
        if str(value or "").strip()
    ]
    if len(resources) > 32 or len(set(resources)) != len(resources):
        raise ValueError("calendar resources must be unique and limited to 32")
    return {
        "ical_status": status,
        "transparency": transparency,
        "classification": classification,
        "priority": priority,
        "location": _text(values.get("location", existing.get("location", "")), 500),
        "event_url": safe_uri(values.get("event_url", existing.get("event_url", ""))),
        "resources": resources,
        "conferences": normalize_conferences(values.get("conferences", existing.get("conferences", []))),
    }


def metadata_lines(event: dict[str, Any], escape) -> list[str]:
    """Return canonical iCalendar content lines for normalized event metadata."""
    metadata = normalize_metadata(event, event)
    lines = [
        f"STATUS:{metadata['ical_status'].upper()}",
        f"TRANSP:{metadata['transparency'].upper()}",
        f"CLASS:{metadata['classification'].upper()}",
        f"PRIORITY:{metadata['priority']}",
    ]
    if metadata["location"]:
        lines.append(f"LOCATION:{escape(metadata['location'])}")
    if metadata["event_url"]:
        lines.append(f"URL:{metadata['event_url']}")
    if metadata["resources"]:
        lines.append("RESOURCES:" + ",".join(escape(value) for value in metadata["resources"]))
    for conference in metadata["conferences"]:
        params = []
        if conference["features"]:
            params.append("FEATURE=" + ",".join(value.upper() for value in conference["features"]))
        if conference["label"]:
            params.append('LABEL="' + escape(conference["label"]) + '"')
        lines.append("CONFERENCE" + (";" + ";".join(params) if params else "") + ":" + conference["uri"])
    return lines
