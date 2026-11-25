# Optionaler Google-Kalender-Abgleich

## Zweck und Nutzen

SimpleOffice kann einen ausdrücklich ausgewählten Google-Kalender inkrementell
in einen lokalen, beschreibbaren Kalender übernehmen. Der Abgleich ist
standardmäßig manuell (**Nur prüfen** / **Jetzt abgleichen**). Auf derselben
Kalenderseite kann der betroffene Benutzer den automatischen Import
ausdrücklich für das eingerichtete Konto und Ziel aktivieren. SimpleOffice schreibt
niemals zu Google zurück und überträgt keine lokalen Termine, Freigaben oder
Kontakte an Google.

Die Funktion ergänzt den einmaligen Import bei einer Google-Anmeldung um
Pagination, dauerhafte Sync-Tokens, Löschungen, Serien-, Teilnehmer- und
Zeitzonenfelder, begrenzte Netzwerkzugriffe sowie eine nachvollziehbare
Konfliktentscheidung. Vor dem ersten Schreiben kann derselbe Abruf als
Vorschau ausgeführt werden.

## Primäre Standards und verbindliche Vorgaben

Google Calendar verwendet eine JSON-API und ist selbst kein IETF-Protokoll.
Die übertragenen Kalenderdaten werden deshalb gegen iCalendar modelliert:

