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

# Older templates contain literal UI text.  The client-side catalogue keeps
# those pages language-aware while they are gradually moved to named keys.
# Entries are deliberately phrases (not only single words), so user-created
# contact names, document names and notes are never translated.
UI_LITERAL_TRANSLATIONS = {
    "en": {
        "Termine": "Appointments", "To-Do": "To-do", "Bearbeiten": "Edit", "Öffnen": "Open", "Speichern": "Save", "Suchen": "Search", "Filtern": "Filter", "Hinzufügen": "Add", "Entfernen": "Remove", "Löschen": "Delete", "Abbrechen": "Cancel", "Zurück": "Back",
        "Kontakte": "Contacts", "Kontaktliste": "Contact list", "Kontakt anlegen": "Create contact", "Kontaktdaten bearbeiten": "Edit contact details", "Änderungen speichern": "Save changes", "Änderungsverlauf": "Change history", "Gemeinsame Verwaltung": "Shared management", "Freigaben speichern": "Save sharing", "Eigentümer:": "Owner:", "Adressen": "Addresses", "Adresse": "Address", "Anzeigename": "Display name", "Vorname": "First name", "Nachname": "Last name", "Firma": "Company", "Geburtstag": "Birthday", "Telefon": "Phone", "Bearbeitet von": "Edited by", "Alle Bearbeiter": "All editors", "Geändertes Feld": "Changed field", "Alle geänderten Felder": "All changed fields", "Keine Änderungen entsprechen dem Filter.": "No changes match the filter.", "vCard exportieren": "Export vCard", "vCard importieren": "Import vCard", "Kontakt gespeichert.": "Contact saved.",
        "Kalender": "Calendar", "Termin anlegen": "Create appointment", "Titel": "Title", "Grund": "Reason", "Beginn": "Start", "Ende": "End", "Sichtbarkeit": "Visibility", "Privat": "Private", "Familie": "Family", "Extern": "External", "Externer Hinweis": "External notice", "Private Tags": "Private tags", "Externe Buchung": "External booking", "Buchung erlauben": "Allow booking", "Buchungszeiten speichern": "Save booking hours", "öffentliche Seite": "public page", "Offene Buchungsanfragen": "Pending booking requests", "Bestätigen und ICS senden": "Confirm and send ICS", "ICS herunterladen": "Download ICS", "E-Mail im Client vorbereiten": "Prepare email in client", "Versand ausstehend – ICS manuell weitergeben": "Delivery pending – forward ICS manually", "Einladung:": "Invitation:", "versendet": "sent", "Termine": "Appointments", "Keine freien Termine an diesem Tag.": "No free appointments on this day.", "Termin anfragen": "Request an appointment", "Anfrage senden": "Send request", "Freie Zeiten anzeigen": "Show available times", "Dein Name": "Your name", "E-Mail für ICS-Antwort": "Email for ICS reply", "Freier Zeitpunkt": "Available time",
        "Dokumente": "Documents", "Dateien hinzufügen": "Add files", "Vollständig importieren": "Import completely", "Direkt ins feste Archiv importieren (nach SHA-256 sortiert)": "Import directly into the permanent archive (sorted by SHA-256)", "Notizen": "Notes", "Notiz speichern": "Save note", "Tags": "Tags", "Zustand": "State", "Versionen": "Versions", "Logbuch": "Activity log", "Datei freigeben": "Share file", "Passwort": "Password", "HTTPS-Link erzeugen": "Create HTTPS link", "Inbox": "Inbox", "Keine offenen Aufgaben.": "No open tasks.", "Neue Aufgabe": "New task", "Bilder": "Images", "Diashow": "Slideshow", "Alle Zeiten": "All time", "Diese Woche": "This week", "Dieser Monat": "This month", "Dieses Jahr": "This year", "Tags speichern": "Save tags", "Verwalten / freigeben": "Manage / share",
        "Einstellungen und Standardwerte": "Settings and defaults", "Oberfläche": "Interface", "Standardsprache für neue Sitzungen": "Default language for new sessions", "Zeitzone": "Time zone", "Dokumente und Import": "Documents and import", "Kalender": "Calendar", "Freigaben": "Sharing", "Standardwerte speichern": "Save defaults", "Archive": "Archives", "Externes Archiv registrieren": "Register external archive", "Verbindung prüfen": "Check connection", "Angeschlossene Archive suchen": "Find connected archives", "SSH-Systeme": "SSH systems", "SSH-Quelle hinzufügen": "Add SSH source", "Registrierte Quellen": "Registered sources", "Jetzt importieren": "Import now", "Quelle entfernen": "Remove source",
        "Geschützte Freigabe": "Protected share", "Gib das Passwort ein, um die freigegebene Datei oder Notiz zu öffnen.": "Enter the password to open the shared file or note.", "Systemübersicht": "System overview", "Zeit": "Time", "Dokumentenspeicher": "Document storage", "Verbundene Speicher": "Connected storage", "Leer.": "Empty.", "Noch keine Ereignisse.": "No events yet.", "Noch keine Notizen vorhanden.": "No notes yet.", "Keine veröffentlichten Termine.": "No published appointments.", "Familienkalender": "Family calendar", "Öffentliche Kalenderhinweise": "Public calendar notices",
        "Log In": "Sign in", "Log Out": "Sign out", "Register": "Register", "Username": "Username", "Password": "Password",
    },
    "de": {
        "Log In": "Anmelden", "Sign in": "Anmelden", "Log Out": "Abmelden", "Sign out": "Abmelden", "Register": "Registrieren", "Username": "Benutzername", "Password": "Passwort", "First": "Vorname", "Last": "Nachname",
    },
}


def translate(language: str, key: str) -> str:
    return TRANSLATIONS.get(language, TRANSLATIONS["de"]).get(key, key)


def ui_literal_translations(language: str) -> dict[str, str]:
    return UI_LITERAL_TRANSLATIONS.get(language, {})


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
