"""Calendar-to-audio bridge for scheduled announcements."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .audio_output_store import AudioOutputStore


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def announcement_text(event: dict, minutes_before: int = 5) -> str:
    title = " ".join(str(event.get("title") or "Termin").split()).strip()[:300]
    if minutes_before <= 0:
        return f"Der Termin {title} beginnt jetzt."
    if minutes_before == 1:
        return f"Der Termin {title} beginnt in einer Minute."
    return f"Der Termin {title} beginnt in {minutes_before} Minuten."


def event_audio_settings(event: dict) -> dict | None:
    settings = event.get("audio_announcement") or event.get("audio_event")
    if not isinstance(settings, dict) or not settings.get("enabled", False):
        return None
    targets = settings.get("targets") or []
    if isinstance(targets, str):
        targets = [item.strip() for item in targets.split(",") if item.strip()]
    return {
        "targets": list(targets),
        "minutes_before": max(0, min(int(settings.get("minutes_before", 5)), 1440)),
        "priority": max(0, min(int(settings.get("priority", 60)), 100)),
        "voice": str(settings.get("voice") or "de_DE")[:80],
        "text": str(settings.get("text") or "").strip()[:2000],
    }


def queue_due_calendar_events(store: AudioOutputStore, events: list[dict], *, now: datetime | None = None, window_seconds: int = 60) -> list[dict]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    queued: list[dict] = []
    for event in events:
        settings = event_audio_settings(event)
        starts = _parse_time(event.get("start"))
        if not settings or not starts or not settings["targets"]:
            continue
        trigger = starts.timestamp() - settings["minutes_before"] * 60
        delta = current.timestamp() - trigger
        if not 0 <= delta < max(1, int(window_seconds)):
            continue
        event_id = str(event.get("event_id") or event.get("id") or "")[:200]
        text = settings["text"] or announcement_text(event, settings["minutes_before"])
        queued.append(store.queue_tts(text, settings["targets"], priority=settings["priority"], voice=settings["voice"], source="calendar", source_ref=event_id))
    return queued
