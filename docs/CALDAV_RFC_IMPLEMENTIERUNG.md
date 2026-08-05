# Mehrere Kalender und CalDAV: RFC-Auswertung und Umsetzung

## Zweck und Nutzen

SimpleOffice4Me stellt mehrere getrennte Kalender bereit und synchronisiert
`VEVENT`-Ressourcen mit Thunderbird und anderen CalDAV-Clients. Kalender haben
Name, Farbe, Beschreibung, Zeitzone, Eigentümer und eigene Lese- oder
Bearbeitungsfreigaben. Bestehende Termine ohne `calendar_id` bleiben ohne
Migration im persönlichen Kalender `default` sichtbar.

## Ausgewertete Primärstandards

### iCalendar – RFC 5545

Quelle: [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545.html).

- Eine Kalenderobjekt-Ressource enthält genau ein `VEVENT`; `UID` und
  `DTSTART` werden geprüft (Abschnitte 3.6.1 und 3.8.4.7).
- Textwerte, Zeilenfaltung, `TZID`, UTC mit `Z`, `SEQUENCE`, `STATUS` und
  `CATEGORIES` werden verarbeitet (Abschnitte 3.1, 3.2.19, 3.3.5 und 3.8).
- Unbekannte `TZID` werden abgelehnt, statt als lokale Zeit fehlinterpretiert zu
  werden. Bekannte IANA-Zonen werden als Offset gespeichert.
- `ORGANIZER` und wiederholte `ATTENDEE`-Properties werden einschließlich
  `CN`, `ROLE`, `PARTSTAT` und `RSVP` gespeichert (Abschnitte 3.8.4.1,
  3.8.4.3, 3.2.12, 3.2.9 und 3.2.17).

### CalDAV – RFC 4791

