"""Auditable, file-based application defaults. Retention policies are intentionally excluded."""

from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .integration_settings import (
    DEFAULT_CLAMAV_TIMEOUT_SECONDS,
    DEFAULT_PERSONNEL_TIME_AUTOSYNC_ENABLED,
    DEFAULT_PERSONNEL_TIME_AUTOSYNC_SECONDS,
    DEFAULT_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
    DEFAULT_OSM_DOWNLOAD_RETRIES,
    DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_OSM_FILTER_TIMEOUT_SECONDS,
    DEFAULT_OSM_MAX_DOWNLOAD_GIB,
    DEFAULT_OSM_READ_TIMEOUT_SECONDS,
    MAX_CLAMAV_TIMEOUT_SECONDS as MAX_CLAMAV_TIMEOUT_SECONDS,
    MAX_PERSONNEL_TIME_AUTOSYNC_SECONDS as MAX_PERSONNEL_TIME_AUTOSYNC_SECONDS,
    MAX_PRINTERSHARE_PURGE_INTERVAL_SECONDS as MAX_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
    MAX_OSM_DOWNLOAD_RETRIES as MAX_OSM_DOWNLOAD_RETRIES,
    MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS as MAX_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    MAX_OSM_FILTER_TIMEOUT_SECONDS as MAX_OSM_FILTER_TIMEOUT_SECONDS,
    MAX_OSM_MAX_DOWNLOAD_GIB as MAX_OSM_MAX_DOWNLOAD_GIB,
    MAX_OSM_READ_TIMEOUT_SECONDS as MAX_OSM_READ_TIMEOUT_SECONDS,
    MIN_CLAMAV_TIMEOUT_SECONDS as MIN_CLAMAV_TIMEOUT_SECONDS,
    MIN_PERSONNEL_TIME_AUTOSYNC_SECONDS as MIN_PERSONNEL_TIME_AUTOSYNC_SECONDS,
    MIN_PRINTERSHARE_PURGE_INTERVAL_SECONDS as MIN_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
    MIN_OSM_DOWNLOAD_RETRIES as MIN_OSM_DOWNLOAD_RETRIES,
    MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS as MIN_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
    MIN_OSM_FILTER_TIMEOUT_SECONDS as MIN_OSM_FILTER_TIMEOUT_SECONDS,
    MIN_OSM_MAX_DOWNLOAD_GIB as MIN_OSM_MAX_DOWNLOAD_GIB,
    MIN_OSM_READ_TIMEOUT_SECONDS as MIN_OSM_READ_TIMEOUT_SECONDS,
    legacy_clamav_timeout,
    legacy_osm_download_retries,
    legacy_osm_export_idle_timeout,
    legacy_osm_filter_timeout,
    legacy_osm_max_download_gib,
    legacy_osm_read_timeout,
    legacy_personnel_time_autosync_enabled,
    legacy_personnel_time_autosync_seconds,
    legacy_printershare_purge_interval_seconds,
    normalize_integration_settings,
    validate_integration_settings,
)
from .revision_history import RevisionHistory
from .settings_limits import (
    DEFAULT_RSYNC_MAX_BYTES,
    DEFAULT_RSYNC_MAX_FILES,
    DEFAULT_WEBDAV_QUOTA_MIB,
    MAX_RSYNC_MAX_BYTES,
    MAX_RSYNC_MAX_FILES,
    MAX_WEBDAV_QUOTA_MIB,
    MIN_RSYNC_MAX_BYTES,
    MIN_RSYNC_MAX_FILES,
)


_SETTINGS_LOCK = threading.RLock()
_LOGGER = logging.getLogger(__name__)
DEFAULT_VIDEO_PREVIEW_FRAMES = 10
MIN_VIDEO_PREVIEW_FRAMES = 1
MAX_VIDEO_PREVIEW_FRAMES = 30
LEGACY_VIDEO_PREVIEW_SETTINGS_FILE = "video-preview-settings.json"
DEFAULT_ARCHIVE_OCR_ENABLED = False
DEFAULT_PREVIEW_ENABLED = True
DEFAULT_PREVIEW_TIMEOUT_SECONDS = 45
MIN_PREVIEW_TIMEOUT_SECONDS = 5
MAX_PREVIEW_TIMEOUT_SECONDS = 300
DEFAULT_PREVIEW_MAX_BYTES = 256 * 1024 * 1024
MIN_PREVIEW_MAX_BYTES = 1024
MAX_PREVIEW_MAX_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_PREVIEW_MAX_PIXELS = 80_000_000
MIN_PREVIEW_MAX_PIXELS = 1_000_000
MAX_PREVIEW_MAX_PIXELS = 250_000_000
DEFAULT_RSYNC_TIMEOUT_SECONDS = 3600
MIN_RSYNC_TIMEOUT_SECONDS = 30
MAX_RSYNC_TIMEOUT_SECONDS = 86400


@contextmanager
def _settings_write_lock(path: Path):
    """Serialize read/merge/write across threads and POSIX worker processes."""
    with _SETTINGS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with (path.parent / "settings.write.lock").open("a+b") as handle:
            if os.name == "posix":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "posix":
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _bounded_setting(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, parsed))


DEFAULT_SETTINGS: dict[str, Any] = {
    "interface": {"default_language": "de", "timezone": "Europe/Berlin"},
    "documents": {"default_state": "new", "default_tags": [], "upload_to_archive": False},
    "calendar": {"default_visibility": "private", "default_public_notice": "Belegt", "default_duration_minutes": 60},
    "sharing": {"default_expiry_days": 7},
    "s3": {"enabled": False},
    "network": {
        "webdav_quota_mib": DEFAULT_WEBDAV_QUOTA_MIB,
        "rsync_timeout_seconds": DEFAULT_RSYNC_TIMEOUT_SECONDS,
        "rsync_max_bytes": DEFAULT_RSYNC_MAX_BYTES,
        "rsync_max_files": DEFAULT_RSYNC_MAX_FILES,
    },
    "media": {
        "archive_ocr_enabled": DEFAULT_ARCHIVE_OCR_ENABLED,
        "video_preview_frames": DEFAULT_VIDEO_PREVIEW_FRAMES,
        "preview_enabled": DEFAULT_PREVIEW_ENABLED,
        "preview_timeout_seconds": DEFAULT_PREVIEW_TIMEOUT_SECONDS,
        "preview_max_bytes": DEFAULT_PREVIEW_MAX_BYTES,
        "preview_max_pixels": DEFAULT_PREVIEW_MAX_PIXELS,
    },
    "integrations": {
        "osm_download_read_timeout_seconds": DEFAULT_OSM_READ_TIMEOUT_SECONDS,
        "osm_filter_timeout_seconds": DEFAULT_OSM_FILTER_TIMEOUT_SECONDS,
        "osm_export_idle_timeout_seconds": DEFAULT_OSM_EXPORT_IDLE_TIMEOUT_SECONDS,
        "osm_max_download_gib": DEFAULT_OSM_MAX_DOWNLOAD_GIB,
        "osm_download_retries": DEFAULT_OSM_DOWNLOAD_RETRIES,
        "clamav_timeout_seconds": DEFAULT_CLAMAV_TIMEOUT_SECONDS,
        "personnel_time_autosync_enabled": DEFAULT_PERSONNEL_TIME_AUTOSYNC_ENABLED,
        "personnel_time_autosync_seconds": DEFAULT_PERSONNEL_TIME_AUTOSYNC_SECONDS,
        "printershare_purge_interval_seconds": DEFAULT_PRINTERSHARE_PURGE_INTERVAL_SECONDS,
    },
}

