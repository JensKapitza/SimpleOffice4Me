# iTIP-Terminplanung nach RFC 5546

## Zweck und Nutzen

SimpleOffice kann iCalendar-Termineinladungen jetzt kontrolliert empfangen,
prüfen, anwenden, ablehnen und erzeugen. Externe Dateien verändern einen
Kalender niemals beim Hochladen: Sie landen zuerst in einem persönlichen
Posteingang und benötigen eine ausdrückliche Entscheidung. Dadurch bleiben
Einladungen aus Thunderbird, Google Kalender oder E-Mail nachvollziehbar und
manipulierte oder veraltete Nachrichten überschreiben keine Termine.

Unterstützt werden die iTIP-Methoden `REQUEST`, `REPLY`, `CANCEL`, `COUNTER`
und `DECLINECOUNTER`. Änderungen erzeugen dieselbe Kalender- und Feldhistorie
wie Bedienaktionen und aktualisieren das CalDAV-Sync-Journal.

## Bedienung

1. In **Dokumente > Kalender** unter **Termineinladungen nach iTIP** eine
   `.ics`-Datei auswählen und hochladen.
2. Die geprüfte Nachricht im persönlichen Posteingang kontrollieren.
3. Bei `REQUEST` den Zielkalender wählen und **Anwenden** drücken oder die
   Nachricht mit optionaler Begründung ablehnen.
4. Aus einem Termin heraus können Eigentümer `REQUEST` und `CANCEL` erzeugen;
   ein Teilnehmer kann seine eigene `REPLY` erzeugen, wenn seine Adresse der
   verifizierten E-Mail des angemeldeten Kontos entspricht.

Der Import akzeptiert höchstens eine `VEVENT`-Komponente und maximal 1 MiB.
`COUNTER` verschiebt einen Termin nicht automatisch, sondern hält den Vorschlag
zur manuellen Entscheidung fest.

## Auswertung der Primärstandards

