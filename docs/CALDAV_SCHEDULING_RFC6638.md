# CalDAV Scheduling und Free/Busy nach RFC 6638

## Zweck und Nutzen

SimpleOffice erweitert CalDAV um eine lokale, kontrollierte Terminplanung.
Thunderbird und andere CalDAV-Clients können Scheduling-Inbox und -Outbox
erkennen, Einladungen und Antworten zwischen lokalen Benutzern zustellen sowie
belegte Zeiträume abfragen. Alle Funktionen sind standardmäßig deaktiviert und
werden pro Benutzer und Gegenstelle ausdrücklich freigegeben.

Die Scheduling-Inbox verwendet denselben quarantänisierten iTIP-Speicher wie
die Kalenderoberfläche. Eine zugestellte Einladung verändert daher noch keinen
Kalender. Erst die ausdrückliche Aktion **Anwenden** übernimmt sie.

## Bedienung und Konfiguration

1. Zuerst CalDAV im Kalender mit einem separaten App-Passwort aktivieren.
2. Unter **CalDAV-Terminplanung und Verfügbarkeit** Scheduling einschalten.
3. Für jeden lokalen Benutzer getrennt festlegen:
   - **Einladungen und Antworten** erlaubt iTIP-Zustellungen mit Termindaten.
   - **Nur Belegtzeiten** erlaubt VFREEBUSY-Abfragen ohne Titel oder Details.
4. In Thunderbird die normale CalDAV-Serveradresse verwenden. Principal,
   Scheduling-Inbox und -Outbox werden automatisch entdeckt.

Jeder Benutzer erhält eine stabile lokale Kalenderadresse nach dem Muster
`benutzer@simpleoffice.local`. Eine über Google verifizierte und eindeutige
Konto-E-Mail wird zusätzlich als Kalenderadresse angekündigt. Mehrdeutige
E-Mail-Adressen werden nicht aufgelöst.

## Auswertung des Primärstandards

