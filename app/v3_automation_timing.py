"""Action timing uses stable UTC instants, not the time of a dispatch retry."""
from datetime import date, datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PERIOD_LIMIT = 7 * 24 * 3600
FIELDS = {"delay_seconds", "within_seconds", "end_of_day", "slot_start", "slot_end", "timezone"}


def _second(values, name, *, minimum=0):
    value = values.get(name, 0)
    if type(value) is not int or not minimum <= value <= PERIOD_LIMIT:
        raise ValueError(f"invalid {name}")
    return value


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("slot must use HH:MM")
    return time.fromisoformat(value)


def _zone(value):
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("invalid action timezone")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("invalid action timezone") from exc


def validate_timing(values, default_zone="UTC"):
    if type(values) is not dict or set(values) - FIELDS:
        raise ValueError("invalid automation action timing")
    delay = _second(values, "delay_seconds")
    within = _second(values, "within_seconds", minimum=1) if "within_seconds" in values else None
    end_of_day = values.get("end_of_day", False)
    if type(end_of_day) is not bool:
        raise ValueError("end_of_day must be a boolean")
    has_slot = "slot_start" in values or "slot_end" in values
    if sum((within is not None, end_of_day, has_slot)) > 1:
        raise ValueError("choose an elapsed deadline, end of day, or a time slot")
    if within is not None and delay >= within:
        raise ValueError("action delay must be shorter than its deadline")
    slot = None
    if has_slot:
        slot = (_clock(values.get("slot_start")), _clock(values.get("slot_end")))
        if slot[0] >= slot[1]:
            raise ValueError("time slot must finish later on the same day")
    zone = _zone(values.get("timezone", default_zone))
    return delay, within, end_of_day, slot, zone


def utc_instant(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("action timestamp must include a timezone") from exc
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("action timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _local_bound(day: date, wall: time, zone: ZoneInfo, *, end=False):
    naive = datetime.combine(day, wall)
    candidates = {
        candidate.astimezone(timezone.utc)
        for candidate in (naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1))
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive
    }
    if not candidates:
        raise ValueError("time slot does not exist on this date in the workflow timezone")
    # A repeated hour must not allow execution earlier or later than requested.
    return min(candidates) if end else max(candidates)


def action_window(values, occurred_at, default_zone="UTC"):
    """Return immutable not-before and optional expiry for persistence/transmission."""
    delay, within, end_of_day, slot, zone = validate_timing(values, default_zone)
    event = utc_instant(occurred_at)
    ready = event + timedelta(seconds=delay)
    expiry = None
    if within is not None:
        expiry = event + timedelta(seconds=within)
    elif end_of_day:
        expiry = _local_bound(event.astimezone(zone).date() + timedelta(days=1), time.min, zone, end=True)
    elif slot is not None:
        day = event.astimezone(zone).date()
        ready = max(ready, _local_bound(day, slot[0], zone))
        expiry = _local_bound(day, slot[1], zone, end=True)
    return ready, expiry


def window_status(ready, expiry, now, *, uncertainty_seconds=0):
    """Safe evaluation for a caller or receiver with a known upper clock error."""
    if type(uncertainty_seconds) is not int or not 0 <= uncertainty_seconds <= 60:
        raise ValueError("clock uncertainty is out of bounds")
    checked = utc_instant(now)
    uncertainty = timedelta(seconds=uncertainty_seconds)
    if expiry is not None and (checked + uncertainty >= utc_instant(expiry)
                               or utc_instant(ready) >= utc_instant(expiry)):
        return "window_missed"
    if checked - uncertainty < utc_instant(ready):
        return "waiting_for_window"
    return "ready"


def timing_envelope(values, occurred_at, default_zone="UTC"):
    """Resolve origin wall time once, before a delivery attempt or a retry."""
    _, _, _, _, zone = validate_timing(values, default_zone)
    event = utc_instant(occurred_at)
    ready, expiry = action_window(values, event, default_zone)
    return {
        "requested_at": event.isoformat(),
        "origin_timezone": zone.key,
        "not_before": ready.isoformat(),
        "expires_at": expiry.isoformat() if expiry is not None else None,
    }


def received_window_status(envelope, now, *, uncertainty_seconds=0):
    """Use only after authenticating the sender and authorizing this typed action."""
    if type(envelope) is not dict or set(envelope) != {"requested_at", "origin_timezone", "not_before", "expires_at"}:
        raise ValueError("invalid action timing envelope")
    _zone(envelope["origin_timezone"])
    origin = utc_instant(envelope["requested_at"])
    ready = utc_instant(envelope["not_before"])
    expiry = utc_instant(envelope["expires_at"]) if envelope["expires_at"] is not None else None
    if ready < origin or ready - origin > timedelta(seconds=PERIOD_LIMIT):
        raise ValueError("invalid action not-before instant")
    if expiry is not None and expiry - origin > timedelta(seconds=PERIOD_LIMIT):
        raise ValueError("invalid action expiry")
    return window_status(ready, expiry, now, uncertainty_seconds=uncertainty_seconds)