TRANSLATIONS = {
    "de": {
        "overview": "Übersicht", "documents": "Dokumente", "inbox": "Inbox", "calendar": "Kalender", "contacts": "Kontakte", "images": "Bilder", "settings": "Einstellungen", "logout": "Abmelden", "archives": "Archive", "notes": "Notizen", "logbook": "Logbuch",
        "crm.status.active": "Aktiv", "crm.status.inactive": "Inaktiv", "crm.status.prospect": "Interessent", "crm.status.blocked": "Gesperrt", "crm.status.former": "Ehemalig",
        "crm.role.customer": "Kunde", "crm.role.supplier": "Lieferant", "crm.role.contact_person": "Ansprechpartner", "crm.role.private": "Privat", "crm.role.other": "Sonstige",
        "crm.activity.email": "E-Mail", "crm.activity.phone": "Telefonat", "crm.activity.meeting": "Termin", "crm.activity.letter": "Brief", "crm.activity.note": "Notiz",
        "crm.direction.outgoing": "Ausgehend", "crm.direction.incoming": "Eingehend", "crm.direction.internal": "Intern",
        "crm.entries": "Einträge", "crm.last": "zuletzt", "crm.contact_field": "Kontaktfeld", "crm.changed": "geändert",
        "crm.activity.saved": "CRM-Aktivität gespeichert.", "crm.activity.error.type": "Unbekannte CRM-Aktivitätsart.", "crm.activity.error.direction": "Unbekannte Richtung der CRM-Aktivität.", "crm.activity.error.content_required": "Betreff oder Notiz ist erforderlich.", "crm.activity.error.default": "CRM-Aktivität konnte nicht gespeichert werden.",
        "crm.csv.name": "Name", "crm.csv.company": "Firma", "crm.csv.email": "E-Mail", "crm.csv.phone": "Telefon", "crm.csv.status": "Status", "crm.csv.roles": "Rollen", "crm.csv.customer_number": "Kundennummer", "crm.csv.supplier_number": "Lieferantennummer", "crm.csv.latest_activity": "Letzte Aktivität", "crm.csv.activities": "Aktivitäten",
        "invoice.nav": "Rechnungen", "invoice.overview.title": "Rechnungsübersicht", "invoice.overview.subtitle": "Rechnungen suchen, Fälligkeiten prüfen und Zahlungseingänge erfassen.", "invoice.settings": "Vorlagen / Rechnungssteller", "invoice.stats.total": "Rechnungen", "invoice.status": "Status", "invoice.status.all": "Alle Status", "invoice.status.open": "Offen", "invoice.status.partial": "Teilbezahlt", "invoice.status.overdue": "Überfällig", "invoice.status.paid": "Bezahlt", "invoice.search": "Suche", "invoice.search.placeholder": "Rechnungsnummer oder Kunde", "invoice.filter": "Filtern", "invoice.reset": "Zurücksetzen", "invoice.number": "Rechnungsnummer", "invoice.customer": "Kunde", "invoice.issue_date": "Rechnungsdatum", "invoice.service_date": "Leistungsdatum", "invoice.due_date": "Fällig", "invoice.total": "Gesamt", "invoice.outstanding": "Offener Betrag", "invoice.open": "Öffnen", "invoice.none": "Keine passenden Rechnungen.", "invoice.title": "Rechnung", "invoice.immutable_notice": "Rechnungsdaten und Positionen sind unveränderliche Snapshots. Zahlungseingänge werden separat protokolliert.", "invoice.pdf": "PDF-Dokument", "invoice.positions": "Positionen", "invoice.position": "Pos.", "invoice.object": "Objekt", "invoice.description": "Beschreibung", "invoice.quantity": "Menge", "invoice.net": "Netto", "invoice.vat": "MwSt.", "invoice.free_position": "frei", "invoice.data": "Rechnungsdaten", "invoice.payment_terms": "Zahlungsbedingungen", "invoice.net_total": "Nettosumme", "invoice.tax_total": "Umsatzsteuer", "invoice.payment_history": "Zahlungshistorie", "invoice.no_payments": "Noch keine Zahlung erfasst.", "invoice.payment_amount": "Betrag", "invoice.payment_date": "Zahlungsdatum", "invoice.payment_reference": "Referenz", "invoice.record_payment": "Zahlung erfassen", "invoice.payment.saved": "Zahlung zur Rechnung erfasst.", "invoice.payment.error.paid": "Die Rechnung ist bereits vollständig bezahlt.", "invoice.payment.error.amount": "Der Zahlungsbetrag muss positiv sein und darf den offenen Betrag nicht überschreiten.", "invoice.payment.error.date": "Das Zahlungsdatum ist ungültig.", "invoice.payment.error.default": "Die Zahlung konnte nicht erfasst werden.", "invoice.profile": "Profil", "invoice.recipient": "Empfänger",
        "billing.title": "Abrechnung und Guthaben", "billing.customer": "Kunde", "billing.new_invoice": "Neue Rechnung", "billing.download_all": "Alle Rechnungen als ZIP", "billing.invoices": "Rechnungen des Kunden", "billing.search_placeholder": "Rechnungsnummer, Datum oder Status", "billing.download": "Herunterladen", "billing.credit_history": "Guthabenhistorie", "billing.no_credit_entries": "Noch keine Guthabenbuchung.", "billing.credit": "Kundenguthaben", "billing.credit_explanation": "Guthaben wird als Zahlung verrechnet. Netto, Umsatzsteuer und Brutto der Rechnung bleiben unverändert.", "billing.amount": "Betrag", "billing.currency": "Währung", "billing.credit_kind": "Buchungsart", "billing.credit_kind.topup": "Aufladung", "billing.credit_kind.referral": "Prämie für Kundenwerbung", "billing.credit_kind.manual": "Manuelle Korrektur", "billing.credit_kind.credit_note": "Gutschrift", "billing.credit_kind.refund": "Auszahlung", "billing.credit_kind.invoice_application": "Rechnungsverrechnung", "billing.tax_treatment": "Steuerliche Einordnung", "billing.tax_treatment.outside_scope": "Nicht leistungsbezogene Einzahlung", "billing.tax_treatment.multipurpose_voucher": "Mehrzweck-Gutschein", "billing.tax_treatment.taxable_advance": "Steuerpflichtige Anzahlung", "billing.tax_treatment.manual_review": "Steuerlich zu prüfen", "billing.select": "Bitte auswählen", "billing.reference": "Referenz", "billing.note": "Notiz", "billing.add_credit": "Guthaben buchen", "billing.referrals": "Kunden werben Kunden", "billing.referred_customer": "Geworbener Kunde", "billing.save_referral": "Werbung speichern", "billing.recruited_count": "Geworbene Kunden", "billing.referred_by": "Geworben durch", "invoice.status.credited": "Gutgeschrieben", "credit_note.title": "Gutschriften", "credit_note.none": "Noch keine Gutschrift erstellt.", "credit_note.create": "Gutschrift erstellen", "credit_note.explanation": "Erstellt einen eigenen Korrekturbeleg mit Bezug zur Ursprungsrechnung und anteiliger Umsatzsteuer.", "credit_note.reason": "Korrekturgrund", "credit_note.confirm": "Gutschrift verbindlich erstellen? Der Beleg wird revisionssicher gespeichert.", "billing.apply_credit": "Guthaben jetzt verrechnen", "billing.referral_reward": "Empfehlungsprämie", "billing.refund": "Guthaben auszahlen", "billing.refund_confirm": "Auszahlung als verbindliche Guthabenbuchung erfassen?",
    },
    "en": {
        "overview": "Overview", "documents": "Documents", "inbox": "Inbox", "calendar": "Calendar", "contacts": "Contacts", "images": "Images", "settings": "Settings", "logout": "Sign out", "archives": "Archives", "notes": "Notes", "logbook": "Activity log",
        "crm.status.active": "Active", "crm.status.inactive": "Inactive", "crm.status.prospect": "Prospect", "crm.status.blocked": "Blocked", "crm.status.former": "Former",
        "crm.role.customer": "Customer", "crm.role.supplier": "Supplier", "crm.role.contact_person": "Contact person", "crm.role.private": "Private", "crm.role.other": "Other",
        "crm.activity.email": "Email", "crm.activity.phone": "Phone call", "crm.activity.meeting": "Meeting", "crm.activity.letter": "Letter", "crm.activity.note": "Note",
        "crm.direction.outgoing": "Outgoing", "crm.direction.incoming": "Incoming", "crm.direction.internal": "Internal",
        "crm.entries": "entries", "crm.last": "last", "crm.contact_field": "Contact field", "crm.changed": "changed",
        "crm.activity.saved": "CRM activity saved.", "crm.activity.error.type": "Unknown CRM activity type.", "crm.activity.error.direction": "Unknown CRM activity direction.", "crm.activity.error.content_required": "Subject or note is required.", "crm.activity.error.default": "CRM activity could not be saved.",
        "crm.csv.name": "Name", "crm.csv.company": "Company", "crm.csv.email": "Email", "crm.csv.phone": "Phone", "crm.csv.status": "Status", "crm.csv.roles": "Roles", "crm.csv.customer_number": "Customer number", "crm.csv.supplier_number": "Supplier number", "crm.csv.latest_activity": "Latest activity", "crm.csv.activities": "Activities",
        "invoice.nav": "Invoices", "invoice.overview.title": "Invoice overview", "invoice.overview.subtitle": "Search invoices, review due dates and record incoming payments.", "invoice.settings": "Templates / issuer", "invoice.stats.total": "Invoices", "invoice.status": "Status", "invoice.status.all": "All statuses", "invoice.status.open": "Open", "invoice.status.partial": "Partially paid", "invoice.status.overdue": "Overdue", "invoice.status.paid": "Paid", "invoice.search": "Search", "invoice.search.placeholder": "Invoice number or customer", "invoice.filter": "Filter", "invoice.reset": "Reset", "invoice.number": "Invoice number", "invoice.customer": "Customer", "invoice.issue_date": "Invoice date", "invoice.service_date": "Service date", "invoice.due_date": "Due", "invoice.total": "Total", "invoice.outstanding": "Outstanding amount", "invoice.open": "Open", "invoice.none": "No matching invoices.", "invoice.title": "Invoice", "invoice.immutable_notice": "Invoice data and line items are immutable snapshots. Incoming payments are recorded separately.", "invoice.pdf": "PDF document", "invoice.positions": "Line items", "invoice.position": "Pos.", "invoice.object": "Object", "invoice.description": "Description", "invoice.quantity": "Quantity", "invoice.net": "Net", "invoice.vat": "VAT", "invoice.free_position": "free", "invoice.data": "Invoice data", "invoice.payment_terms": "Payment terms", "invoice.net_total": "Net total", "invoice.tax_total": "VAT total", "invoice.payment_history": "Payment history", "invoice.no_payments": "No payment recorded yet.", "invoice.payment_amount": "Amount", "invoice.payment_date": "Payment date", "invoice.payment_reference": "Reference", "invoice.record_payment": "Record payment", "invoice.payment.saved": "Invoice payment recorded.", "invoice.payment.error.paid": "The invoice is already paid in full.", "invoice.payment.error.amount": "The payment amount must be positive and must not exceed the outstanding amount.", "invoice.payment.error.date": "The payment date is invalid.", "invoice.payment.error.default": "The payment could not be recorded.", "invoice.profile": "Profile", "invoice.recipient": "Recipient",
        "billing.title": "Billing and credit", "billing.customer": "Customer", "billing.new_invoice": "New invoice", "billing.download_all": "All invoices as ZIP", "billing.invoices": "Customer invoices", "billing.search_placeholder": "Invoice number, date or status", "billing.download": "Download", "billing.credit_history": "Credit history", "billing.no_credit_entries": "No credit entry yet.", "billing.credit": "Customer credit", "billing.credit_explanation": "Credit is applied as a payment. Invoice net, VAT and gross totals remain unchanged.", "billing.amount": "Amount", "billing.currency": "Currency", "billing.credit_kind": "Entry type", "billing.credit_kind.topup": "Top-up", "billing.credit_kind.referral": "Customer referral reward", "billing.credit_kind.manual": "Manual adjustment", "billing.credit_kind.credit_note": "Credit note", "billing.credit_kind.refund": "Refund", "billing.credit_kind.invoice_application": "Invoice application", "billing.tax_treatment": "Tax classification", "billing.tax_treatment.outside_scope": "Payment unrelated to a specific supply", "billing.tax_treatment.multipurpose_voucher": "Multi-purpose voucher", "billing.tax_treatment.taxable_advance": "Taxable advance payment", "billing.tax_treatment.manual_review": "Tax review required", "billing.select": "Please select", "billing.reference": "Reference", "billing.note": "Note", "billing.add_credit": "Post credit", "billing.referrals": "Refer a customer", "billing.referred_customer": "Referred customer", "billing.save_referral": "Save referral", "billing.recruited_count": "Referred customers", "billing.referred_by": "Referred by", "invoice.status.credited": "Credited", "credit_note.title": "Credit notes", "credit_note.none": "No credit note created yet.", "credit_note.create": "Create credit note", "credit_note.explanation": "Creates a separate correction document referencing the original invoice with proportional VAT.", "credit_note.reason": "Correction reason", "credit_note.confirm": "Create this binding credit note? The document will be stored with an audit trail.", "billing.apply_credit": "Apply credit now", "billing.referral_reward": "Referral reward", "billing.refund": "Refund credit", "billing.refund_confirm": "Record this refund as a binding credit entry?",
    },
}