Quelle: [RFC 4791](https://www.rfc-editor.org/rfc/rfc4791.html).

- `DAV: calendar-access` wird in `OPTIONS` angekündigt (Abschnitt 3).
- Collections tragen Ressourcentyp, Anzeigename, Beschreibung, Zeitzonen-ID,
  `text/calendar;version=2.0` und die Komponente `VEVENT` (Abschnitte 4.2 und
  5.2.3–5.2.7).
- Kalenderressourcen liefern starke `DAV:getetag`-/HTTP-`ETag`-Werte
  (Abschnitte 5.3.4 und 6.3.2). `If-Match` verhindert verlorene Änderungen;
  `If-None-Match: *` verhindert Überschreiben bei Neuanlage.
- `calendar-query` und `calendar-multiget` liefern ETag und Kalenderdaten
  (Abschnitte 7.8 und 7.9). Multiget ist auf 500 Hrefs begrenzt.
- `MKCALENDAR` legt Collections mit Anzeigename, Beschreibung und IANA-Zeitzone
  an (Abschnitt 5.3.1). Leere eigene Zusatzkalender können per DAV gelöscht
  werden; Standardkalender und nicht leere Kalender sind geschützt.
- `calendar-query` wertet `time-range` für einzelne VEVENTs aus
  (Abschnitte 7.8.5 und 9.9). Die Grenzen werden als UTC-Werte geprüft.
- Eine Collection enthält nur VEVENT-Ressourcen; UIDs sind darin eindeutig
  (Abschnitt 4.1). Kollisionen werden mit HTTP 409 abgewiesen.

### WebDAV Sync – RFC 6578

Quelle: [RFC 6578](https://www.rfc-editor.org/rfc/rfc6578.html).

- `DAV: sync-collection` wird angekündigt; Collections liefern eine geschützte
  `sync-token`-Property (Abschnitte 3 und 3.2).
- Der Token ist eine opaque URI. Ein leerer Token liefert den Anfangsbestand;
  spätere Tokens nur Änderungen (Abschnitt 3.2.1).
- Jede erfolgreiche Antwort ist `207 Multi-Status` und enthält genau einen neuen
  Token. Gelöschte Ressourcen erscheinen als `404` ohne `propstat`
  (Abschnitt 3.1).
- Ungültige, zukünftige oder aus dem Journal gefallene Tokens liefern
  `403 valid-sync-token`. Das Journal hält die letzten 1000 Änderungen je
  Kalender; danach ist eine vollständige Neusynchronisation nötig.

### Service Discovery – RFC 6764

Quelle: [RFC 6764](https://www.rfc-editor.org/rfc/rfc6764.html).

- `/.well-known/caldav` leitet mit HTTP 307 auf CalDAV um (Abschnitt 5).
- Principal und `calendar-home-set` sind authentifiziert auffindbar
  (Abschnitt 6). Fremde Benutzerpfade antworten mit 404.
- Produktion muss HTTPS verwenden; es gibt keine unsichere Zertifikatsumgehung
  (Abschnitte 4 und 11).

## Bedienung und Konfiguration

1. In **Kalender → Mehrere Kalender und CalDAV** Kalender mit Farbe,
   Beschreibung und IANA-Zeitzone anlegen.
2. Der Eigentümer vergibt pro Benutzer **Nur lesen** oder **Bearbeiten**.
3. Ein separates CalDAV-App-Passwort mit mindestens zwölf Zeichen setzen. Es ist
   weder das Anmelde- noch das CardDAV-Passwort.
4. In Thunderbird die angezeigte Serveradresse eintragen. Discovery ermittelt
   Principal, Home-Set und lesbare Collections.

Beim Anlegen oder Bearbeiten eines Termins steht jeder beschreibbare Kalender
zur Auswahl. Farben markieren Monatsansicht und Terminliste; lokale Filter
blenden Kalender ohne Datenübertragung ein oder aus. Teilnehmer werden als
`E-Mail | Name | Rolle | Status | RSVP` gepflegt.

Es werden keine externen Dienste oder Repository-Geheimnisse benötigt. Hinter
einem Reverse Proxy muss die vorhandene Proxy-Konfiguration externe HTTPS-URLs
korrekt erzeugen.

## Rechte, Sicherheit und Datenschutz

- CalDAV ist standardmäßig inaktiv. App-Passwörter werden mit `scrypt` und
  individuellem Zufallssalz gehasht.
- Benutzer sehen nur eigene oder explizit freigegebene Kalender. Lesen und
  Schreiben werden getrennt geprüft; nur Eigentümer ändern Freigaben.
- Jeder Benutzer besitzt einen getrennten logischen `default`-Kalender mit
  eigenem Sync-Token und Journal. Ressourcennamen, UIDs und Revisionen anderer
  Benutzer werden weder sichtbar noch für den eigenen Token gezählt.
- XML und ICS sind auf 1 MiB begrenzt. XML wird ohne externe Entitäten geparst;
  Ressourcennamen sind auf sichere Zeichen begrenzt.
- Anlage, Änderung, Löschung, Freigabe und Aktivierung werden benutzerbezogen in
  der Git-basierten RevisionHistory festgehalten. Löschen erzeugt Tombstones.
- Daten gehen nur an den konfigurierten Client. Es gibt keine automatische
  Cloud-Übertragung, Freigabe oder Rechteausweitung.

## Konflikt-, Fehler- und Ausfallverhalten

- Veraltete ETags führen zu HTTP 412; doppelte UIDs zu HTTP 409. Fehlerhafte
  ICS-, XML- und Zeitzonenwerte führen zu HTTP 400.
- Atomare JSON-Schreibvorgänge und dieselbe exklusive Kalendersperre schützen
  Ereignisdaten und Sync-Journal gegen verlorene parallele Änderungen.
- Eingebettete Git-Audit-Repositories starten keine losgelöste automatische
  Wartung während einer Schreibaktion. Dadurch konkurrieren Shutdown, Backup
  oder Restore nicht mit einem Hintergrundprozess im Objektverzeichnis;
  administrative Git-Wartung bleibt weiterhin manuell möglich.
- Nach abgelaufenem Sync-Token fordert `valid-sync-token` eine vollständige
  Neusynchronisation an. Ein Kalender kann erst ohne aktive Termine gelöscht
  werden; der Standardkalender nie.

## Rückwärtskompatibilität, Deaktivierung und Rückkehr

Es gibt keine destruktive Migration. Alte Termine ohne Collection-ID gehören
logisch zu `default`. Metadaten liegen separat in
`.simpleoffice-meta/calendar-collections.json`, Zugangsdaten in
`caldav-auth.json`. Zur Deaktivierung wird das Konto im Client entfernt oder die
Kontokonfiguration administrativ deaktiviert. Ein Code-Rollback lässt alte
Termine funktionieren; zusätzliche Collections bleiben in `calendar.json`
erhalten, auch wenn ältere Versionen sie gemeinsam darstellen.

## Tests

Automatisiert geprüft werden Discovery, Authentifizierung, Abschirmung fremder
Benutzer, Collection-Metadaten, mehrere Kalender, Lese-/Schreibrechte, sichere
Löschung, GET/PUT/DELETE, starke ETags, Create-only, UID-Eindeutigkeit, genau ein
VEVENT, IANA-Zeitzonen, unbekannte TZID, Query, Multiget, initiale und
inkrementelle Synchronisation, Tombstones, ungültige Tokens und atomare
Konfliktprüfung. Ergänzend werden Benutzertrennung der Standardkalender,
`MKCALENDAR`, Zeitbereichsfilter, Organizer/Teilnehmer, Teilnahmezustände,
Kalenderwechsel mit beiden Sync-Journal-Einträgen und Teilnehmer-Audit geprüft.
Zusätzlich läuft die vollständige bestehende Testsuite.

## Serientermine

RRULE-, RDATE-, EXDATE- und RECURRENCE-ID-Verarbeitung einschließlich
CalDAV-Zeitbereichsabfragen ist in
[Serientermine, Ausnahmen und Zeitzonen nach RFC 5545](KALENDER_SERIEN_RFC5545.md)
dokumentiert.

## Bewusste Grenzen

- `VTODO`, `VJOURNAL`, `VFREEBUSY` und vollständiges Scheduling/iTIP für
  Serieninstanzen sind noch nicht implementiert.
- Zeitbereichsfilter expandieren den dokumentierten RRULE-Teilumfang und
  berücksichtigen `RDATE`, `EXDATE` sowie einzelne `RECURRENCE-ID`-Ausnahmen.
- Das Sync-Journal ist auf 1000 Einträge begrenzt. Termin-Tombstones und
  Audit-Historie bleiben davon unberührt.
- Rohe ICS-Daten bleiben für verlustarmen CalDAV-Roundtrip erhalten.
