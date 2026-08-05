# Terminmetadaten und Konferenzzugänge nach RFC 5545/7986

## Zweck und Nutzen

SimpleOffice4Me speichert Status, Zeitbelegung, Klassifizierung, Priorität, Ort,
Ressourcen, Termin-URL und Konferenzzugänge durchgängig in Weboberfläche,
Audit-Historie, ICS und CalDAV. Thunderbird, Google-ICS und andere
iCalendar-Clients erhalten damit dieselbe fachliche Bedeutung. Die Erweiterung
ändert keine Benutzer-, Kalender- oder Ereignisfreigabe.

## Primäre Standards und Anforderungen

| Norm | MUST/SHOULD/MAY und Designentscheidung | Umsetzung |
|---|---|---|
| [RFC 5545 §3.6.1](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.6.1) | VEVENT legt Eigenschaften und Häufigkeiten fest. | Pro Ressource genau ein Master; Einzelwerte werden eindeutig validiert. |
| [RFC 5545 §3.8.1.2](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.1.2) | `CATEGORIES` MAY als TEXT-Liste vorkommen. | Vorhandene Tags werden exportiert und beim Import privat angelegt. |
| [RFC 5545 §3.8.1.3](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.1.3) | `CLASS` MAY `PUBLIC`, `PRIVATE` oder `CONFIDENTIAL` sein. | Alle Werte werden geprüft; Standard ist `PRIVATE`. |
| [RFC 5545 §3.8.1.7](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.1.7) | `LOCATION` MAY beschreibenden TEXT enthalten. | Ort bis 500 Zeichen, in ICS korrekt escaped. |
| [RFC 5545 §3.8.1.9](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.1.9) | `PRIORITY` MAY eine Ganzzahl 0–9 sein. | Werte außerhalb 0–9 werden atomar abgewiesen. |
| [RFC 5545 §3.8.1.10](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.1.10) | `RESOURCES` MAY eine TEXT-Liste sein. | Höchstens 32 eindeutige Ressourcen. |
| [RFC 5545 §3.8.1.11](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.1.11) | VEVENT-Status MAY `TENTATIVE`, `CONFIRMED`, `CANCELLED` sein. | Anzeige, Validierung und Roundtrip; abgesagte Termine blockieren keine Buchung. |
| [RFC 5545 §3.8.2.7](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.2.7) | `TRANSP` steuert, ob ein Ereignis Zeit belegt. | `TRANSPARENT` erscheint nicht in Buchungskonflikten oder Free/Busy. |
| [RFC 5545 §3.8.4.6](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.4.6) | `URL` MAY auf Ereignisinformationen verweisen. | Sichere URI-Schemata werden rundübertragen. |
| [RFC 7986 §5.11](https://www.rfc-editor.org/rfc/rfc7986.html#section-5.11) | `CONFERENCE` ist eine URI; `FEATURE` und `LABEL` beschreiben Zugang und Funktion. | Bis zu acht eindeutige Zugänge mit standardisierten Merkmalen. |
| [RFC 7986 §7](https://www.rfc-editor.org/rfc/rfc7986.html#section-7) | Implementierungen SHOULD externe Inhalte sicher behandeln. | Kein serverseitiger Abruf; unsichere Schemata und URL-Zugangsdaten werden abgewiesen. |

## Design und implementierte Konformität

- Der interne Lebenszyklus (`active`, `pending`, `deleted`) bleibt vom
  interoperablen `ical_status` getrennt. Bestehende Buchungs- und
  Aufbewahrungsabläufe bleiben kompatibel.
- `TRANSPARENT` und `CANCELLED` geben Zeit in lokaler Buchungsprüfung und
  RFC-6638-Free/Busy frei.
- `CLASS` ist nur Austauschmetadatum. Sichtbarkeit und Bearbeitungsrechte
  folgen weiterhin `visibility`, Eigentümer und `access`.
- URI-Werte erlauben ausschließlich `https`, `http`, `mailto` und
  `tel`. HTTP-URLs mit eingebettetem Benutzer oder Passwort sind verboten.
- Konferenzlinks werden nie automatisch geöffnet, geprüft oder an Dritte
  gemeldet. Erst ein bewusster Klick startet die lokal registrierte App.
- Import und CalDAV-PUT sind atomar: Ein ungültiger Wert verwirft die gesamte
  Ressource und verändert weder Daten noch Sync-Token.

## Bedienung

Beim Anlegen oder Bearbeiten erscheint **Terminstatus und Interoperabilität**:

- Status: bestätigt, vorläufig oder abgesagt
- Zeitbelegung: belegt oder frei/transparent
- Klassifizierung: privat, vertraulich oder öffentlich
- Priorität 0 bis 9, Ort, Termin-URL und Ressourcen
- Konferenzen, eine Zeile je Zugang: `URI | Bezeichnung | Merkmale`

Beispiel:

```text
https://meet.example/raum-4 | Teamvideo | audio,video,chat
tel:+491234567 | Telefoneinwahl | phone
```

Abgesagte Termine werden durchgestrichen, vorläufige kursiv angezeigt.
Konferenzzugänge erscheinen im Termindialog als Schaltfläche.

## Konfiguration und Voraussetzungen

Es sind keine zusätzliche Serverkonfiguration, API und kein Geheimnis
erforderlich. CalDAV benötigt weiterhin HTTPS und ein separates App-Passwort.
Eine passende Konferenz- oder Telefon-App muss nur clientseitig für das
jeweilige URI-Schema registriert sein.

## Sicherheit, Datenschutz, Rechte und Freigaben

- Nur Eigentümer oder Benutzer mit Ereignis-Bearbeitungsrecht ändern Metadaten.
- Leser sehen sie nur, wenn sie den Termin bereits sehen dürfen.
- `CLASS:PUBLIC` erzeugt weder Freigabe noch öffentlichen Link.
- Änderungen werden feldgenau mit Altwert, Neuwert, Benutzer und Zeitpunkt
  sowie als vollständiger Git-Audit-Snapshot gespeichert.
- RFC-6638-Teilnehmer dürfen weiterhin nur den eigenen `PARTSTAT` ändern;
  Organizer-Metadaten sind geschützt.
- Free/Busy gibt ausschließlich Zeiträume aus, niemals Ort, URL, Ressourcen,
  Kategorien oder Konferenzdaten.

## Formate und Protokollkompatibilität

Unterstützt sind ICS-Import/-Export, CalDAV `PUT`/`GET`,
`calendar-query`, `calendar-multiget` und Sync-Token. Die Eigenschaften
bleiben zusammen mit Serien, Ausnahmen, Teilnehmern und `VALARM` erhalten.
SimpleOffice4Me erzeugt kanonische Einzelwerte und mehrere
`CONFERENCE`-Zeilen. Proprietäre Google- oder Microsoft-Felder sind nicht
erforderlich.

## Fehler- und Ausfallverhalten

- Webfehler werden verständlich angezeigt; CalDAV liefert HTTP 400.
- ETag und Schedule-Tag bleiben aktiv; veraltete Schreibversuche liefern 412.
- Ein Validierungsfehler schreibt keine Kalenderdaten.
- Ein ausgefallener Konferenzdienst beeinflusst SimpleOffice4Me nicht, weil
  Links nie im Hintergrund abgerufen werden.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenmigration. Alte Termine gelten als bestätigt, belegt,
privat und Priorität 0. Ein Rollback lässt neue JSON-Felder bestehen; ältere
Versionen ignorieren sie. Vor einem dauerhaften Rollback kann ICS als
interoperable Sicherung exportiert werden.

## Tests

Automatisiert geprüft werden sichere Standards, Enumerationen, Längen,
Prioritäten, Duplikate, gefährliche URI-Schemata und eingebettete
Zugangsdaten, Bearbeitungsrechte, feldgenaue Historie, Freigabe transparenter
und abgesagter Buchungszeiten, RFC-Import/Export, CalDAV-Roundtrip und
Webanlage einschließlich Konferenzzugängen.

## Bewusst nicht implementierte Teile und Grenzen

- Sprachparameter, `ALTREP`, GEO und RFC-9073-Strukturlocations
- Erreichbarkeits- oder Vertrauensprüfung externer Konferenz-URIs
- getrennte Sichtbarkeit einzelner Konferenzlinks
- automatische Abbildung von `CLASS` auf SimpleOffice-Rechte

Diese Teile werden nicht stillschweigend angenähert. Unbekannte
Konferenzmerkmale und unsichere URIs werden abgewiesen.

## Deaktivierung und Rückkehr

Die Funktion benötigt keinen Hintergrunddienst. Felder können je Termin auf
Standardwerte zurückgesetzt und Links/Ressourcen gelöscht werden. Ein
Code-Rollback benötigt keine Datenänderung. Vor einem Rollback sollte
`TRANSPARENT` auf `OPAQUE` gesetzt werden, wenn ältere Versionen diese
Termine wieder als belegt behandeln sollen.
