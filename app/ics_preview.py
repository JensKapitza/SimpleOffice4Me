"""Read-only, bounded inspection of iCalendar files before import."""

from __future__ import annotations

from datetime import datetime
from typing import Any


MAX_PREVIEW_BYTES = 1024 * 1024
MAX_PREVIEW_EVENTS = 200


def _unescape_text(value: str) -> str:
    result: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            result.append("\n" if character in {"n", "N"} else character)
            escaped = False
        elif character == "\\":
            escaped = True
        else:
            result.append(character)
    if escaped:
        result.append("\\")
    return "".join(result)


def _format_datetime(value: str, parameters: dict[str, str]) -> tuple[str, list[str], bool]:
    value = value.strip()
    warnings: list[str] = []
    timezone_id = parameters.get("TZID", "").strip()
    try:
        if parameters.get("VALUE", "").upper() == "DATE" or (len(value) == 8 and "T" not in value):
            parsed = datetime.strptime(value, "%Y%m%d")
            return parsed.strftime("%Y-%m-%d"), warnings, True

        is_utc = value.endswith("Z")
        normalized = value[:-1] if is_utc else value
        parsed = None
        for pattern in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                parsed = datetime.strptime(normalized, pattern)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError

        suffix = " UTC" if is_utc else (f" ({timezone_id})" if timezone_id else "")
        if timezone_id:
            warnings.append("timezone_not_converted")
        elif not is_utc:
            warnings.append("floating_time")
        return parsed.strftime("%Y-%m-%d %H:%M") + suffix, warnings, True
    except ValueError:
        return value or "—", ["invalid_datetime"], False


def _property(line: str) -> tuple[str, dict[str, str], str] | None:
    name_and_parameters, separator, value = line.partition(":")
    if not separator:
        return None
    parts = name_and_parameters.split(";")
    name = parts[0].upper()
    parameters: dict[str, str] = {}
    for raw_parameter in parts[1:]:
        key, equals, parameter_value = raw_parameter.partition("=")
        if equals:
            parameters[key.upper()] = parameter_value.strip('"')
    return name, parameters, value


def preview_ics(content: str) -> dict[str, Any]:
    """Return a bounded VEVENT summary without persisting any calendar data."""
    if not content.strip():
        raise ValueError("empty iCalendar file")
    if "\x00" in content:
        raise ValueError("iCalendar file contains NUL bytes")
    if len(content.encode("utf-8")) > MAX_PREVIEW_BYTES:
        raise ValueError(f"iCalendar preview is limited to {MAX_PREVIEW_BYTES // 1024} KiB")

    unfolded: list[str] = []
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)

    raw_events: list[dict[str, tuple[dict[str, str], str]]] = []
    current: dict[str, tuple[dict[str, str], str]] | None = None
    for line in unfolded:
        parsed = _property(line)
        if parsed is None:
            continue
        name, parameters, value = parsed
        if name == "BEGIN" and value.upper() == "VEVENT":
            current = {}
        elif name == "END" and value.upper() == "VEVENT" and current is not None:
            raw_events.append(current)
            current = None
            if len(raw_events) > MAX_PREVIEW_EVENTS:
                raise ValueError(f"iCalendar preview is limited to {MAX_PREVIEW_EVENTS} events")
        elif current is not None:
            current.setdefault(name, (parameters, value))

    if not raw_events:
        raise ValueError("no VEVENT records found")

    events: list[dict[str, Any]] = []
    for index, raw_event in enumerate(raw_events, start=1):
        warnings: list[str] = []
        summary = _unescape_text(raw_event.get("SUMMARY", ({}, ""))[1]).strip()
        uid = _unescape_text(raw_event.get("UID", ({}, ""))[1]).strip()
        start_parameters, start_value = raw_event.get("DTSTART", ({}, ""))
        end_parameters, end_value = raw_event.get("DTEND", ({}, ""))
        start, start_warnings, start_valid = _format_datetime(start_value, start_parameters)
        end = "—"
        end_valid = True
        if end_value.strip():
            end, end_warnings, end_valid = _format_datetime(end_value, end_parameters)
            warnings.extend(end_warnings)
        warnings.extend(start_warnings)
        if not summary:
            warnings.append("missing_summary")
        if not start_value.strip():
            warnings.append("missing_start")
            start_valid = False
        if "RRULE" in raw_event:
            warnings.append("recurrence_not_expanded")
        if "RECURRENCE-ID" in raw_event:
            warnings.append("recurrence_exception_not_applied")
        status = raw_event.get("STATUS", ({}, ""))[1].strip().upper()
        if status:
            warnings.append("status_review_required")
        if not uid:
            warnings.append("missing_uid")

        events.append(
            {
                "index": index,
                "uid": uid or "—",
                "title": summary or "(ohne Titel)",
                "start": start,
                "end": end,
                "status": status or "—",
                "usable": bool(summary and start_value.strip() and start_valid and end_valid),
                "warnings": list(dict.fromkeys(warnings)),
            }
        )

    usable = sum(1 for event in events if event["usable"])
    return {
        "total": len(events),
        "usable": usable,
        "invalid": len(events) - usable,
        "warning_count": sum(len(event["warnings"]) for event in events),
        "events": events,
    }