# Older templates contain literal UI text.  The client-side catalogue keeps
# those pages language-aware while they are gradually moved to named keys.
# Entries are deliberately phrases (not only single words), so user-created
# contact names, document names and notes are never translated.
UI_LITERAL_TRANSLATIONS = {
    "en": {
        "Termine": "Appointments", "To-Do": "To-do", "Bearbeiten": "Edit", "Öffnen": "Open", "Speichern": "Save", "Suchen": "Search", "Filtern": "Filter", "Hinzufügen": "Add", "Entfernen": "Remove", "Löschen": "Delete", "Abbrechen": "Cancel", "Zurück": "Back",
        "Kontakte": "Contacts", "Kontaktliste": "Contact list", "Kontakt anlegen": "Create contact", "Kontaktdaten bearbeiten": "Edit contact details", "Änderungen speichern": "Save changes", "Änderungsverlauf": "Change history", "Gemeinsame Verwaltung": "Shared management", "Freigaben speichern": "Save sharing", "Eigentümer:": "Owner:", "Adressen": "Addresses", "Adresse": "Address", "Anzeigename": "Display name", "Vorname": "First name", "Nachname": "Last name", "Firma": "Company", "Geburtstag": "Birthday", "Telefon": "Phone", "Bearbeitet von": "Edited by", "Alle Bearbeiter": "All editors", "Geändertes Feld": "Changed field", "Alle geänderten Felder": "All changed fields", "Keine Änderungen entsprechen dem Filter.": "No changes match the filter.", "vCard exportieren": "Export vCard", "vCard importieren": "Import vCard", "Kontakt gespeichert.": "Contact saved.",
        "CRM-Übersicht öffnen": "Open CRM overview", "CRM-Kontaktübersicht": "CRM contact overview", "Kontakte und CRM-Daten gemeinsam durchsuchen und filtern.": "Search and filter contacts and CRM data together.", "CSV exportieren": "Export CSV", "Änderungshistorie": "Change history", "Treffer": "Results", "Aktiv": "Active", "Interessenten": "Prospects", "Ohne Aktivität": "Without activity", "Nur Kontakte ohne Aktivität": "Only contacts without activity", "Alle Aktivitäten anzeigen": "Show all activities", "Suche": "Search", "Name, Firma, Nummer, Kommunikation, Adresse oder Notiz": "Name, company, number, communication, address or note", "Alle Status": "All statuses", "Alle Rollen": "All roles", "Rolle": "Role", "Interessent": "Prospect", "Inaktiv": "Inactive", "Gesperrt": "Blocked", "Ehemalig": "Former", "Kunde": "Customer", "Lieferant": "Supplier", "Ansprechpartner": "Contact person", "Privat": "Private", "Sonstige": "Other", "Sortierung": "Sort order", "Name": "Name", "Letzte Aktivität": "Latest activity", "Zurücksetzen": "Reset", "Kontakt": "Contact", "Kommunikation": "Communication", "Einträge": "entries", "zuletzt": "last", "CRM öffnen": "Open CRM", "Keine passenden Kontakte.": "No matching contacts.", "Kommunikations- und Änderungshistorie": "Communication and change history", "Art": "Type", "E-Mail": "Email", "Telefonat": "Phone call", "Termin": "Meeting", "Brief": "Letter", "Richtung": "Direction", "Ausgehend": "Outgoing", "Eingehend": "Incoming", "Intern": "Internal", "Betreff": "Subject", "Notiz": "Note", "Aktivität speichern": "Save activity", "CRM-Daten geändert": "CRM data changed", "Felder:": "Fields:", "Noch keine Kommunikations- oder Änderungseinträge.": "No communication or change entries yet.",
        "Kalender": "Calendar", "Termin anlegen": "Create appointment", "Titel": "Title", "Grund": "Reason", "Beginn": "Start", "Ende": "End", "Sichtbarkeit": "Visibility", "Familie": "Family", "Extern": "External", "Externer Hinweis": "External notice", "Private Tags": "Private tags", "Externe Buchung": "External booking", "Buchung erlauben": "Allow booking", "Buchungszeiten speichern": "Save booking hours", "öffentliche Seite": "public page", "Offene Buchungsanfragen": "Pending booking requests", "Bestätigen und ICS senden": "Confirm and send ICS", "ICS herunterladen": "Download ICS", "E-Mail im Client vorbereiten": "Prepare email in client", "Versand ausstehend – ICS manuell weitergeben": "Delivery pending – forward ICS manually", "Einladung:": "Invitation:", "versendet": "sent", "Keine freien Termine an diesem Tag.": "No free appointments on this day.", "Termin anfragen": "Request an appointment", "Anfrage senden": "Send request", "Freie Zeiten anzeigen": "Show available times", "Dein Name": "Your name", "E-Mail für ICS-Antwort": "Email for ICS reply", "Freier Zeitpunkt": "Available time",
        "Dokumente": "Documents", "Dateien hinzufügen": "Add files", "Vollständig importieren": "Import completely", "Direkt ins feste Archiv importieren (nach SHA-256 sortiert)": "Import directly into the permanent archive (sorted by SHA-256)", "Notizen": "Notes", "Notiz speichern": "Save note", "Tags": "Tags", "Zustand": "State", "Versionen": "Versions", "Logbuch": "Activity log", "Datei freigeben": "Share file", "Passwort": "Password", "HTTPS-Link erzeugen": "Create HTTPS link", "Inbox": "Inbox", "Keine offenen Aufgaben.": "No open tasks.", "Neue Aufgabe": "New task", "Bilder": "Images", "Diashow": "Slideshow", "Alle Zeiten": "All time", "Diese Woche": "This week", "Dieser Monat": "This month", "Dieses Jahr": "This year", "Tags speichern": "Save tags", "Verwalten / freigeben": "Manage / share",
        "Einstellungen und Standardwerte": "Settings and defaults", "Oberfläche": "Interface", "Standardsprache für neue Sitzungen": "Default language for new sessions", "Zeitzone": "Time zone", "Dokumente und Import": "Documents and import", "Freigaben": "Sharing", "Standardwerte speichern": "Save defaults", "Archive": "Archives", "Externes Archiv registrieren": "Register external archive", "Verbindung prüfen": "Check connection", "Angeschlossene Archive suchen": "Find connected archives", "SSH-Systeme": "SSH systems", "SSH-Quelle hinzufügen": "Add SSH source", "Registrierte Quellen": "Registered sources", "Jetzt importieren": "Import now", "Quelle entfernen": "Remove source",
        "Geschützte Freigabe": "Protected share", "Gib das Passwort ein, um die freigegebene Datei oder Notiz zu öffnen.": "Enter the password to open the shared file or note.", "Systemübersicht": "System overview", "Zeit": "Time", "Dokumentenspeicher": "Document storage", "Verbundene Speicher": "Connected storage", "Leer.": "Empty.", "Noch keine Ereignisse.": "No events yet.", "Noch keine Notizen vorhanden.": "No notes yet.", "Keine veröffentlichten Termine.": "No published appointments.", "Familienkalender": "Family calendar", "Öffentliche Kalenderhinweise": "Public calendar notices",
        "Log In": "Sign in", "Log Out": "Sign out", "Register": "Register", "Username": "Username", "Password": "Password",
    },
    "de": {
        "Log In": "Anmelden", "Sign in": "Anmelden", "Log Out": "Abmelden", "Sign out": "Abmelden", "Register": "Registrieren", "Username": "Benutzername", "Password": "Passwort", "First": "Vorname", "Last": "Nachname",
    },
}