| Quelle | Anforderung | Umsetzung |
|---|---|---|
| [RFC 5545 §3.6.1](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.6.1) | Ein `VEVENT` benötigt eine stabile UID; Zeit, Status, Organisator und Teilnehmer haben definierte Semantik. | Googles unveränderliche Ereignis-ID ist der technische Quellschlüssel, `iCalUID` bleibt als Interoperabilitätskennung erhalten. Start, Ende, Status, Organisator und Teilnehmer werden validiert abgebildet. |
| [RFC 5545 §3.2.19](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.2.19) und [§3.3.5](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.3.5) | Lokale Zeiten SHOULD eine TZID verwenden; UTC wird mit `Z` gekennzeichnet. | Google-`timeZone` wird als IANA-Zeitzone erhalten. Offset- und UTC-Zeitstempel bleiben ISO-konform; Ganztage werden als lokale Tagesgrenzen übernommen. |
| [RFC 5545 §3.8.5.3](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.5.3) | Wiederholungsregeln folgen `RRULE`. | Bei `singleEvents=false` wird die Regel des Serienmasters übernommen und durch die vorhandene begrenzte RFC-5545-Engine validiert. |
| [RFC 5545 §3.8.1.11](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.1.11) | `STATUS:CANCELLED` kennzeichnet abgesagte Ereignisse. | Gelöschte Google-Ressourcen werden mit `showDeleted=true` empfangen und lokal revisionssicher abgesagt. Ein Löschstub ohne Zeitangaben verwendet ausschließlich die bereits gespeicherte Zeitspanne. |
| [RFC 7986 §5.11](https://www.rfc-editor.org/rfc/rfc7986.html#section-5.11) | Konferenz-URIs MAY mit Eigenschaften übertragen werden. | HTTPS-, HTTP- und Telefonzugänge werden übernommen; unbekannte oder unsichere Schemata sowie Zugangsdaten in URLs werden verworfen beziehungsweise abgewiesen. |

Für den inkrementellen Transport gelten die primären Google-Vorgaben:

- [Synchronize resources efficiently](https://developers.google.com/workspace/calendar/api/guides/sync): Zuerst vollständiger Abruf, alle Seiten lesen, danach `nextSyncToken` speichern. Bei einem inkrementellen Abruf bleiben die Abfrageparameter stabil. HTTP `410 Gone` verwirft den abgelaufenen Token und startet kontrolliert einen vollständigen Abruf.
- [Events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list): `showDeleted=true`, `singleEvents=false`, höchstens 2500 Einträge je Seite und Pagination über `nextPageToken`. Gelöschte Einträge sind bei Sync-Tokens immer enthalten.
- [OAuth 2.0 für Webserver](https://developers.google.com/identity/protocols/oauth2/web-server#offline): Ein Refresh-Token ermöglicht kurzlebige Zugriffstokens. SimpleOffice speichert das neue Zugriffstoken nicht und verlangt nur den lesenden Calendar-Scope.

Die Begriffe MUST, SHOULD und MAY in dieser Datei geben Anforderungen der
verlinkten Originaltexte wieder. Google-spezifische Anforderungen sind keine
IETF-Konformitätsaussage.

## Konfiguration und Voraussetzungen

Voraussetzungen sind HTTPS, eine Google-OAuth-Anwendung, ein für den
Serverbetrieb ausgestellter Offline-Refresh-Token mit dem Scope
`https://www.googleapis.com/auth/calendar.readonly` und ein vorhandener lokaler
Zielkalender mit Schreibrecht des zugeordneten SimpleOffice-Benutzers.

Die Konfiguration wird ausschließlich aus dem App-Store gelesen (Admin →
Einstellungen → Integrationen → Marketplace & OAuth → Kalenderkonten-JSON,
verschlüsselt gespeichert, keine Umgebungsvariable). Beispiel mit
Platzhaltern:

```json
{
  "alice": {
    "client_id": "OAUTH-CLIENT-ID",
    "client_secret": "OAUTH-CLIENT-SECRET",
    "refresh_token": "OFFLINE-REFRESH-TOKEN",
    "calendar_id": "primary",
    "target_calendar_id": "default"
  }
}
```

Geheimnisse gehören weder in das Repository noch in `start.sh`, eine
`.env`-Datei im Projektverzeichnis, Logs oder Screenshots. Mehrere Benutzer
können getrennte Blöcke erhalten; Benutzer ohne Block sehen die Funktion als
deaktiviert. Client-Secret und Refresh-Token werden niemals in Statusdatei,
Audit-Nutzdaten oder HTML ausgegeben.

## Bedienung

1. Kalender öffnen und im Abschnitt **Google Kalender sicher abgleichen** die
   Quell- und Ziel-ID kontrollieren.
2. **Nur prüfen** ruft Google ab, schreibt jedoch keine Kalenderereignisse und
   keinen Sync-Token. Der Prüfvorgang selbst wird auditiert.
3. **Jetzt abgleichen** übernimmt konfliktfreie Änderungen in den lokalen
   Zielkalender und veröffentlicht sie im CalDAV-Sync-Journal.
4. Wenn Google und SimpleOffice dasselbe Ereignis seit dem letzten Abgleich
   geändert haben, bleibt der lokale Termin unverändert. Danach muss
   ausdrücklich **Google-Versionen übernehmen** oder **Lokale Versionen
   behalten** gewählt werden. Die Quelle wird unmittelbar erneut abgefragt;
   zwischenzeitliche Änderungen können daher nicht unbemerkt bestätigt werden.
5. **Sync-Zustand zurücksetzen** löscht nur Token und letzten Status. Bereits
   importierte Termine bleiben bestehen; der nächste Lauf ist ein Vollabgleich.

## Automatischer Betrieb

Unter **Google Kalender sicher abgleichen** zeigt **Automatischer Import von
Google** den Zustand, den letzten automatischen Versuch und den nächsten
geplanten Versuch. **Automatischen Import aktivieren** gilt nur für das eigene
SimpleOffice-Konto und die aktuelle OAuth-/Kalenderkonfiguration. Es wird nichts
an Google gesendet außer der OAuth-Token-Anfrage und dem lesenden Abruf.
Ein laufender Server prüft fällige Konten alle 30 Sekunden; nach Erfolg oder
manuellem Abgleich folgt der nächste Versuch frühestens 15 Minuten später.
Maximal vier fällige Konten je Durchgang werden verarbeitet. Bei Fehlern wird
nach 15, 30, 60 Minuten usw. (höchstens sechs Stunden) erneut versucht.
Neustarts behalten den Termin; ein harter Abbruch verzögert den nächsten
Versuch um maximal fünf Minuten ab Beginn des abgebrochenen Versuchs.

Bei Konflikten hält der Hintergrunddienst für dieses Konto an, bis die
Konflikte über die bestehenden Aktionen behandelt wurden. Ohne Schreibrecht,
bei gesperrtem Konto oder bei anderer Konto-ID wird kein Import durchgeführt.
Eine Änderung von OAuth-Zugangsdaten, Quell- oder Zielkalender erfordert neue
Zustimmung und einen Vollabruf ohne den vorherigen Cursor. Der Import
aktualisiert nur Ereignisse derselben Quelle und desselben Ziels. Beim Wechsel
des Ziels bleiben die alten Termine bestehen; wenn die neue Quelle dieselben
Termine enthält, werden sie im neuen Ziel importiert. Bei der nicht eindeutigen
Google-ID `primary` kann ein ausgetauschter Refresh-Token eine andere
Identität darstellen; die alte Kopie bleibt deshalb unangetastet. Eine explizite
Google-Kalender-ID bewahrt die Ereigniszuordnung bei reiner Token-Rotation.

Der normale SimpleOffice-Start (Waitress, auch im Docker-Container) und der
eingebettete Desktop-Start verwalten den Worker mit der Serverlaufzeit. Für
eigene WSGI-Deployments mit gleicher Daten-/Instanzkonfiguration den separaten
Prozess `flask --app app google-calendar-worker` betreiben. Mit `--once` lässt
sich ein einzelner begrenzter Durchgang ausführen. Es gibt keinen Scheduler in
einer HTTP-Anfrage und keinen externen Cron-Zwang bei den Standardstartwegen.

## Rechte, Freigaben und Audit

Vor Vorschau, Übernahme und Konfliktentscheidung wird das Schreibrecht am
lokalen Zielkalender geprüft. Ein fremder Kalender ist nur bei einer bereits
vorhandenen `edit`-Freigabe zulässig. Der Import erzeugt oder erweitert keine
Kalenderfreigabe. Ereignisse in einem freigegebenen Zielkalender bleiben dessen
Eigentümer zugeordnet; der synchronisierende Benutzer erhält nur das bereits
erforderliche Bearbeitungsrecht am Ereignis.

Vorschau, Übernahme, Konfliktentscheidung und Zurücksetzen werden in der
Revision History mit Benutzer, Zeitpunkt, Quelle und Ergebnis protokolliert.
Auch Quellversionswechsel erscheinen in der feldgenauen Ereignishistorie.
OAuth-Geheimnisse und kurzlebige Zugriffstokens sind ausdrücklich nicht Teil
dieser Einträge.

## Abbildung und Protokollkompatibilität

- Google-ID → stabiler `source_id`; `iCalUID` → Quellenmetadatum
- `summary`, `description`, `start`, `end`, `timeZone` → lokale Terminfelder
- `status`, `transparency`, `visibility`, `location`, `htmlLink` → RFC-5545-
  Metadaten
- `organizer` und bis zu 200 eindeutige Teilnehmer → Organizer/ATTENDEE mit
  Rolle, RSVP und Teilnahmestatus
- `RRULE` des Serienmasters → lokale Serienregel
- sichere `conferenceData.entryPoints` → RFC-7986-Konferenzzugänge
- abgesagte/gelöschte Ressourcen → lokaler Status `cancelled`

Importierte Termine sind anschließend über den bestehenden ICS-Export und
CalDAV für Thunderbird verfügbar. Der lokale CalDAV-Sync-Token wird bei jeder
übernommenen Google-Änderung fortgeschrieben.

## Konflikte, Fehler und Ausfälle

Es gelten zehn Sekunden Zeitlimit je Google-Aufruf, höchstens 5 MiB Antwort,
20 Seiten und 5000 Änderungen pro Lauf. Ungültiges JSON, fehlende Pflichtfelder,
unzulässige URIs, zu viele Seiten, Netzwerkfehler und HTTP-Fehler führen zu
einer verständlichen Meldung. Ein fehlgeschlagener Lauf speichert keinen neuen
Google-Sync-Token. Dadurch wird ein Teilabruf nicht fälschlich als vollständig
markiert.

Manuelle Vorschau, Übernahme, Konfliktauflösung und Hintergrundarbeit
teilen eine exklusive Dateisperre pro Konto. Bei einer bereits laufenden
Synchronisation kann der manuelle Versuch später wiederholt werden. Nur ein
Worker je Dokumentablage verarbeitet fällige Konten gleichzeitig. Google-ETag, letzter Google-Zeitpunkt, lokaler Änderungszeitpunkt
und letzter erfolgreicher Sync-Zeitpunkt bestimmen Konflikte. Ohne explizite
Entscheidung gibt es kein Last-write-wins. Beim Beibehalten der lokalen Version
wird nur die geprüfte Remote-Version bestätigt; es erfolgt kein Upload zu
Google.

## Migration, Rückwärtskompatibilität und Deaktivierung

Es gibt keine Datenmigration. Vorhandene Konten laufen nicht automatisch
los. Beim nächsten Abruf ohne passenden Konfigurationsfingerabdruck erfolgt ein
Vollabgleich. Die additive Statusdatei liegt unter
`.simpleoffice-meta/google-calendar-sync.json`; sie enthält den Google-Cursor,
Status-/Zeitwerte und nicht umkehrbare Konfigurationsabgleiche, keine
OAuth-Geheimnisse. Bei der Aktualisierung zuerst die alte Serverinstanz beenden,
damit kein Prozess mit der früheren Sperrlogik weiterläuft. Ein Software-Rollback lässt importierte Termine als normale
lokale Termine bestehen. Vor einem Rollback kann der Zielkalender als ICS
exportiert werden.

**Automatischen Import deaktivieren** hält nur den Hintergrundimport an;
manueller Abgleich und Cursor bleiben möglich. **Sync-Zustand zurücksetzen**
entfernt den Cursor, aber keine Termine und keine Hintergrundzustimmung; die
nächste manuelle oder automatische Ausführung liest vollständig. Zum völligen
Trennen den Kontoblock aus der geschützten Marketplace-Konfiguration entfernen.
Es werden weder lokale Termine noch Google-Daten gelöscht.

## Tests und bekannte Grenzen

Automatisierte Tests decken Mapping, IANA-Zeitzonen, Serien, Teilnehmer,
Konferenzlinks, Vorschau ohne Kalenderänderung, initialen und inkrementellen
Abruf, stabile Parameter, Paginationgrenzen, `410 Gone`, Löschstubs,
Schreibrechte, Konflikte, beide Konfliktentscheidungen, Tokenfortschritt,
Geheimnisschutz, Statusfehler und Audit ab.

Bewusst nicht implementiert sind Schreibzugriffe auf Google, Push-Webhooks,
mehrere Google-Quellkalender pro Benutzer,
Dateianhänge sowie das Zusammenführen einzelner geänderter Felder. Google-
Serienausnahmen werden als eigenständige Quellereignisse übernommen; die
vollständige Rückabbildung in `RECURRENCE-ID`-Overrides bleibt eine bekannte
Interoperabilitätsgrenze. Nicht unterstützte Google-Erweiterungen werden nicht
ungeprüft gespeichert.
