# Issue #1136 – Mail-Client vollständig ausbauen: Plan

Ziel aus [#1136](https://github.com/JensKapitza/SimpleOffice4Me/issues/1136):
teilweise funktionalen Browser-Mail-Client zu einem vollständigen, responsiven
Mehrkonten-Mail-Client nach Outlook-/Thunderbird-Konventionen ausbauen.
Bestand wiederverwenden, keine Parallelarchitektur, IMAP/SMTP nur übers Backend.
Keine automatischen Merges oder Issue-Schließungen (Issue-Vorgabe).

## Ist-Zustand (verifiziert 20.11.2026)

- Mehrkonten-IMAP/SMTP mit verschlüsselten Zugangsdaten (`MailStore`, SecretBox),
  Autoconfig, Sieve, Delegation (`app/mail_client.py`, `app/mail_routes.py`).
- Webclient mit Ordnern, Nachrichtenliste (Suche, Paginierung, Spaltenwahl),
  Lesebereich, Anhängen, Archivieren, Antworten, Verschieben, Gelesen-Status,
  Vorgängen/Kommentaren/Entwürfen (`app/mail_webclient.py`, `app/mail_reader_routes.py`,
  `templates/documents/mail_reader.html`).
- Strikte Lesemodi: `MailAccountPolicy.require_writable`, BODY.PEEK-Lesezugriffe,
  `MailReadOnlyError` an allen Schreibrouten; Schreiben nur nach ausdrücklicher
  Freigabe je Konto.
- Archiv: EML-Übernahme ins Dokumentenarchiv, Vorschau, Berechtigungsprüfung
  (`app/mail_archive_storage.py`, `app/mail_document_access.py`), Retention-Basis
  (`app/retention.py`), generische Dokumentenregeln.
- Tests: `tests/test_mail_*.py` (u. a. Fake-IMAP-Muster in `test_mail_reader.py`).

## Lücken → Slices (je Slice ein PR mit Blackbox-Tests, kein Auto-Merge)

| Slice | Inhalt | Kern-Akzeptanz aus #1136 |
|---|---|---|
| 1. UI-Shell (dieser PR) | 3-Spalten-Navigation per Tastatur (j/k/Enter), Befehls-Palette (Strg+K), mobile Pane-Umschaltung, Shortcut-Hinweise; nur bestehende Routen, ohne Backend-Änderung | Responsive UI, Tastatur-/Schnellaktionen |
| 2. Flüchtiger Cache | Prozess-lokaler LRU-Cache (Default 256 MiB, 200–300 konfigurierbar), pro Benutzer/Konto isoliert, Neustart-Leerung, Flags separat synchronisiert, keine Server-Mutationen | Wiederholtes Öffnen ohne Re-Download; Grenze/Isolation nachgewiesen |
| 3. Lese-Timing | Konfigurierbare Verweildauer bis Gelesen-Markierung; Schnellaktion „Gelesen + nächste“ (nur Ansicht) | Timing + Schnellaktion |
| 4. Tags/Keywords | Server-Keyword-Sync nach Capability, UI-Kennzeichnungen | Flags/Tags synchron |
| 5. Überführen | Mail/Anhang → Aufgabe, Wiedervorlage/Termin, Dokumentenbezug mit stabilen Referenzen | Integration ohne Kopienflut |
| 6. Signaturen/Benachrichtigung | Kontosignaturen, ungelesen-Benachrichtigungen | Standardfunktionen |
| 7. Archiv-Härtung | Byteidentitäts-Nachweis EML (SHA-256), Anhang-Ordner inkl. Edge Cases (Duplikate, Unicode, ZIP-Slip), generische Retention/Audit-Regeln, Fake-IMAP-Nachweis „keine Mutation im Nur-Lese-Modus“ | Archiv-Azzeptanz + Readonly-Beweis |

## UI-Leitlinien (Hi-Tech, ohne neue Abhängigkeiten)

- Tastatur-zuerst (Outlook/Gmail-Konventionen), Befehlspalette, lokale Präferenzen
  (localStorage, wie Spaltenwahl), responsives 3-Spalten-Layout mit mobiler
  Pane-Umschaltung, Dark/Light über bestehendes Theme-System.
- Vanilla-JS + serverseitiges Rendering (bestehender Stack); jede Aktion bleibt
  ohne JS als Link/Formular nutzbar (auffangende Verschlechterung statt
  Client-Zwang). Keine neuen Laufzeitabhängigkeiten.