TRANSLATIONS["de"].update({"invoice.status.draft": "Entwurf", "invoice.recipient_type": "Empfängerart", "invoice.recipient.private": "Privatperson", "invoice.recipient.company": "Firma", "invoice.recipient.company_name": "Firmenname", "invoice.recipient.contact": "Name / Ansprechpartner", "invoice.recipient.contact_optional": "Ansprechpartner (optional)", "invoice.project.add": "Projekt abrechnen", "invoice.project.open": "Offene Projektposten", "invoice.project.none": "Keine offenen Projektposten vorhanden.", "invoice.category": "Kategorie"})
TRANSLATIONS["en"].update({"invoice.status.draft": "Draft", "invoice.recipient_type": "Recipient type", "invoice.recipient.private": "Private person", "invoice.recipient.company": "Company", "invoice.recipient.company_name": "Company name", "invoice.recipient.contact": "Name / contact person", "invoice.recipient.contact_optional": "Contact person (optional)", "invoice.project.add": "Bill project", "invoice.project.open": "Open project items", "invoice.project.none": "No open project items available.", "invoice.category": "Category"})
TRANSLATIONS["de"].update({
    "slideshow.title": "Diashow",
    "slideshow.close": "Diashow schließen",
    "slideshow.info_controls": "Info und Steuerung",
    "slideshow.autostart": "Automatisch starten",
    "slideshow.duration": "Anzeigedauer",
    "slideshow.seconds": "Sek.",
    "slideshow.start": "Start",
    "slideshow.stop": "Stop",
    "slideshow.open_original": "Original öffnen",
    "slideshow.previous": "Vorheriges Bild",
    "slideshow.next": "Nächstes Bild",
    "slideshow.thumbnails": "Bildübersicht",
    "slideshow.image": "Bild",
})
TRANSLATIONS["en"].update({
    "slideshow.title": "Slideshow",
    "slideshow.close": "Close slideshow",
    "slideshow.info_controls": "Info and controls",
    "slideshow.autostart": "Start automatically",
    "slideshow.duration": "Display time",
    "slideshow.seconds": "sec.",
    "slideshow.start": "Start",
    "slideshow.stop": "Stop",
    "slideshow.open_original": "Open original",
    "slideshow.previous": "Previous image",
    "slideshow.next": "Next image",
    "slideshow.thumbnails": "Image overview",
    "slideshow.image": "Image",
})
TRANSLATIONS["de"].update({
    "billing.customer_archive": "Kundenakte als ZIP",
    "billing.customer_documents": "Belege und Kundendokumente",
    "billing.customer_documents_explanation": "Explizit verknüpfte Dokumente und Rechnungsbelege; der Kundenakten-Export enthält Herkunft, Prüfsummen und Protokolle.",
    "billing.document_search_placeholder": "Datei, Beziehung oder Schlagwort",
    "billing.document": "Dokument", "billing.relation": "Beziehung", "billing.origin": "Herkunft", "billing.linked_at": "Verknüpft / erfasst",
    "billing.origin_email": "E-Mail-Archiv", "billing.origin_attachment": "E-Mail-Anhang", "billing.origin_copy": "Dokumentkopie", "billing.origin_local": "Lokaler Dokumentimport",
    "billing.document_unavailable": "Datei fehlt", "billing.no_customer_documents": "Noch keine Kundendokumente verknüpft.",
})
TRANSLATIONS["en"].update({
    "billing.customer_archive": "Customer archive as ZIP",
    "billing.customer_documents": "Receipts and customer documents",
    "billing.customer_documents_explanation": "Explicitly linked documents and invoice records; the customer archive includes provenance, checksums and audit logs.",
    "billing.document_search_placeholder": "File, relation or tag",
    "billing.document": "Document", "billing.relation": "Relation", "billing.origin": "Origin", "billing.linked_at": "Linked / recorded",
    "billing.origin_email": "Email archive", "billing.origin_attachment": "Email attachment", "billing.origin_copy": "Document copy", "billing.origin_local": "Local document import",
    "billing.document_unavailable": "File missing", "billing.no_customer_documents": "No customer documents linked yet.",
})
TRANSLATIONS["de"].update({
    "invoice.status.written_off": "Ausgebucht",
    "writeoff.title": "Forderung ausbuchen",
    "writeoff.history": "Ausbuchungshistorie",
    "writeoff.none": "Keine Forderung ausgebucht.",
    "writeoff.explanation": "Die Rechnung und ihre Umsatzsteuer bleiben unverändert. Nur die offene Forderung wird ganz oder teilweise ausgebucht.",
    "writeoff.reason": "Grund",
    "writeoff.reason.customer_deceased": "Kunde verstorben",
    "writeoff.reason.insolvency": "Insolvenz",
    "writeoff.reason.unknown_address": "Unbekannt verzogen",
    "writeoff.reason.collection_uneconomical": "Beitreibung wirtschaftlich nicht sinnvoll",
    "writeoff.reason.goodwill": "Kulanz",
    "writeoff.reason.other": "Sonstiger Grund",
    "writeoff.note": "Begründung / Aktennotiz",
    "writeoff.date": "Ausbuchungsdatum",
    "writeoff.original_outstanding": "Ursprünglich offen",
    "writeoff.amount": "Ausgebuchter Betrag",
    "writeoff.stop_collection": "Weitere Beitreibung und Mahnungen stoppen",
    "writeoff.submit": "Forderung verbindlich ausbuchen",
    "writeoff.confirm": "Forderung wirklich ausbuchen? Rechnung und Rechnungsnummer bleiben erhalten.",
    "writeoff.saved": "Forderung wurde ausgebucht und in der Historie protokolliert.",
    "writeoff.error": "Die Forderung konnte nicht ausgebucht werden.",
    "writeoff.error.draft": "Ein Rechnungsentwurf kann nicht ausgebucht werden.",
    "writeoff.error.no_outstanding": "Es ist keine beitreibbare Restforderung vorhanden.",
    "writeoff.error.reason": "Der Ausbuchungsgrund ist ungültig.",
    "writeoff.error.note": "Bei einem sonstigen Grund ist eine Begründung erforderlich.",
    "writeoff.error.amount": "Der Betrag muss positiv sein und darf die beitreibbare Restforderung nicht überschreiten.",
    "writeoff.error.date": "Das Ausbuchungsdatum ist ungültig.",
    "writeoff.error.date_before_invoice": "Das Ausbuchungsdatum darf nicht vor dem Rechnungsdatum liegen.",
    "writeoff.error.stop_requires_full": "Die Beitreibung kann nur bei vollständiger Ausbuchung der Restforderung gestoppt werden.",
    "writeoff.collection_stopped": "Weitere Beitreibung gestoppt",
})
TRANSLATIONS["en"].update({
    "invoice.status.written_off": "Written off",
    "writeoff.title": "Write off receivable",
    "writeoff.history": "Write-off history",
    "writeoff.none": "No receivable has been written off.",
    "writeoff.explanation": "The invoice and its VAT remain unchanged. Only the outstanding receivable is written off in full or in part.",
    "writeoff.reason": "Reason",
    "writeoff.reason.customer_deceased": "Customer deceased",
    "writeoff.reason.insolvency": "Insolvency",
    "writeoff.reason.unknown_address": "Customer address unknown",
    "writeoff.reason.collection_uneconomical": "Collection is not economically reasonable",
    "writeoff.reason.goodwill": "Goodwill",
    "writeoff.reason.other": "Other reason",
    "writeoff.note": "Reasoning / file note",
    "writeoff.date": "Write-off date",
    "writeoff.original_outstanding": "Originally outstanding",
    "writeoff.amount": "Amount written off",
    "writeoff.stop_collection": "Stop further collection and reminders",
    "writeoff.submit": "Write off receivable",
    "writeoff.confirm": "Write off this receivable? The invoice and invoice number remain unchanged.",
    "writeoff.saved": "The receivable was written off and recorded in the history.",
    "writeoff.error": "The receivable could not be written off.",
    "writeoff.error.draft": "A draft invoice cannot be written off.",
    "writeoff.error.no_outstanding": "There is no collectible outstanding receivable.",
    "writeoff.error.reason": "The write-off reason is invalid.",
    "writeoff.error.note": "A note is required when another reason is selected.",
    "writeoff.error.amount": "The amount must be positive and must not exceed the collectible outstanding receivable.",
    "writeoff.error.date": "The write-off date is invalid.",
    "writeoff.error.date_before_invoice": "The write-off date must not precede the invoice date.",
    "writeoff.error.stop_requires_full": "Collection can only be stopped when the full remaining receivable is written off.",
    "writeoff.collection_stopped": "Further collection stopped",
})
TRANSLATIONS["de"].update({
    "contact_update.title": "Kontaktdaten aktualisieren",
    "contact_update.explanation": "Bitte nur geänderte Angaben anpassen. Jede Änderung wird vor der Übernahme intern geprüft.",
    "contact_update.submitted": "Änderung übermittelt",
    "contact_update.submitted_text": "Die Angaben wurden als Vorschlag gespeichert. Der Kontakt wurde nicht direkt überschrieben.",
    "contact_update.no_changes": "Es wurden keine Änderungen übermittelt.",
    "contact_update.submit": "Änderungsvorschlag senden",
    "contact.first_name": "Vorname", "contact.last_name": "Nachname", "contact.display_name": "Anzeigename", "contact.nickname": "Spitzname",
    "contact.organization": "Organisation", "contact.company": "Firma", "contact.department": "Abteilung", "contact.title": "Position / Funktion", "contact.role": "Rolle",
    "contact.communication": "Kommunikation", "contact.email": "E-Mail", "contact.email_private": "E-Mail privat", "contact.email_business": "E-Mail geschäftlich",
    "contact.phone": "Telefon", "contact.mobile": "Mobil", "contact.phone_business": "Telefon geschäftlich", "contact.phone_private": "Telefon privat", "contact.fax": "Fax",
    "contact.address": "Adresse", "contact.city": "Ort", "contact.postal": "PLZ", "contact.street": "Straße und Hausnummer", "contact.country": "Land",
    "contact.birthday": "Geburtstag", "contact.website": "Webseite", "contact.tags": "Tags / Kategorien", "contact.note": "Notizen",
})
TRANSLATIONS["en"].update({
    "contact_update.title": "Update contact details",
    "contact_update.explanation": "Only adjust details that have changed. Every change is reviewed internally before it is accepted.",
    "contact_update.submitted": "Changes submitted",
    "contact_update.submitted_text": "The details were saved as a proposal. The contact was not overwritten directly.",
    "contact_update.no_changes": "No changes were submitted.",
    "contact_update.submit": "Submit change proposal",
    "contact.first_name": "First name", "contact.last_name": "Last name", "contact.display_name": "Display name", "contact.nickname": "Nickname",
    "contact.organization": "Organization", "contact.company": "Company", "contact.department": "Department", "contact.title": "Position / function", "contact.role": "Role",
    "contact.communication": "Communication", "contact.email": "Email", "contact.email_private": "Private email", "contact.email_business": "Business email",
    "contact.phone": "Phone", "contact.mobile": "Mobile", "contact.phone_business": "Business phone", "contact.phone_private": "Private phone", "contact.fax": "Fax",
    "contact.address": "Address", "contact.city": "City", "contact.postal": "Postal code", "contact.street": "Street and house number", "contact.country": "Country",
    "contact.birthday": "Birthday", "contact.website": "Website", "contact.tags": "Tags / categories", "contact.note": "Notes",
})
TRANSLATIONS["de"].update({
    "contact_update.proposals": "Externe Kontaktänderungen", "contact_update.accept": "Übernehmen", "contact_update.field": "Feld",
    "contact_update.current": "Aktueller Wert", "contact_update.proposed": "Vorschlag", "contact_update.accept_selected": "Ausgewählte übernehmen",
    "contact_update.reject": "Ablehnen", "contact_update.none": "Keine Änderungsvorschläge.", "contact_update.status.pending": "Offen",
    "contact_update.status.accepted": "Übernommen", "contact_update.status.rejected": "Abgelehnt", "contact_update.resolved": "Externe Kontaktänderung verarbeitet.",
    "contact_update.link": "Externer Aktualisierungslink: {url}",
})
TRANSLATIONS["en"].update({
    "contact_update.proposals": "External contact changes", "contact_update.accept": "Accept", "contact_update.field": "Field",
    "contact_update.current": "Current value", "contact_update.proposed": "Proposal", "contact_update.accept_selected": "Accept selected fields",
    "contact_update.reject": "Reject", "contact_update.none": "No change proposals.", "contact_update.status.pending": "Pending",
    "contact_update.status.accepted": "Accepted", "contact_update.status.rejected": "Rejected", "contact_update.resolved": "External contact change processed.",
    "contact_update.link": "External update link: {url}",
})
_CONTACT_UPDATE_FIELD_LABELS = {
    "first_name": ("Vorname", "First name"), "last_name": ("Nachname", "Last name"), "display_name": ("Anzeigename", "Display name"),
    "nickname": ("Spitzname", "Nickname"), "company": ("Firma", "Company"), "department": ("Abteilung", "Department"),
    "title": ("Position / Funktion", "Position / function"), "role": ("Rolle", "Role"), "email": ("E-Mail", "Email"),
    "email_private": ("E-Mail privat", "Private email"), "email_business": ("E-Mail geschäftlich", "Business email"),
    "phone": ("Telefon", "Phone"), "mobile": ("Mobil", "Mobile"), "phone_private": ("Telefon privat", "Private phone"),
    "phone_business": ("Telefon geschäftlich", "Business phone"), "fax": ("Fax", "Fax"), "address_city": ("Ort", "City"),
    "address_postal": ("PLZ", "Postal code"), "address_street": ("Straße und Hausnummer", "Street and house number"),
    "address_country": ("Land", "Country"), "birthday": ("Geburtstag", "Birthday"), "website": ("Webseite", "Website"),
    "tags": ("Tags / Kategorien", "Tags / categories"), "note": ("Notizen", "Notes"),
}
for _field, (_de_label, _en_label) in _CONTACT_UPDATE_FIELD_LABELS.items():
    TRANSLATIONS["de"][f"contact_update.field.{_field}"] = _de_label
    TRANSLATIONS["en"][f"contact_update.field.{_field}"] = _en_label