Maßgeblich sind [RFC 5546 (iTIP)](https://www.rfc-editor.org/rfc/rfc5546.html),
[RFC 5545 (iCalendar)](https://www.rfc-editor.org/rfc/rfc5545.html),
[RFC 6047 (iMIP)](https://www.rfc-editor.org/rfc/rfc6047.html) und
[RFC 6638 (CalDAV Scheduling)](https://www.rfc-editor.org/rfc/rfc6638.html).

| Anforderung | Einordnung | Umsetzung |
| --- | --- | --- |
| `METHOD` muss zur transportierten Methode passen und eine Nachricht soll genau den vorgesehenen Komponententyp enthalten ([RFC 5546, Abschnitt 3.1.1](https://www.rfc-editor.org/rfc/rfc5546.html#section-3.1.1)). | MUST | Genau eine `VEVENT`-Komponente und eine unterstützte `METHOD` werden verlangt. |
| Der Organizer verwaltet die maßgebliche Terminfassung ([RFC 5546, Abschnitt 2.1](https://www.rfc-editor.org/rfc/rfc5546.html#section-2.1)). | MUST | Ein bestehender Organizer darf durch einen späteren Import nicht ersetzt werden. |
| `REQUEST` enthält Organizer und Teilnehmer ([RFC 5546, Abschnitt 3.2.2](https://www.rfc-editor.org/rfc/rfc5546.html#section-3.2.2)). | MUST | Nachrichten ohne beide Rollen werden abgewiesen. |
| `REPLY` enthält genau einen antwortenden Teilnehmer ([RFC 5546, Abschnitt 3.2.3](https://www.rfc-editor.org/rfc/rfc5546.html#section-3.2.3)). | MUST | Nur ein bereits am Termin geführter Teilnehmer darf seinen `PARTSTAT` ändern. |
| `CANCEL` bezeichnet den abzusagenden Termin über UID und Organizer ([RFC 5546, Abschnitt 3.2.5](https://www.rfc-editor.org/rfc/rfc5546.html#section-3.2.5)). | MUST | Die Absage erhält den Termin samt Historie und setzt den Status auf `CANCELLED`. |
| Empfänger dürfen eine niedrigere `SEQUENCE` nicht als neueren Stand anwenden ([RFC 5546, Abschnitte 2.1.4 und 2.1.5](https://www.rfc-editor.org/rfc/rfc5546.html#section-2.1.4)). | MUST | Downgrades werden als Konflikt abgewiesen; identische Uploads sind per SHA-256 idempotent. |
| `COUNTER` ist ein Gegenvorschlag, keine einseitige Terminänderung ([RFC 5546, Abschnitt 3.2.7](https://www.rfc-editor.org/rfc/rfc5546.html#section-3.2.7)). | MUST | Der Vorschlag wird auditierbar gespeichert, aber nicht automatisch übernommen. |
| Kalenderdaten aus nicht vertrauenswürdigen Quellen sind vor Spoofing, Wiederholung und unerwünschter Offenlegung zu schützen ([RFC 5546, Abschnitte 6.1 und 6.2](https://www.rfc-editor.org/rfc/rfc5546.html#section-6.1)). | SHOULD | Quarantäne, Größenlimit, Rollenprüfung, Replay-Schutz, Benutzertrennung und explizite Freigabe sind aktiv. |
| `UID` identifiziert das Ereignis dauerhaft; Datumswerte müssen gültig sein ([RFC 5545, Abschnitte 3.8.4.7 und 3.3.5](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.4.7)). | MUST | Der bestehende strikte ICS-Parser normalisiert UID, UTC und Zeitzonen vor der Verarbeitung. |
| Scheduling kann über CalDAV-Inbox und -Outbox transportiert werden ([RFC 6638, Abschnitte 2 und 3](https://www.rfc-editor.org/rfc/rfc6638.html#section-2)). | MAY | Lokale, explizit freigegebene Zustellung sowie private Inbox/Outbox-Collections sind implementiert; Details stehen in [CALDAV_SCHEDULING_RFC6638.md](CALDAV_SCHEDULING_RFC6638.md). |

## Abgeleitete Architekturentscheidungen

- Ein Dateiupload beweist die Identität von Organizer oder Teilnehmer nicht.
  Deshalb wird jede externe Nachricht zunächst `pending` gespeichert.
- Anwenden, Ablehnen und Erzeugen laufen unter dem angemeldeten Benutzer und
  werden mit Zeitpunkt, Methode, UID, Sequenz und Ergebnis protokolliert.
- UID-Suche, Sequenzprüfung, Änderung und Sync-Token-Aktualisierung erfolgen
  unter derselben Kalendersperre. Gleichzeitige Importe verlieren keine
  zwischenzeitlichen Änderungen.
- `REPLY` darf nur den bekannten Teilnehmerstatus ändern. Eine Antwort kann
  weder neue Teilnehmer noch einen neuen Organizer einschleusen.
- `CANCEL` löscht nicht. Status und frühere Werte bleiben in der Audit-Historie
  erhalten und Buchungszeiten werden durch den abgesagten Zustand freigegeben.

## Konfiguration und Voraussetzungen

Es gibt keine neue Pflichtkonfiguration und keinen externen Dienst. Benötigt
werden nur die bestehende Anmeldung und ein Kalender. Der Kalender-Endpunkt
läuft hinter denselben HTTPS-, Sitzungs- und Proxy-Einstellungen wie die
übrige Anwendung. Zugangsdaten oder Schlüssel werden nicht im Repository
gespeichert.

## Rechte, Sicherheit und Datenschutz

- Posteingang, Nachrichten und Prüfsummen sind strikt pro Benutzer getrennt.
- Ein Anwender kann nur in einen Kalender schreiben, für den er Schreibrechte
  besitzt. Der Eigentümer bleibt für Organizer-Aktionen maßgeblich.
- Teilnehmerantworten werden nur für die verifizierte Konto-E-Mail angeboten;
  eine Antwort im Namen eines anderen Teilnehmers wird serverseitig abgewiesen.
- Unbekannte Teilnehmer, Organizer-Wechsel, veraltete Sequenzen, wiederholtes
  Anwenden und nicht unterstützte Methoden werden abgewiesen.
- Importierte Inhalte werden nicht an externe Dienste übertragen und lösen
  keine automatische E-Mail oder Kalenderfreigabe aus.
- Die 1-MiB-Grenze und genau eine Ereigniskomponente begrenzen Speicher- und
  Parsermissbrauch. Rohinhalte werden nicht in Listenansichten ausgegeben.
- iTIP selbst authentifiziert den Absender nicht. Vor einem produktiven
  E-Mail-Transport sind signierte Nachrichten oder eine vertrauenswürdige
  Transportauthentisierung erforderlich.

## Format- und Protokollkompatibilität

Ein- und Ausgabe verwenden iCalendar 2.0 mit `METHOD`, `UID`, `SEQUENCE`,
`ORGANIZER`, `ATTENDEE`, `PARTSTAT`, UTC-Zeitwerten und vorhandenen
Zeitzoneninformationen. Thunderbird und Google Kalender können solche Dateien
importieren beziehungsweise exportieren. Lokales CalDAV Scheduling nach RFC
6638 ist implementiert; iMIP-Mailzustellung nach RFC 6047 bleibt offen.

## Fehler- und Konfliktverhalten

Ungültige Dateien, fehlende Rollen, zu große Nachrichten und nicht
unterstützte Methoden erzeugen eine verständliche Fehlermeldung ohne
Kalenderänderung. Fachliche Konflikte bleiben im Posteingang sichtbar und
können abgelehnt werden. Bereits angewandte oder abgelehnte Nachrichten sind
unveränderlich. Ein fehlgeschlagener Schreibvorgang erhöht den CalDAV-Sync-Token
nicht.

## Migration und Rückwärtskompatibilität

Es gibt keine destruktive Datenmigration. Der iTIP-Speicher wird bei Bedarf
angelegt; bestehende Kalender, ICS-Dateien, Freigaben und CalDAV-URLs bleiben
unverändert. Ältere Installationen können den neuen Speicher ignorieren.

## Tests

Automatisiert geprüft werden Quarantäne und explizites Anwenden,
Idempotenz, Sequenz-Downgrade, Organizer-Spoofing, Absagehistorie,
Teilnehmerantworten, unbekannte Teilnehmer, Replay-Schutz, `COUNTER` ohne
automatische Verschiebung, Ablehnung ohne Kalenderänderung, Größen- und
Formatgrenzen, Rollen beim Export sowie der vollständige Webablauf.

## Bekannte Grenzen und offene Entscheidungen

- Serieninstanzen mit `RECURRENCE-ID`, Delegation, `ADD`, `REFRESH`, `VTODO`
  und `VFREEBUSY` werden noch nicht verarbeitet.
- Gegenvorschläge werden bewusst nicht automatisch angenommen.
- Es gibt noch keine kryptografische Absenderprüfung und keine automatische
  iMIP-E-Mailzustellung.
- RFC-6638-Inbox/Outbox und lokale Zustellung sind optional implementiert;
  Server-zu-Server- und iMIP-Transport bleiben bewusst offen.

## Deaktivierung und Rückkehr

Ohne Nutzung des Importformulars oder der iTIP-Downloadlinks bleibt das
bisherige Kalenderverhalten unverändert. Ein Rollback entfernt Routen und
Oberfläche; vorhandene Kalendertermine bleiben erhalten. Der separate
iTIP-Nachrichtenspeicher kann nach vorheriger Sicherung entfernt werden, ohne
Kalender- oder CalDAV-Daten zu verändern.
