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

TRANSLATIONS["de"].update({"invoice.status.draft": "Entwurf"})
TRANSLATIONS["en"].update({"invoice.status.draft": "Draft"})


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