TRANSLATIONS["de"].update({
    "duplicates.title": "Kontakt-Dubletten", "duplicates.subtitle": "Separat geladen, damit der normale Kontaktmanager schnell bleibt.",
    "duplicates.candidates": "mögliche Dubletten", "duplicates.navigation": "Kontaktmanagement", "duplicates.tab": "Dubletten",
    "duplicates.select_all_safe": "Alle sicheren Paare auswählen", "duplicates.bulk_help": "Bis zu 100 eindeutige, inhaltlich konfliktfreie und nicht überlappende Paare können je Durchlauf zusammengeführt werden.",
    "duplicates.selected": "ausgewählt", "duplicates.merge_selected": "Ausgewählte zusammenführen", "duplicates.bulk_confirm": "Alle ausgewählten Kontaktpaare jetzt zusammenführen? Beide Originalfassungen werden jeweils gesichert.",
    "duplicates.merge_explanation": "Der inhaltsreichere Kontakt wird empfohlen und bleibt beim Massen-Merge erhalten. Leere Felder, neue Adressen, Tags und Gruppen werden sofort ergänzt. Konflikte müssen einzeln geprüft werden. Beide Originale werden als Snapshot gesichert.",
    "duplicates.select_pair": "Kontaktpaar auswählen", "duplicates.individual_only": "Nur einzeln zusammenführen", "duplicates.recommended": "Empfohlen / bleibt",
    "duplicates.keep_recommended": "Empfohlenen Kontakt behalten", "duplicates.keep_other": "Stattdessen anderen behalten",
    "duplicates.single_confirm": "Kontakte jetzt zusammenführen und den empfohlenen Kontakt behalten? Beide Originale werden gesichert.",
    "duplicates.single_confirm_reverse": "Kontakte jetzt zusammenführen und stattdessen den anderen Kontakt behalten? Beide Originale werden gesichert.",
    "duplicates.reasons": "Treffergründe", "duplicates.new": "neu", "duplicates.different": "abweichend", "duplicates.safe_bulk": "Sicherer Massen-Merge",
    "duplicates.review_required": "Prüfung erforderlich", "duplicates.no_additional": "Keine zusätzlichen Daten", "duplicates.field": "Feld",
    "duplicates.retained": "Empfohlen / bleibt", "duplicates.incoming": "Wird ergänzt", "duplicates.more_changes": "weitere Änderungen", "duplicates.none": "Keine wahrscheinlichen Dubletten gefunden.",
    "duplicates.merge_success": "Kontakte revisionssicher zusammengeführt. Beide vorherigen Fassungen wurden gesichert.", "duplicates.bulk_success": "{count} Kontakt-Dublette(n) revisionssicher zusammengeführt.",
    "duplicates.error.invalid": "Die Dublettenauswahl ist ungültig.", "duplicates.error.none_selected": "Keine Kontaktpaare ausgewählt.", "duplicates.error.distinct": "Jede Auswahl benötigt zwei unterschiedliche Kontakte.",
    "duplicates.error.overlap": "Ein Kontakt darf nur in einem ausgewählten Dublettenpaar vorkommen.", "duplicates.error.missing": "Mindestens ein ausgewählter Kontakt ist nicht mehr vorhanden.",
    "duplicates.error.editable": "Alle ausgewählten Kontakte müssen bearbeitbar sein.", "duplicates.error.conflict": "Der Massen-Merge erlaubt nur eindeutige Paare ohne widersprüchliche Werte.", "duplicates.error.owner": "Kontakte mit unterschiedlichen Eigentümern können nicht zusammengeführt werden.",
    "duplicates.reason.email": "gleiche E-Mail", "duplicates.reason.phone": "gleiche Telefonnummer", "duplicates.reason.display_name": "gleicher Anzeigename", "duplicates.reason.loose_name": "Name nur durch Leerzeichen/Zeichen verschieden", "duplicates.reason.company": "gleiche Firma", "duplicates.reason.person_name": "gleicher Vor-/Nachname", "duplicates.reason.address": "gleiche Adresse normalisiert", "duplicates.reason.core_conflicts": "{count} abweichende Kernfelder",
    "duplicates.combine_tab": "Zusammenführen", "duplicates.combine_title": "Mehrere Kontakte zusammenführen", "duplicates.combine_subtitle": "Suche eingrenzen, mindestens zwei Kontakte auswählen und alle vorhandenen Daten in einem Kontakt vereinigen.",
    "duplicates.search_label": "Kontakte durchsuchen", "duplicates.search_hint": "Name, E-Mail, Telefon, Firma, Adresse oder ID", "duplicates.match_filter": "Gemeinsames Feld", "duplicates.match_any": "Alle Treffer", "duplicates.match_name": "Gleicher Name", "duplicates.match_email": "Gleiche E-Mail", "duplicates.match_phone": "Gleiches Telefon", "duplicates.match_company": "Gleiche Firma", "duplicates.match_address": "Gleiche Adresse", "duplicates.refine": "Anzeige verfeinern",
    "duplicates.combine_help": "Abweichende E-Mail-Adressen und Telefonnummern bleiben als zusätzliche vCard-Werte erhalten. Fehlende übrige Felder werden ergänzt; identische Werte werden nur einmal gespeichert.", "duplicates.multi_confirm": "Alle ausgewählten Kontakte jetzt zu einem Kontakt zusammenführen? Die Originalfassungen werden gesichert.", "duplicates.combine_selected": "Auswahl zusammenführen", "duplicates.select_contact": "Kontakt auswählen", "duplicates.name": "Name", "duplicates.contact_data": "Kontaktdaten", "duplicates.match_indicators": "Gemeinsame Felder", "duplicates.no_search_results": "Keine Kontakte für diese Einschränkung gefunden.", "duplicates.multi_success": "{count} Kontakte revisionssicher zu einem Kontakt zusammengeführt.", "duplicates.error.at_least_two": "Wähle mindestens zwei Kontakte aus.", "duplicates.error.too_many": "Je Vorgang können höchstens 100 Kontakte zusammengeführt werden.",
})
TRANSLATIONS["en"].update({
    "duplicates.title": "Duplicate contacts", "duplicates.subtitle": "Loaded separately so the normal contact manager stays fast.",
    "duplicates.candidates": "possible duplicates", "duplicates.navigation": "Contact management", "duplicates.tab": "Duplicates",
    "duplicates.select_all_safe": "Select all safe pairs", "duplicates.bulk_help": "Up to 100 unambiguous, semantically conflict-free, non-overlapping pairs can be merged per batch.",
    "duplicates.selected": "selected", "duplicates.merge_selected": "Merge selected", "duplicates.bulk_confirm": "Merge all selected contact pairs now? Both original versions of each pair will be saved.",
    "duplicates.merge_explanation": "The more complete contact is recommended and retained by bulk merge. Empty fields, new addresses, tags and groups are added immediately. Conflicts require individual review. Both originals are saved as snapshots.",
    "duplicates.select_pair": "Select contact pair", "duplicates.individual_only": "Individual merge only", "duplicates.recommended": "Recommended / retained",
    "duplicates.keep_recommended": "Keep recommended contact", "duplicates.keep_other": "Keep the other contact instead",
    "duplicates.single_confirm": "Merge these contacts now and keep the recommended contact? Both originals will be saved.",
    "duplicates.single_confirm_reverse": "Merge these contacts now and keep the other contact instead? Both originals will be saved.",
    "duplicates.reasons": "Match reasons", "duplicates.new": "new", "duplicates.different": "different", "duplicates.safe_bulk": "Safe bulk merge",
    "duplicates.review_required": "Review required", "duplicates.no_additional": "No additional data", "duplicates.field": "Field",
    "duplicates.retained": "Recommended / retained", "duplicates.incoming": "Added data", "duplicates.more_changes": "more changes", "duplicates.none": "No likely duplicates found.",
    "duplicates.merge_success": "Contacts merged with an audit trail. Both previous versions were saved.", "duplicates.bulk_success": "{count} duplicate contact pair(s) merged with an audit trail.",
    "duplicates.error.invalid": "The duplicate selection is invalid.", "duplicates.error.none_selected": "No contact pairs selected.", "duplicates.error.distinct": "Each selection requires two different contacts.",
    "duplicates.error.overlap": "A contact may only occur in one selected duplicate pair.", "duplicates.error.missing": "At least one selected contact no longer exists.",
    "duplicates.error.editable": "All selected contacts must be editable.", "duplicates.error.conflict": "Bulk merge only accepts unambiguous pairs without conflicting values.", "duplicates.error.owner": "Contacts with different owners cannot be merged.",
    "duplicates.reason.email": "same email address", "duplicates.reason.phone": "same phone number", "duplicates.reason.display_name": "same display name", "duplicates.reason.loose_name": "name differs only in spacing/punctuation", "duplicates.reason.company": "same company", "duplicates.reason.person_name": "same first/last name", "duplicates.reason.address": "same normalized address", "duplicates.reason.core_conflicts": "{count} conflicting core field(s)",
    "duplicates.combine_tab": "Combine", "duplicates.combine_title": "Combine multiple contacts", "duplicates.combine_subtitle": "Refine the search, select at least two contacts, and unite all available data in one contact.",
    "duplicates.search_label": "Search contacts", "duplicates.search_hint": "Name, email, phone, company, address, or ID", "duplicates.match_filter": "Shared field", "duplicates.match_any": "All results", "duplicates.match_name": "Same name", "duplicates.match_email": "Same email", "duplicates.match_phone": "Same phone", "duplicates.match_company": "Same company", "duplicates.match_address": "Same address", "duplicates.refine": "Refine results",
    "duplicates.combine_help": "Different email addresses and phone numbers are retained as additional vCard values. Other missing fields are filled; identical values are stored only once.", "duplicates.multi_confirm": "Combine all selected contacts into one contact now? The original versions will be saved.", "duplicates.combine_selected": "Combine selection", "duplicates.select_contact": "Select contact", "duplicates.name": "Name", "duplicates.contact_data": "Contact data", "duplicates.match_indicators": "Shared fields", "duplicates.no_search_results": "No contacts match these filters.", "duplicates.multi_success": "{count} contacts combined into one with an audit trail.", "duplicates.error.at_least_two": "Select at least two contacts.", "duplicates.error.too_many": "At most 100 contacts can be combined per operation.",
})


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
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        for section, values in stored.items() if isinstance(stored, dict) else []:
            if section in result and isinstance(values, dict):
                result[section].update(values)
        stored_media = stored.get("media") if isinstance(stored, dict) else None
        if not isinstance(stored_media, dict) or any(
            key not in stored_media for key in DEFAULT_SETTINGS["media"]
        ):
            legacy_media = self._legacy_media_settings()
            for key, legacy_value in legacy_media.items():
                if not isinstance(stored_media, dict) or key not in stored_media:
                    result["media"][key] = legacy_value
        result["media"] = self._normalize_media_settings(result["media"])
        stored_network = stored.get("network") if isinstance(stored, dict) else None
        if not isinstance(stored_network, dict) or "webdav_quota_mib" not in stored_network:
            result["network"]["webdav_quota_mib"] = self._legacy_webdav_quota()
        if not isinstance(stored_network, dict) or "rsync_timeout_seconds" not in stored_network:
            result["network"]["rsync_timeout_seconds"] = self._legacy_rsync_timeout()
        if not isinstance(stored_network, dict) or "rsync_max_bytes" not in stored_network:
            result["network"]["rsync_max_bytes"] = self._legacy_rsync_max_bytes()
        if not isinstance(stored_network, dict) or "rsync_max_files" not in stored_network:
            result["network"]["rsync_max_files"] = self._legacy_rsync_max_files()
        result["network"] = self._normalize_network_settings(result["network"])
        stored_integrations = stored.get("integrations") if isinstance(stored, dict) else None
        if (
            not isinstance(stored_integrations, dict)
            or "osm_download_read_timeout_seconds" not in stored_integrations
        ):
            result["integrations"]["osm_download_read_timeout_seconds"] = (
                legacy_osm_read_timeout()
            )
        if not isinstance(stored_integrations, dict) or "osm_filter_timeout_seconds" not in stored_integrations:
            result["integrations"]["osm_filter_timeout_seconds"] = (
                legacy_osm_filter_timeout()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "osm_export_idle_timeout_seconds" not in stored_integrations
        ):
            result["integrations"]["osm_export_idle_timeout_seconds"] = (
                legacy_osm_export_idle_timeout()
            )
        if not isinstance(stored_integrations, dict) or "osm_max_download_gib" not in stored_integrations:
            result["integrations"]["osm_max_download_gib"] = legacy_osm_max_download_gib()
        if not isinstance(stored_integrations, dict) or "osm_download_retries" not in stored_integrations:
            result["integrations"]["osm_download_retries"] = legacy_osm_download_retries()
        if (
            not isinstance(stored_integrations, dict)
            or "clamav_timeout_seconds" not in stored_integrations
        ):
            result["integrations"]["clamav_timeout_seconds"] = (
                legacy_clamav_timeout()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "personnel_time_autosync_enabled" not in stored_integrations
        ):
            result["integrations"]["personnel_time_autosync_enabled"] = (
                legacy_personnel_time_autosync_enabled()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "personnel_time_autosync_seconds" not in stored_integrations
        ):
            result["integrations"]["personnel_time_autosync_seconds"] = (
                legacy_personnel_time_autosync_seconds()
            )
        if (
            not isinstance(stored_integrations, dict)
            or "printershare_purge_interval_seconds" not in stored_integrations
        ):
            result["integrations"]["printershare_purge_interval_seconds"] = (
                legacy_printershare_purge_interval_seconds()
            )
        result["integrations"] = self._normalize_integration_settings(
            result["integrations"]
        )
        return result

    @staticmethod
    def _legacy_webdav_quota() -> int:
        """Read the old environment setting only until a persisted value exists."""
        requested = os.environ.get("SIMPLEOFFICE_WEBDAV_QUOTA_MIB", "0").strip()
        return _bounded_setting(
            requested, DEFAULT_WEBDAV_QUOTA_MIB, DEFAULT_WEBDAV_QUOTA_MIB,
            MAX_WEBDAV_QUOTA_MIB,
        )

    @staticmethod
    def _normalize_network_settings(network: dict[str, Any]) -> dict[str, Any]:
        quota = network.get("webdav_quota_mib", DEFAULT_WEBDAV_QUOTA_MIB)
        if (
            isinstance(quota, bool)
            or not isinstance(quota, int)
            or not DEFAULT_WEBDAV_QUOTA_MIB <= quota <= MAX_WEBDAV_QUOTA_MIB
        ):
            _LOGGER.warning("Invalid stored WebDAV quota; using the unlimited default")
            quota = DEFAULT_WEBDAV_QUOTA_MIB
        rsync_timeout = network.get(
            "rsync_timeout_seconds", DEFAULT_RSYNC_TIMEOUT_SECONDS,
        )
        if (
            isinstance(rsync_timeout, bool)
            or not isinstance(rsync_timeout, int)
            or not MIN_RSYNC_TIMEOUT_SECONDS <= rsync_timeout <= MAX_RSYNC_TIMEOUT_SECONDS
        ):
            _LOGGER.warning("Invalid stored rsync timeout; using the default")
            rsync_timeout = DEFAULT_RSYNC_TIMEOUT_SECONDS
        rsync_max_bytes = network.get("rsync_max_bytes", DEFAULT_RSYNC_MAX_BYTES)
        if (
            isinstance(rsync_max_bytes, bool)
            or not isinstance(rsync_max_bytes, int)
            or not MIN_RSYNC_MAX_BYTES <= rsync_max_bytes <= MAX_RSYNC_MAX_BYTES
        ):
            _LOGGER.warning("Invalid stored rsync size limit; using the default")
            rsync_max_bytes = DEFAULT_RSYNC_MAX_BYTES
        rsync_max_files = network.get("rsync_max_files", DEFAULT_RSYNC_MAX_FILES)
        if (
            isinstance(rsync_max_files, bool)
            or not isinstance(rsync_max_files, int)
            or not MIN_RSYNC_MAX_FILES <= rsync_max_files <= MAX_RSYNC_MAX_FILES
        ):
            _LOGGER.warning("Invalid stored rsync file-count limit; using the default")
            rsync_max_files = DEFAULT_RSYNC_MAX_FILES
        return {
            "webdav_quota_mib": quota,
            "rsync_timeout_seconds": rsync_timeout,
            "rsync_max_bytes": rsync_max_bytes,
            "rsync_max_files": rsync_max_files,
        }

    @staticmethod
    def _legacy_rsync_timeout() -> int:
        """Read and bound the legacy rsync session timeout until it is saved."""
        raw = os.environ.get(
            "SIMPLEOFFICE_RSYNC_TIMEOUT", str(DEFAULT_RSYNC_TIMEOUT_SECONDS),
        ).strip()
        is_integer = raw.isdecimal() or (raw.startswith("-") and raw[1:].isdecimal())
        if len(raw) > 12 or not raw.isascii() or not is_integer:
            _LOGGER.warning("Invalid legacy rsync timeout; using the default")
            return DEFAULT_RSYNC_TIMEOUT_SECONDS
        value = int(raw)
        if not MIN_RSYNC_TIMEOUT_SECONDS <= value <= MAX_RSYNC_TIMEOUT_SECONDS:
            _LOGGER.warning("Legacy rsync timeout outside range; clamping to supported limits")
            return max(MIN_RSYNC_TIMEOUT_SECONDS, min(MAX_RSYNC_TIMEOUT_SECONDS, value))
        return value

    @staticmethod
    def _legacy_rsync_max_bytes() -> int:
        """Read and bound the legacy rsync size limit until it is saved."""
        raw = os.environ.get(
            "SIMPLEOFFICE_RSYNC_MAX_BYTES", str(DEFAULT_RSYNC_MAX_BYTES),
        ).strip()
        is_integer = raw.isdecimal() or (raw.startswith("-") and raw[1:].isdecimal())
        if len(raw) > 20 or not raw.isascii() or not is_integer:
            _LOGGER.warning("Invalid legacy rsync size limit; using the default")
            return DEFAULT_RSYNC_MAX_BYTES
        value = int(raw)
        if not MIN_RSYNC_MAX_BYTES <= value <= MAX_RSYNC_MAX_BYTES:
            _LOGGER.warning("Legacy rsync size limit outside range; clamping to supported limits")
            return max(MIN_RSYNC_MAX_BYTES, min(MAX_RSYNC_MAX_BYTES, value))
        return value

    @staticmethod
    def _legacy_rsync_max_files() -> int:
        """Read and bound the old file-count limit until it is saved in settings."""
        raw = os.environ.get(
            "SIMPLEOFFICE_RSYNC_MAX_FILES", str(DEFAULT_RSYNC_MAX_FILES),
        ).strip()
        is_integer = raw.isdecimal() or (raw.startswith("-") and raw[1:].isdecimal())
        if len(raw) > 12 or not raw.isascii() or not is_integer:
            _LOGGER.warning("Invalid legacy rsync file-count limit; using the default")
            return DEFAULT_RSYNC_MAX_FILES
        value = int(raw)
        if not MIN_RSYNC_MAX_FILES <= value <= MAX_RSYNC_MAX_FILES:
            _LOGGER.warning("Legacy rsync file-count limit outside range; clamping to supported limits")
            return max(MIN_RSYNC_MAX_FILES, min(MAX_RSYNC_MAX_FILES, value))
        return value

    @staticmethod
    def _normalize_integration_settings(integrations: dict[str, Any]) -> dict[str, Any]:
        return normalize_integration_settings(integrations)

    def _legacy_media_settings(self) -> dict[str, Any]:
        legacy_path = self.root / CONTROL_DIR / LEGACY_VIDEO_PREVIEW_SETTINGS_FILE
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            legacy = {}
        legacy_frames = (
            legacy.get("frame_count")
            if isinstance(legacy, dict) and "frame_count" in legacy
            else os.environ.get("SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES", DEFAULT_VIDEO_PREVIEW_FRAMES)
        )
        return {
            "video_preview_frames": _bounded_setting(
                legacy_frames, DEFAULT_VIDEO_PREVIEW_FRAMES,
                MIN_VIDEO_PREVIEW_FRAMES, MAX_VIDEO_PREVIEW_FRAMES,
            ),
            "preview_enabled": os.environ.get("SIMPLEOFFICE_PREVIEWS", "1").strip().casefold()
            not in {"0", "false", "off"},
            "preview_timeout_seconds": _bounded_setting(
                os.environ.get("SIMPLEOFFICE_PREVIEW_TIMEOUT_SECONDS"),
                DEFAULT_PREVIEW_TIMEOUT_SECONDS, MIN_PREVIEW_TIMEOUT_SECONDS,
                MAX_PREVIEW_TIMEOUT_SECONDS,
            ),
            "preview_max_bytes": _bounded_setting(
                os.environ.get("SIMPLEOFFICE_PREVIEW_MAX_BYTES"),
                DEFAULT_PREVIEW_MAX_BYTES, MIN_PREVIEW_MAX_BYTES, MAX_PREVIEW_MAX_BYTES,
            ),
            "preview_max_pixels": _bounded_setting(
                os.environ.get("SIMPLEOFFICE_PREVIEW_MAX_PIXELS"),
                DEFAULT_PREVIEW_MAX_PIXELS, MIN_PREVIEW_MAX_PIXELS, MAX_PREVIEW_MAX_PIXELS,
            ),
            "archive_ocr_enabled": os.environ.get(
                "SIMPLEOFFICE_ARCHIVE_OCR", "0",
            ).strip().casefold() in {"1", "true", "yes", "on"},
        }

    @staticmethod
    def _normalize_media_settings(media: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(media)
        if not isinstance(normalized.get("preview_enabled"), bool):
            _LOGGER.warning("Invalid stored document preview enable setting; using default")
            normalized["preview_enabled"] = DEFAULT_PREVIEW_ENABLED
        if not isinstance(normalized.get("archive_ocr_enabled"), bool):
            _LOGGER.warning("Invalid stored archive OCR setting; using default")
            normalized["archive_ocr_enabled"] = DEFAULT_ARCHIVE_OCR_ENABLED
        ranges = {
            "video_preview_frames": (
                DEFAULT_VIDEO_PREVIEW_FRAMES,
                MIN_VIDEO_PREVIEW_FRAMES,
                MAX_VIDEO_PREVIEW_FRAMES,
            ),
            "preview_timeout_seconds": (
                DEFAULT_PREVIEW_TIMEOUT_SECONDS,
                MIN_PREVIEW_TIMEOUT_SECONDS,
                MAX_PREVIEW_TIMEOUT_SECONDS,
            ),
            "preview_max_bytes": (
                DEFAULT_PREVIEW_MAX_BYTES,
                MIN_PREVIEW_MAX_BYTES,
                MAX_PREVIEW_MAX_BYTES,
            ),
            "preview_max_pixels": (
                DEFAULT_PREVIEW_MAX_PIXELS,
                MIN_PREVIEW_MAX_PIXELS,
                MAX_PREVIEW_MAX_PIXELS,
            ),
        }
        for key, (default, minimum, maximum) in ranges.items():
            value = normalized.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= maximum
            ):
                _LOGGER.warning("Invalid stored media setting %s; using default", key)
                normalized[key] = default
        return normalized

    def save(self, settings: dict[str, Any], actor: str) -> dict[str, Any]:
        if not actor.strip():
            raise ValueError("a named user is required")
        with _settings_write_lock(self.path):
            latest = self.settings()
            if not self.path.exists():
                from flask import current_app, has_app_context
                if has_app_context() and current_app.config.get("S3_OVERLAY_ENABLED") is True:
                    latest["s3"] = {"enabled": True}
            # Ordinary preferences and admin S3 changes share one atomic merge.
            # Network, media and integration settings support partial writes so callers
            # preserve the other controls in the same section.
            merged = {**latest, **settings}
            for section in ("network", "media", "integrations"):
                section_update = settings.get(section)
                if isinstance(section_update, dict):
                    merged[section] = {**latest[section], **section_update}
            normalized = self._validate(merged)
            atomic_json_write(self.path, normalized)
            self.history.record("settings_updated", actor, "settings", "application-defaults", {"updated_at": utc_now(), **normalized})
            return normalized

    def set_s3_enabled(self, enabled: bool, actor: str) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("S3 enabled must be boolean")
        return self.save({"s3": {"enabled": enabled}}, actor)

    def _validate(self, settings: dict[str, Any]) -> dict[str, Any]:
        interface = settings.get("interface", {})
        documents = settings.get("documents", {})
        calendar = settings.get("calendar", {})
        sharing = settings.get("sharing", {})
        s3 = settings.get("s3", {})
        network = settings.get("network", {})
        media = settings.get("media", {})
        integrations = settings.get("integrations", {})
        if not isinstance(network, dict):
            raise ValueError("network settings must be an object")
        webdav_quota = network.get("webdav_quota_mib", DEFAULT_WEBDAV_QUOTA_MIB)
        if (
            isinstance(webdav_quota, bool)
            or not isinstance(webdav_quota, int)
            or not DEFAULT_WEBDAV_QUOTA_MIB <= webdav_quota <= MAX_WEBDAV_QUOTA_MIB
        ):
            raise ValueError("WebDAV quota must be 0 or between 1 and 1,048,576 MiB")
        rsync_timeout = network.get(
            "rsync_timeout_seconds", DEFAULT_RSYNC_TIMEOUT_SECONDS,
        )
        if (
            isinstance(rsync_timeout, bool)
            or not isinstance(rsync_timeout, int)
            or not MIN_RSYNC_TIMEOUT_SECONDS <= rsync_timeout <= MAX_RSYNC_TIMEOUT_SECONDS
        ):
            raise ValueError("rsync timeout must be between 30 and 86,400 seconds")
        rsync_max_bytes = network.get("rsync_max_bytes", DEFAULT_RSYNC_MAX_BYTES)
        if (
            isinstance(rsync_max_bytes, bool)
            or not isinstance(rsync_max_bytes, int)
            or not MIN_RSYNC_MAX_BYTES <= rsync_max_bytes <= MAX_RSYNC_MAX_BYTES
        ):
            raise ValueError("rsync size limit must be between 1 MiB and 2 GiB")
        rsync_max_files = network.get("rsync_max_files", DEFAULT_RSYNC_MAX_FILES)
        if (
            isinstance(rsync_max_files, bool)
            or not isinstance(rsync_max_files, int)
            or not MIN_RSYNC_MAX_FILES <= rsync_max_files <= MAX_RSYNC_MAX_FILES
        ):
            raise ValueError("rsync file-count limit must be between 0 and 100,000")
        if not isinstance(media, dict):
            raise ValueError("media settings must be an object")
        if not isinstance(integrations, dict):
            raise ValueError("integration settings must be an object")
        integrations = validate_integration_settings(integrations)
        video_preview_frames = media.get("video_preview_frames", DEFAULT_VIDEO_PREVIEW_FRAMES)
        if (
            isinstance(video_preview_frames, bool)
            or not isinstance(video_preview_frames, int)
            or not MIN_VIDEO_PREVIEW_FRAMES <= video_preview_frames <= MAX_VIDEO_PREVIEW_FRAMES
        ):
            raise ValueError("video preview frame count must be between 1 and 30")
        preview_enabled = media.get("preview_enabled", DEFAULT_PREVIEW_ENABLED)
        if not isinstance(preview_enabled, bool):
            raise ValueError("document previews enabled must be boolean")
        archive_ocr_enabled = media.get("archive_ocr_enabled", DEFAULT_ARCHIVE_OCR_ENABLED)
        if not isinstance(archive_ocr_enabled, bool):
            raise ValueError("archive OCR enabled must be boolean")
        preview_timeout = media.get("preview_timeout_seconds", DEFAULT_PREVIEW_TIMEOUT_SECONDS)
        if (
            isinstance(preview_timeout, bool)
            or not isinstance(preview_timeout, int)
            or not MIN_PREVIEW_TIMEOUT_SECONDS <= preview_timeout <= MAX_PREVIEW_TIMEOUT_SECONDS
        ):
            raise ValueError("preview timeout must be between 5 and 300 seconds")
        preview_max_bytes = media.get("preview_max_bytes", DEFAULT_PREVIEW_MAX_BYTES)
        if (
            isinstance(preview_max_bytes, bool)
            or not isinstance(preview_max_bytes, int)
            or not MIN_PREVIEW_MAX_BYTES <= preview_max_bytes <= MAX_PREVIEW_MAX_BYTES
        ):
            raise ValueError("preview byte limit is outside the allowed range")
        preview_max_pixels = media.get("preview_max_pixels", DEFAULT_PREVIEW_MAX_PIXELS)
        if (
            isinstance(preview_max_pixels, bool)
            or not isinstance(preview_max_pixels, int)
            or not MIN_PREVIEW_MAX_PIXELS <= preview_max_pixels <= MAX_PREVIEW_MAX_PIXELS
        ):
            raise ValueError("preview pixel limit is outside the allowed range")
        if not isinstance(s3.get("enabled", False), bool):
            raise ValueError("S3 enabled must be boolean")
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
        return {
            "interface": {"default_language": language, "timezone": timezone},
            "documents": {
                "default_state": str(documents.get("default_state", "new")).strip() or "new",
                "default_tags": sorted(set(tags), key=str.casefold),
                "upload_to_archive": documents.get("upload_to_archive") is True,
            },
            "calendar": {
                "default_visibility": visibility,
                "default_public_notice": str(calendar.get("default_public_notice", "Belegt")).strip(),
                "default_duration_minutes": duration,
            },
            "sharing": {"default_expiry_days": expiry},
            "s3": {"enabled": s3.get("enabled", False)},
            "network": {
                "webdav_quota_mib": webdav_quota,
                "rsync_timeout_seconds": rsync_timeout,
                "rsync_max_bytes": rsync_max_bytes,
                "rsync_max_files": rsync_max_files,
            },
            "media": {
                "archive_ocr_enabled": archive_ocr_enabled,
                "video_preview_frames": video_preview_frames,
                "preview_enabled": preview_enabled,
                "preview_timeout_seconds": preview_timeout,
                "preview_max_bytes": preview_max_bytes,
                "preview_max_pixels": preview_max_pixels,
            },
            "integrations": {
                **integrations,
            },
        }
