"""Auditable, file-based application defaults. Retention policies are intentionally excluded."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .revision_history import RevisionHistory


DEFAULT_SETTINGS: dict[str, Any] = {
    "interface": {"default_language": "de", "timezone": "Europe/Berlin"},
    "documents": {"default_state": "new", "default_tags": [], "upload_to_archive": False},
    "calendar": {"default_visibility": "private", "default_public_notice": "Belegt", "default_duration_minutes": 60},
    "sharing": {"default_expiry_days": 7},
}

TRANSLATIONS = {
    "de": {"overview": "Übersicht", "documents": "Dokumente", "inbox": "Inbox", "calendar": "Kalender", "contacts": "Kontakte", "images": "Bilder", "settings": "Einstellungen", "logout": "Abmelden", "archives": "Archive", "notes": "Notizen", "logbook": "Logbuch"},
    "en": {"overview": "Overview", "documents": "Documents", "inbox": "Inbox", "calendar": "Calendar", "contacts": "Contacts", "images": "Images", "settings": "Settings", "logout": "Sign out", "archives": "Archives", "notes": "Notes", "logbook": "Activity log"},
}


def translate(language: str, key: str) -> str:
    return TRANSLATIONS.get(language, TRANSLATIONS["de"]).get(key, key)


class SettingsStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / CONTROL_DIR / "settings.json"
        self.history = RevisionHistory(self.root)

    def settings(self) -> dict[str, Any]:
        result = copy.deepcopy(DEFAULT_SETTINGS)
        try:
            import json
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        for section, values in stored.items() if isinstance(stored, dict) else []:
            if section in result and isinstance(values, dict):
                result[section].update(values)
        return result

    def save(self, settings: dict[str, Any], actor: str) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("a named user is required")
        normalized = self._validate(settings)
        atomic_json_write(self.path, normalized)
        self.history.record("settings_updated", actor, "settings", "application-defaults", {"updated_at": utc_now(), **normalized})
        return normalized

    def _validate(self, settings: dict[str, Any]) -> dict[str, Any]:
        interface = settings.get("interface", {})
        documents = settings.get("documents", {})
        calendar = settings.get("calendar", {})
        sharing = settings.get("sharing", {})
        language = str(interface.get("default_language", "de"))
        if language not in TRANSLATIONS:
            raise ValueError("unsupported default language")
        timezone = str(interface.get("timezone", "Europe/Berlin")).strip()
        if not timezone or len(timezone) > 100:
            raise ValueError("invalid timezone")
        visibility = str(calendar.get("default_visibility", "private"))
        if visibility not in {"private", "family", "external"}:
            raise ValueError("invalid calendar visibility")
        duration = int(calendar.get("default_duration_minutes", 60))
        expiry = int(sharing.get("default_expiry_days", 7))
        if not 15 <= duration <= 480 or not 1 <= expiry <= 365:
            raise ValueError("calendar duration or share expiry outside allowed range")
        tags = [str(tag).strip() for tag in documents.get("default_tags", []) if str(tag).strip()]
        return {"interface": {"default_language": language, "timezone": timezone}, "documents": {"default_state": str(documents.get("default_state", "new")).strip() or "new", "default_tags": sorted(set(tags), key=str.casefold), "upload_to_archive": documents.get("upload_to_archive") is True}, "calendar": {"default_visibility": visibility, "default_public_notice": str(calendar.get("default_public_notice", "Belegt")).strip(), "default_duration_minutes": duration}, "sharing": {"default_expiry_days": expiry}}
