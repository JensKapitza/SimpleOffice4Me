# Serientermine, Ausnahmen und Zeitzonen nach RFC 5545

## Zweck und Nutzen

SimpleOffice4Me kann wiederkehrende Termine als echte iCalendar-Serie speichern,
anzeigen, importieren, exportieren und über CalDAV synchronisieren. Die Anwendung
erzeugt keine Kopie je Termin: Ein Master enthält die Regel, einzelne Absagen oder
Verschiebungen bleiben als revisionssichere Ausnahmen erhalten. Damit funktionieren
Serien aus Thunderbird und Google Kalender einschließlich Sommerzeitwechseln,
Monatsdarstellung, Buchungskonflikten und CalDAV-Zeitbereichsabfragen konsistent.

## Ausgewertete Primärstandards

### iCalendar – RFC 5545

Maßgeblich ist [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545.html).

| Anforderung | Einordnung | Umsetzung |
| --- | --- | --- |
| Eine Wiederholungsmenge besteht aus `DTSTART` zusammen mit `RRULE` und `RDATE`; `EXDATE` entfernt Instanzen ([Abschnitt 3.8.5](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.5)). | MUST | Die Expansion bildet diese Mengenoperation ab, entfernt Duplikate und behandelt `DTSTART` als erste Instanz. |
| `RRULE` darf in einem Ereignis höchstens einmal vorkommen. `FREQ` ist erforderlich; `COUNT` und `UNTIL` dürfen nicht gemeinsam auftreten ([Abschnitt 3.3.10](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.3.10)). | MUST | Doppelte Teile, fehlende oder unbekannte Frequenzen und die Kombination aus `COUNT`/`UNTIL` werden abgewiesen. |
| `UNTIL` begrenzt die Reihe einschließlich des Grenzwerts und muss bei `DTSTART` mit Zeitzonenbezug als UTC angegeben sein ([Abschnitte 3.3.10 und 3.3.5](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.3.10)). | MUST | UTC-`UNTIL` wird inklusiv ausgewertet; ein mehrdeutiger lokaler Grenzwert wird abgelehnt. |
| `EXDATE` hat denselben Werttyp wie `DTSTART` und darf mehrfach vorkommen ([Abschnitt 3.8.5.1](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.5.1)). | MUST | Mehrere Zeilen und kommagetrennte Werte werden normalisiert. Pro Serie gelten höchstens 500 Werte. |
| `RDATE` ergänzt einzelne DATE- oder DATE-TIME-Werte ([Abschnitt 3.8.5.2](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.5.2)). | MUST | Zusätzliche Termine werden mit RRULE-Terminen vereinigt; doppelte Werte erzeugen keine Doppelanzeige. PERIOD-Werte werden abgewiesen. |
| `RECURRENCE-ID` benennt die ursprüngliche Instanz, auch wenn diese verschoben wurde; ihr Werttyp muss zum Master passen ([Abschnitt 3.8.4.4](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.4.4)). | MUST | Der Originalwert bleibt stabil. Neuer Beginn, Ende, Titel, Grund und Status liegen getrennt in der Ausnahme. |
| Lokale Zeitwerte einer Serie benötigen einen konsistenten `TZID`; UTC-Werte tragen `Z` ([Abschnitte 3.2.19 und 3.3.5](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.2.19)). | MUST | IANA-Zonen werden mit `zoneinfo` ausgewertet. Die lokale Uhrzeit bleibt beim Sommerzeitwechsel gleich; unbekannte TZID werden abgewiesen. |
| Implementierungen sollen ungültige oder sehr große Wiederholungsmengen nicht unbegrenzt verarbeiten ([Abschnitt 7](https://www.rfc-editor.org/rfc/rfc5545.html#section-7)). | SHOULD | Regeln, Intervalle, Anzahl, Zeitraum, RDATE/EXDATE, Ausnahmen und Ergebnisgröße besitzen feste Grenzen. |

Unterstützt werden `FREQ=DAILY`, `WEEKLY`, `MONTHLY` und `YEARLY` sowie
`INTERVAL`, `COUNT`, `UNTIL`, `BYDAY`, `BYMONTHDAY`, `BYMONTH` und `WKST`.
Numerische Wochentage wie `-1FR` werden für monatliche Regeln sowie für jährliche
Regeln mit `BYMONTH` verarbeitet; die Jahressicht ohne Monatsbezug wird eindeutig
abgewiesen. Mehrere `BY...`-Teile wirken als Schnittmenge. Nicht implementierte
Regelteile werden nicht still ignoriert, sondern
mit einer verständlichen Fehlermeldung abgewiesen.

### CalDAV – RFC 4791

Maßgeblich ist [RFC 4791](https://www.rfc-editor.org/rfc/rfc4791.html).

| Anforderung | Einordnung | Umsetzung |
| --- | --- | --- |
| Eine Kalenderobjekt-Ressource darf Master und Ausnahmen derselben UID enthalten ([Abschnitt 4.1](https://www.rfc-editor.org/rfc/rfc4791.html#section-4.1)). | MUST | CalDAV akzeptiert genau einen Master und bis zu 500 `RECURRENCE-ID`-Komponenten mit identischer UID. |
| `calendar-query` muss bei `time-range` die für den Komponententyp definierte Überlappung berücksichtigen ([Abschnitte 7.8 und 9.9](https://www.rfc-editor.org/rfc/rfc4791.html#section-9.9)). | MUST | Eine Serie wird geliefert, wenn mindestens eine aktive, verschobene oder per RDATE ergänzte Instanz den halboffenen Bereich überlappt. EXDATE und abgesagte Ausnahmen zählen nicht. |
| Geänderte Ressourcen müssen über ETag und Synchronisation erkennbar werden ([Abschnitte 5.3.4 und 6.3.2](https://www.rfc-editor.org/rfc/rfc4791.html#section-5.3.4)). | MUST | Webänderungen an Master oder Ausnahme erhöhen den Collection-Sync-Token; vorhandene ETag-Konfliktprüfung bleibt aktiv. |

## Datenmodell und Designentscheidungen

Ein Ereignis erhält additiv:

```json
{
  "timezone": "Europe/Berlin",
  "recurrence": {
    "rrule": "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10",
    "rdates": ["2026-09-01T07:00+00:00"],
    "exdates": ["2026-08-10T07:00+00:00"],
    "timezone": "Europe/Berlin"
  },
  "recurrence_overrides": [
    {
      "recurrence_id": "2026-08-17T07:00+00:00",
      "status": "active",
      "start": "2026-08-18T14:00+02:00",
      "end": "2026-08-18T15:00+02:00"
    }
  ]
}
```

Der Master bleibt die Rechte-, Kalender- und Audit-Einheit. Eine Instanz kann
keine weitergehenden Rechte als ihre Serie erhalten. Änderungen speichern alten
und neuen Serien- beziehungsweise Ausnahmezustand in der Feldhistorie und einen
vollständigen Snapshot in der Git-basierten RevisionHistory. Ein optimistischer
Zeitstempel verhindert, dass zwei Webbearbeiter eine zwischenzeitliche Änderung
unbemerkt überschreiben. CalDAV behält zusätzlich starke ETags.

Die Expansion erfolgt nur für den angefragten Zeitraum und verändert keine
gespeicherten Daten. Serien bleiben dadurch auch über viele Jahre kompakt. Die
gleiche Engine wird für Monatsansicht, Buchungskonflikte und CalDAV-Reports
verwendet, sodass keine voneinander abweichenden Terminmengen entstehen.

## Bedienung und Konfiguration

Beim Anlegen eines Termins kann direkt eine Serienregel eingegeben werden:

```text
FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10
```

Die IANA-Zeitzone steht standardmäßig auf `Europe/Berlin`. Ein leerer Regelwert
erzeugt weiterhin einen Einzeltermin. Weitere Beispiele:

- täglich für fünf Tage: `FREQ=DAILY;COUNT=5`
- alle zwei Wochen montags: `FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;COUNT=12`
- letzter Freitag im Monat: `FREQ=MONTHLY;BYDAY=-1FR;COUNT=12`
- jährlich am 10. Mai: `FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=10;COUNT=5`

Im Terminfenster stehen RRULE, Zeitzone, zusätzliche RDATE- und auszunehmende
EXDATE-Werte zur Verfügung. RDATE/EXDATE werden als ISO-Zeitwerte mit einer Zeile
pro Wert eingegeben. Leere RRULE und leere RDATE beenden die Serie und entfernen
deren Ausnahmen ausdrücklich.

Eine Instanz wird im Monatskalender ausgewählt. Im Terminfenster ist die
ursprüngliche Zeit als schreibgeschützte `RECURRENCE-ID` vorausgefüllt. Die
Instanz kann verschoben, mit abweichendem Titel/Grund versehen oder abgesagt
werden. Nur diese Instanz ändert sich. Eine Absage gibt ausschließlich diesen
Buchungszeitraum frei; andere Serientermine bleiben blockiert. Die
Buchungszeitzone ist separat konfigurierbar und verwendet ebenfalls eine IANA-ID.

## Import, Export und Interoperabilität

- Der Dateiimport gruppiert bis zu 200 Serien anhand ihrer UID und akzeptiert
  Master plus Ausnahmen. Neue Importe bleiben privat und benutzerisoliert.
- Eine alleinstehende `RECURRENCE-ID` mit `STATUS:CANCELLED` kann eine bekannte
  eigene Importserie absagen. Unbekannte UID werden nicht angelegt.
- Der Export schreibt RRULE, RDATE, EXDATE und RECURRENCE-ID-Komponenten. UTC
  wird mit `Z` ausgegeben.
- CalDAV erhält rohe Clientdaten für verlustarmen Roundtrip. Webserien werden
  standardkonform generiert und über das Sync-Journal angekündigt.
- Getestet sind typische Thunderbird-/Google-Strukturen mit mehreren VEVENTs,
  IANA-Zeitzone, DST-Wechsel, verschobener und abgesagter Instanz.

Es ist kein externer Dienst und keine neue Zugangskonfiguration erforderlich.

## Rechte, Sicherheit und Datenschutz

- Lesen und Bearbeiten folgen den bestehenden Termin- und Kalenderrechten.
  Nur Eigentümer oder Benutzer mit Bearbeitungsrecht ändern Regel oder Instanz.
- UID-Abgleich beim Dateiimport bleibt auf den importierenden Benutzer begrenzt.
- Import und CalDAV sind auf 1 MiB begrenzt. Maximal gelten 200 Importserien,
  500 Ausnahmen, 500 RDATE, 500 EXDATE, `COUNT=10000`, 20 Jahre Scanbereich und
  2000 ausgegebene Instanzen je Ereignis und Anfrage.
- Keine Kalenderdaten oder Zugangsdaten werden an externe Dienste übertragen.
  Es entstehen keine automatischen Freigaben oder Einladungen.
- Fehlgeschlagene Validierung schreibt weder Ereignis noch Sync-Token.

## Fehler-, Konflikt- und Ausfallverhalten

Unbekannte TZID, nicht unterstützte RRULE-Teile, doppelte Regelteile, ungültige
Grenzen, verschiedene UIDs in einer Ressource und `RANGE=THISANDFUTURE` liefern
einen Fehler ohne Teilimport. Veraltete Webstände werden als gleichzeitige
Änderung gemeldet; CalDAV liefert weiterhin HTTP 412 bei veraltetem ETag.
Atomare JSON-Schreibvorgänge und die gemeinsame Kalendersperre schützen Ereignis
und Sync-Journal. Eine zu große Expansion wird abgebrochen statt CPU oder Speicher
unbegrenzt zu belegen.

## Migration und Rückwärtskompatibilität

Es gibt keine Migration. Alte Einzeltermine besitzen keine `recurrence`-Felder
und verhalten sich unverändert. Ältere Versionen ignorieren die additiven Felder,
zeigen aber nur den Master. Bestehende Kalender-IDs, Rechte, UIDs, URLs,
Aufbewahrungsregeln und App-Passwörter bleiben unverändert.

## Tests

Automatisiert geprüft werden:

- täglich, wöchentlich, monatlich und jährlich; Intervall, COUNT, UNTIL,
  BYDAY, BYMONTHDAY, BYMONTH und WKST,
- positive und negative RDATE-/EXDATE-Mengen sowie Größenlimits,
- Sommerzeitstabilität in `Europe/Berlin`, UTC und unbekannte TZID,
- Verschiebung und Absage einer Instanz bei unverändertem Master,
- Lese-/Bearbeitungsrechte, veralteter Webstand und vollständiger Audit-Snapshot,
- Buchungskollision und gezielte Freigabe einer abgesagten Instanz,
- Thunderbird-Import, Benutzertrennung und ICS-Roundtrip,
- CalDAV-GET/PUT, gleiche UID, starke ETags, Sync-Token und `time-range`,
- Webanlage, Monatsdarstellung und Instanzbearbeitung.

## Bewusste Grenzen

- Nicht implementiert sind `SECONDLY`, `MINUTELY`, `HOURLY`, `BYYEARDAY`,
  `BYWEEKNO`, `BYHOUR`, `BYMINUTE`, `BYSECOND`, `BYSETPOS`, RDATE-PERIOD und
  `RECURRENCE-ID;RANGE=THISANDFUTURE`. Sie werden explizit abgewiesen.
- Benutzerdefinierte eingebettete `VTIMEZONE`-Definitionen ohne bekannte IANA-TZID
  werden nicht ausgeführt. Das verhindert mehrdeutige oder manipulierte Regeln,
  kann aber proprietäre Altdateien ablehnen.
- Eine Serienregel wird als technische RFC-Regel eingegeben. Ein grafischer
  Assistent für alle Kombinationen ist noch offen.
- iTIP-Seriennachrichten mit Teilabsagen bleiben ein eigener Ausbau; normaler
  ICS-Import und CalDAV verarbeiten die Ausnahmen bereits.

## Deaktivierung und Rückkehr

Ohne RRULE/RDATE bleibt das bisherige Einzelterminverhalten aktiv. Eine Serie kann
im Terminfenster durch Leeren von RRULE und RDATE beendet werden. Ein Code-Rollback
verändert keine Daten; ältere Versionen zeigen den Mastertermin. Vor einem
dauerhaften Rollback sollten Serien als ICS exportiert werden, damit alle
Ausnahmen außerhalb von SimpleOffice vollständig erhalten bleiben.