Primärquelle ist
[RFC 6638 – Scheduling Extensions to CalDAV](https://www.rfc-editor.org/rfc/rfc6638.html).
Er ergänzt [RFC 4791 – CalDAV](https://www.rfc-editor.org/rfc/rfc4791.html)
und [RFC 5546 – iTIP](https://www.rfc-editor.org/rfc/rfc5546.html).

| Anforderung | Stärke | Umsetzung |
| --- | --- | --- |
| Ein Scheduling-Server muss CalDAV `calendar-access` unterstützen und bei Scheduling-Ressourcen `calendar-auto-schedule` über `DAV` ankündigen ([RFC 6638 §2](https://www.rfc-editor.org/rfc/rfc6638.html#section-2)). | REQUIRED | Die Capability erscheint ausschließlich für Konten, die Scheduling aktiviert haben. |
| Outbox und Inbox sind vom Server erzeugte Collections und dürfen nicht unterhalb einer Kalender-Collection liegen ([§2.1](https://www.rfc-editor.org/rfc/rfc6638.html#section-2.1), [§2.2](https://www.rfc-editor.org/rfc/rfc6638.html#section-2.2)). | MUST | Persönliche Pfade liegen unter `/caldav/scheduling/<user>/inbox/` und `/outbox/`; Clients können sie weder anlegen noch löschen. |
| Der `resourcetype` der Outbox muss `collection` und `schedule-outbox` enthalten ([§2.1](https://www.rfc-editor.org/rfc/rfc6638.html#section-2.1)). | MUST | PROPFIND liefert beide Elemente und `schedule-send`. |
| Der `resourcetype` der Inbox muss `collection` und `schedule-inbox` enthalten ([§2.2](https://www.rfc-editor.org/rfc/rfc6638.html#section-2.2)). | MUST | PROPFIND liefert beide Elemente und `schedule-deliver`. |
| Eine Inbox darf nur iTIP-konforme Kalenderobjekte, keine Unter-Collections enthalten; gleiche UIDs dürfen mehrfach vorkommen ([§2.2](https://www.rfc-editor.org/rfc/rfc6638.html#section-2.2)). | MUST | Der bestehende iTIP-Validator prüft jede Nachricht; jede Transaktion erhält eine eigene Nachrichten-ID. |
| `calendar-query` und `calendar-multiget` gelten auch für die Scheduling-Inbox ([§2.3](https://www.rfc-editor.org/rfc/rfc6638.html#section-2.3)). | MUST | Beide REPORT-Typen werden unterstützt, mit maximal 500 HREFs. |
| Principal-Ressourcen benötigen ein `calendar-user-address-set` zur eindeutigen Zuordnung ([§2.4.1](https://www.rfc-editor.org/rfc/rfc6638.html#section-2.4.1)). | REQUIRED | Lokale und eindeutig verifizierte Adressen werden nur am eigenen authentifizierten Principal ausgegeben. |
| `schedule-inbox-URL` und `schedule-outbox-URL` ermöglichen Client-Discovery ([§2.1.1](https://www.rfc-editor.org/rfc/rfc6638.html#section-2.1.1), [§2.2.1](https://www.rfc-editor.org/rfc/rfc6638.html#section-2.2.1)). | SHOULD | Beide geschützten Properties erscheinen nach Aktivierung. |
| Das Entfernen einer Organizer-Ressource erzeugt standardmäßig `CANCEL`; `Schedule-Reply: F` verhindert eine Nachricht ([§3.2.1.3](https://www.rfc-editor.org/rfc/rfc6638.html#section-3.2.1.3), [§8.1](https://www.rfc-editor.org/rfc/rfc6638.html#section-8.1)). | MUST | DELETE erzeugt für erlaubte lokale Empfänger `CANCEL`; `Schedule-Reply: F` unterdrückt die Zustellung. |
| Teilnehmer dürfen insbesondere den eigenen `PARTSTAT` ändern, aber keine Organizer-Felder oder fremden Teilnehmer ([§3.2.2.1](https://www.rfc-editor.org/rfc/rfc6638.html#section-3.2.2.1)). | MUST | Serververgleich erlaubt nur den eigenen Teilnahmestatus; manipulierte Kerndaten erhalten die CalDAV-Precondition `allowed-attendee-scheduling-object-change`. |
| Scheduling-Ressourcen müssen `Schedule-Tag` und `If-Schedule-Tag-Match` gegen verlorene Folgeänderungen unterstützen ([§8.2](https://www.rfc-editor.org/rfc/rfc6638.html#section-8.2), [§8.3](https://www.rfc-editor.org/rfc/rfc6638.html#section-8.3)). | MUST | GET, PUT, PROPFIND und REPORT liefern den Tag; PUT und DELETE antworten bei veraltetem Tag mit HTTP 412. |
| Free/Busy-Anfragen müssen Organizer, Senderecht und Empfangsrecht prüfen ([§5](https://www.rfc-editor.org/rfc/rfc6638.html#section-5), [§11.3](https://www.rfc-editor.org/rfc/rfc6638.html#section-11.3)). | MUST | Der Organizer muss zum authentifizierten Principal gehören; jeder Empfänger benötigt eine explizite Free/Busy-Freigabe. |
| Fehler je Empfänger werden als `schedule-response` mit iTIP-Status angegeben ([§10.1](https://www.rfc-editor.org/rfc/rfc6638.html#section-10.1)). | MUST | Erfolg nutzt `2.0`, unbekannte Kalenderbenutzer `3.7`, verweigerte Rechte `3.8`. |
| Private Daten und WebDAV-Ressourcenzustände dürfen anderen Benutzern nicht über Fehler oder Scheduling offengelegt werden ([§11.4](https://www.rfc-editor.org/rfc/rfc6638.html#section-11.4)). | MUST NOT | Fremde Principal-, Kalender- und Inbox-Pfade liefern 404; Free/Busy enthält nur zusammengeführte UTC-Zeiträume. |
| Größe und Verarbeitung müssen gegen Flooding begrenzt werden ([§11.1](https://www.rfc-editor.org/rfc/rfc6638.html#section-11.1)). | MUST | 1 MiB, höchstens 50 Free/Busy-Empfänger, 366 Tage und 500 Multiget-HREFs. |
| TLS schützt Vertraulichkeit und Integrität ([§11.5](https://www.rfc-editor.org/rfc/rfc6638.html#section-11.5)). | MUST | Für den produktiven Betrieb bleibt HTTPS am Reverse Proxy Voraussetzung. |

## Implementierter Ablauf

### Organizer

Ein CalDAV-`PUT` mit einer Organizer-Adresse des angemeldeten Benutzers wird
als Scheduling-Objekt behandelt. Für lokale Teilnehmer, die diesen Benutzer
freigegeben haben, entsteht eine `REQUEST`-Nachricht. Änderungen erzeugen eine
neue Sequenz-/Inhaltsversion; identische Nachrichten bleiben per SHA-256
idempotent. `DELETE` beziehungsweise ein abgesagter Termin erzeugt `CANCEL`.

### Teilnehmer

Bei einer fremden Organizer-Adresse muss eine eigene Kalenderadresse als
`ATTENDEE` vorhanden sein. Bei späteren Änderungen vergleicht der Server alle
Organizer-kontrollierten Felder und alle anderen Teilnehmer. Nur der eigene
`PARTSTAT` darf abweichen; daraus entsteht nach Freigabe eine `REPLY` für die
lokale Organizer-Inbox.

### Inbox

`PROPFIND`, `GET`, `calendar-query`, `calendar-multiget` und `DELETE` werden
unterstützt. DELETE entfernt keine Auditdaten: Die Nachricht wird archiviert,
aus DAV ausgeblendet und behält vorherigen Zustand, Zeitpunkt und Bearbeiter.
Weboberfläche und CalDAV greifen auf denselben persönlichen Posteingang zu.

### Free/Busy-Outbox

Die Outbox akzeptiert ausschließlich `METHOD:REQUEST` mit genau einer
`VFREEBUSY`-Komponente. `DTSTART` und `DTEND` müssen UTC-Werte sein. Überlappende
Belegtzeiten werden zusammengeführt. Abgesagte, gelöschte oder verschobene
Termine zählen nicht als belegt. Weder Titel, Grund, Tags, Teilnehmer,
Kalendernamen noch interne URLs verlassen das Empfängerkonto.

## Rechte, Sicherheit und Datenschutz

- Scheduling und alle Gegenstellen-Freigaben sind standardmäßig aus.
- Einladungsrecht und Free/Busy-Recht sind voneinander unabhängig.
- Basic Auth nutzt weiterhin das separate CalDAV-App-Passwort.
- Fremde Scheduling-Pfade sind durch 404 abgeschirmt.
- Organizer- und Teilnehmerrollen werden serverseitig aus der eindeutigen
  Calendar User Address abgeleitet, nicht aus einem Client-Schalter.
- Nachrichten werden nur lokal zugestellt. Es gibt keinen automatischen
  Versand an externe Adressen und keine Verbindung zu einem Fremddienst.
- Antworten enthalten `Cache-Control: no-store`; Inbox-Rohdaten erscheinen
  nicht in Listen oder Fehlermeldungen.
- Rechteänderungen, Zustellungen, Anwendungen und Archivierungen werden in
  der Revisionshistorie dem Benutzer zugeordnet.

## Fehler- und Ausfallverhalten

Ungültige Scheduling-Daten liefern XML-Preconditions oder HTTP 400, ohne eine
Inbox-Nachricht anzulegen. Veraltete Schedule-Tags liefern HTTP 412 samt
aktuellem Tag. Nicht freigegebene Free/Busy-Empfänger erscheinen mit Status
`3.8`, unbekannte Adressen mit `3.7`; dabei wird nicht verraten, ob ein Konto,
Kalender oder Termin existiert. Ein deaktiviertes Konto kündigt Scheduling
nicht an und seine Collections liefern 404.

Kalenderdaten werden vor der lokalen Scheduling-Zustellung gespeichert. Eine
abgelehnte oder nicht mögliche Zustellung setzt die Kalenderänderung nicht
zurück; sie erzeugt aber keine fremde Inbox-Nachricht. Damit bleibt CalDAV auch
bei deaktiviertem Empfängerkonto verfügbar.

## Migration und Rückwärtskompatibilität

Kalendereinträge aus älteren Versionen ohne `access`- oder `managers`-Feld
werden beim Anzeigen nur im Arbeitsspeicher mit leeren Freigaben ergänzt. Dadurch
bleibt die Kalenderseite nutzbar, ohne Bestandsdaten automatisch umzuschreiben.

Inbox-Löschungen unterstützen `If-Match` und antworten bei einem veralteten
ETag mit `412 Precondition Failed`. Outbox-Anfragen akzeptieren ausschließlich
`text/calendar`. Zustellversuche und Free/Busy-Abfragen werden mit Ergebniscode,
aber ohne Terminbeschreibung, Tags oder sonstige private Inhalte revisioniert.

Es gibt keine destruktive Migration. Der neue Zugriffsspeicher wird erst beim
Speichern der Einstellungen angelegt. Bestehende CalDAV-Konten, URLs, Termine,
Sync-Tokens und App-Passwörter bleiben gültig. Ohne Aktivierung verhalten sich
alte Clients wie zuvor; normale Kalenderobjekte ohne `ORGANIZER` bleiben vom
Scheduling unberührt.

## Tests

Automatisiert geprüft werden:

- deaktivierte Voreinstellung und Discovery nach Aktivierung,
- fremde Principal- und Inbox-Pfade,
- Zustellung mit und ohne explizite Freigabe,
- Inbox-PROPFIND, GET, REPORT und auditbewahrendes DELETE,
- Schedule-Tag-Erfolg und veralteter Konflikt,
- erlaubte eigene Teilnehmerantwort sowie verbotene Terminmanipulation,
- Organizer-Spoofing und verweigerte Free/Busy-Abfrage,
- zusammengeführte Belegtzeiten ohne private Inhalte,
- UTC-, Empfänger-, Größen- und Zeitraumgrenzen,
- Bedienoberfläche und revisionssichere Freigabeeinstellungen.

## Bekannte Grenzen und bewusst nicht implementierte Teile

- Scheduling erfolgt nur zwischen Benutzern derselben Installation. RFC 6638
  definiert keine Server-zu-Server-Zustellung.
- Es gibt noch keinen externen iMIP-Mailtransport nach RFC 6047.
- Serieninstanzen, Delegation, `SCHEDULE-AGENT=CLIENT/NONE`, `VTODO`,
  `VPOLL`, `REFRESH`, `ADD` und automatische Gegenvorschlagsannahme fehlen.
- `VALARM` und `TRANSP` werden noch nicht als gesonderte Teilnehmeränderungen
  bearbeitet.
- Free/Busy liefert nur `BUSY`, noch keine Unterteilung in `BUSY-TENTATIVE`
  oder `BUSY-UNAVAILABLE`.
- Die Oberfläche verwaltet Freigaben benutzerweise; Gruppen und zeitlich
  befristete Scheduling-ACLs sind noch nicht vorhanden.

## Deaktivierung und Rückkehr

Der Schalter **CalDAV Scheduling für mein Konto aktivieren** kann jederzeit
ausgeschaltet werden. Discovery, Zustellung und Free/Busy sind danach sofort
nicht mehr verfügbar; Kalender und bereits auditierte Nachrichten bleiben
unverändert. Ein Code-Rollback kann den additiven Zugriffsspeicher ignorieren.
Es werden weder Kalenderdaten noch Freigaben automatisch gelöscht.
