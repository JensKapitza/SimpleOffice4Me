# Lokale Kalendererinnerungen nach RFC 5545 und RFC 9074

## Zweck und Nutzen

SimpleOffice4Me kann bis zu acht lokale Erinnerungen pro Termin speichern,
anzeigen und über ICS beziehungsweise CalDAV austauschen. Eine Erinnerung kann
sich auf Beginn oder Ende beziehen, wiederholt werden, bestätigt oder für 5 bis
1440 Minuten verschoben werden. Serientermine erzeugen Erinnerungsinstanzen in
der Zeitzone der Serie; Sommerzeitwechsel werden vor der Umrechnung nach UTC
berücksichtigt.

Die Funktion bleibt bewusst lokal: `DISPLAY` zeigt einen Hinweis in einem
Kalenderclient oder in SimpleOffice4Me. Es werden weder E-Mails verschickt noch
Push-Dienste, Browser-Benachrichtigungsdienste oder externe APIs angesprochen.

## Maßgebliche Primärstandards

Geprüft wurden die Originaltexte:

- [RFC 5545 Abschnitt 3.6.6 – VALARM](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.6.6)
- [RFC 5545 Abschnitt 3.8.6.1 – ACTION](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.6.1)
- [RFC 5545 Abschnitt 3.8.6.2 – REPEAT](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.6.2)
- [RFC 5545 Abschnitt 3.8.6.3 – TRIGGER](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.6.3)
- [RFC 5545 Abschnitt 3.3.6 – DURATION](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.3.6)
- [RFC 5545 Abschnitt 3.2.14 – RELATED](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.2.14)
- [RFC 9074 Abschnitt 4 – UID für VALARM](https://www.rfc-editor.org/rfc/rfc9074.html#section-4)
- [RFC 9074 Abschnitt 6.1 – ACKNOWLEDGED](https://www.rfc-editor.org/rfc/rfc9074.html#section-6.1)
- [RFC 9074 Abschnitt 7 – Snooze](https://www.rfc-editor.org/rfc/rfc9074.html#section-7)

## MUST-, SHOULD- und MAY-Anforderungen

| Normative Aussage | Quelle | Entscheidung und Konformität |
|---|---|---|
| Ein `VALARM` muss `ACTION` und `TRIGGER` enthalten. | RFC 5545, 3.6.6 | Parser weist fehlende Felder vollständig zurück. |
| `DISPLAY` muss genau eine `DESCRIPTION` enthalten. | RFC 5545, 3.6.6 | Leere, fehlende oder mehrfache Beschreibungen werden abgewiesen. |
| Ein relativer Trigger darf sich auf `START` oder `END` beziehen. | RFC 5545, 3.8.6.3 und 3.2.14 | Beide Varianten werden unterstützt; `END` ist nur bei vorhandenem Terminende erlaubt. |
| Ein absoluter Trigger muss als UTC-Datum/Zeit angegeben werden. | RFC 5545, 3.8.6.3 | Nur `VALUE=DATE-TIME` mit abschließendem `Z` wird akzeptiert. |
| Bei Wiederholung müssen `REPEAT` und `DURATION` gemeinsam vorkommen. | RFC 5545, 3.6.6 | Fehlt eines der Felder, wird der gesamte Schreibvorgang abgewiesen. |
| Ein Client soll für Alarme eine global eindeutige, stabile UID erzeugen. | RFC 9074, 4 | UIDs werden übernommen oder zufällig als UUID unter `simpleoffice.local` erzeugt; doppelte UIDs in einem Termin sind verboten. |
| `ACKNOWLEDGED` muss UTC sein; frühere Auslösungen sollen nicht erneut gemeldet werden. | RFC 9074, 6.1 | Bestätigung wird in UTC gespeichert. Trigger bis einschließlich dieses Zeitpunkts werden unterdrückt. |
| Snooze soll die ursprüngliche Erinnerung bestätigen und eine verwandte Erinnerung erzeugen. | RFC 9074, 7 | Das Original erhält `ACKNOWLEDGED`; die neue absolute Erinnerung verweist mit `RELATED-TO;RELTYPE=SNOOZE` auf dessen UID. |
| Ein Kalenderobjekt darf mehrere Alarmkomponenten enthalten. | RFC 5545, 3.6.6 | Bis zu acht werden unterstützt. Die Produktgrenze schützt Darstellung und Rechenzeit. |
| Alarmzeiten dürfen relativ oder absolut sein. | RFC 5545, 3.8.6.3 | Relative Zeiten bis 366 Tage und absolute UTC-Zeitpunkte werden unterstützt. |

Die Begriffe MUST, SHOULD und MAY entsprechen den normativen Schlüsselwörtern
der RFCs. Produktgrenzen sind enger als die Standards, wo dies die
Vorhersagbarkeit, Sicherheit oder Leistung verbessert.

## Abgeleitete Designentscheidungen

### Nur lokale DISPLAY-Alarme

`EMAIL` und `AUDIO` werden beim Import mit verständlichem Fehler abgewiesen.
Dadurch löst der Import einer fremden Datei keine externe Kommunikation,
automatische Datenweitergabe oder serverseitige Audiowiedergabe aus.

### Atomare Validierung

Ein ICS-/CalDAV-Schreibvorgang wird erst gespeichert, wenn alle enthaltenen
Alarme gültig sind. Ein fehlerhafter neunter Alarm, eine doppelte UID oder eine
unvollständige Wiederholung lässt keinen Teilzustand zurück.

### Alarm und Ereignis strikt trennen

Der Parser verarbeitet verschachtelte `VALARM`-Zeilen getrennt vom umgebenden
`VEVENT`. Eine Alarm-`DESCRIPTION` kann deshalb nicht die
Ereignisbeschreibung überschreiben. Alarme an einzelnen
`RECURRENCE-ID`-Komponenten werden aktuell ausdrücklich abgewiesen.

### Zeitzonen und Serien

Relative Trigger werden zunächst an der lokalen, expandierten Serieninstanz
berechnet und anschließend nach UTC konvertiert. Damit bleibt beispielsweise
„15 Minuten vor 09:00 Europe/Berlin“ auch über einen Sommerzeitwechsel bei
08:45 lokaler Uhrzeit. Absolute Trigger sind bereits UTC.

### Begrenzte Abfragen

Die Web-API akzeptiert ausschließlich zeitzonenbehaftete Intervalle von
höchstens 31 Tagen und höchstens 500 Ergebnisse. Ein Termin darf höchstens acht
Alarme, zehn Wiederholungen pro Alarm und einen relativen Abstand von 366 Tagen
haben. Diese Grenzen verhindern unbegrenzte Serien- und Alarmexpansion.

## Bedienung

1. **Kalender** öffnen und den Termin anklicken.
2. Unter **Lokale Erinnerungen (VALARM)** Hinweistext, Minuten,
   **vor/nach** sowie **Beginn/Ende** wählen.
3. **Hinzufügen** speichert die Erinnerung mit optimistischer
   Konfliktprüfung und aktualisiert den CalDAV-Sync-Stand.
4. Fällige Erinnerungen erscheinen oben unter **Lokale Erinnerungen nach
   RFC 5545**.
5. **Bestätigen** unterdrückt bereits ausgelöste Instanzen.
6. **Später** bestätigt das Original und legt eine RFC-9074-Snooze-Erinnerung
   für 5, 10, 30 oder 60 Minuten an.

Die benutzerbezogene JSON-Darstellung liegt nach Anmeldung unter
`/documents/calendar/reminders.json`. Optionale Parameter sind `from`, `to` und
`calendar_id`; Datumswerte müssen ISO-8601-Zeitzonen enthalten.

## Konfiguration und Voraussetzungen

Es gibt keine neue Umgebungsvariable, keinen Hintergrunddienst und keine neue
Python-Abhängigkeit. Für Thunderbird oder einen anderen CalDAV-Client gelten
weiter die Voraussetzungen aus [CALDAV_RFC_IMPLEMENTIERUNG.md](CALDAV_RFC_IMPLEMENTIERUNG.md):

- HTTPS außerhalb eines vertrauenswürdigen lokalen Netzes,
- ein separates CalDAV-App-Passwort,
- die CalDAV-Adresse des Benutzers,
- Schreibrecht für den gewählten Kalender.

SimpleOffice4Me sendet nicht selbstständig Benachrichtigungen. Die Kalenderseite
muss geöffnet oder ein kompatibler CalDAV-Client muss aktiv sein, damit ein
sichtbarer Hinweis erscheint.

## Rechte- und Freigabeverhalten

- Eigentümer und Benutzer mit explizitem `edit`-Recht dürfen Erinnerungen
  anlegen, entfernen, bestätigen und verschieben.
- Benutzer mit `read`-Recht sehen fällige Erinnerungen sichtbarer Termine,
  dürfen ihren Zustand aber nicht verändern.
- Nicht freigegebene Termine und Kalender erscheinen weder in der Oberfläche
  noch in `reminders.json`.
- Die Kalenderfreigabe wird durch eine Erinnerung niemals erweitert.
- CalDAV-Schreibrechte werden vor dem Import unverändert geprüft.

Alle Änderungen laufen unter derselben exklusiven Kalendersperre wie andere
Terminänderungen. Die Weboberfläche sendet den zuletzt gesehenen
`updated_at`-Wert; bei paralleler Bearbeitung wird statt Überschreiben ein
Konflikt gemeldet.

## Audit-Historie

Jede Alarmänderung erzeugt:

- einen Feldhistorieneintrag `alarms` mit altem und neuem Wert,
- eine Revision `calendar_event_alarms_updated`,
- bei Bestätigung zusätzlich `calendar_alarm_acknowledged`,
- bei Snooze zusätzlich `calendar_alarm_snoozed`,
- eine CalDAV-Sync-Änderung der betroffenen Collection.

UID, Trigger, Beschreibung, Bestätigungszeit und Snooze-Beziehung bleiben damit
prüfbar. Aufbewahrungsregeln werden nicht verändert.

## Format- und Protokollkompatibilität

### Unterstützt

- `BEGIN:VALARM` innerhalb des Master-`VEVENT`
- `ACTION:DISPLAY`
- relative `TRIGGER` mit RFC-5545-Dauer
- `TRIGGER;RELATED=END`
- absolute `TRIGGER;VALUE=DATE-TIME` in UTC
- `DESCRIPTION`
- `REPEAT` zusammen mit `DURATION`
- `UID` aus RFC 9074
- `ACKNOWLEDGED` in UTC
- `RELATED-TO;RELTYPE=SNOOZE`
- gefaltete ICS-Zeilen
- ICS-Import/-Export und CalDAV-PUT/-GET
- Thunderbird- und andere standardkonforme Kalenderclients

Roh importierte CalDAV-Ressourcen bleiben bis zu einer Webänderung verlustarm
erhalten. Nach einer Webänderung wird die Ressource aus dem validierten
Datenmodell neu erzeugt, damit der geänderte Alarm tatsächlich synchronisiert
wird.

### Bewusst nicht implementiert

- `ACTION:EMAIL` und `ACTION:AUDIO`
- `ATTACH` innerhalb eines Alarms
- Alarme nur für einzelne Serienausnahmen
- serverseitige E-Mail-, SMS-, Push- oder Audiozustellung
- Hintergrund-Worker und garantierte Zustellung bei geschlossener Anwendung
- proprietäre Client-Erweiterungen

Diese Teile werden nicht stillschweigend ignoriert, wenn dadurch unsicheres
Verhalten entstehen könnte. Nicht unterstützte Aktionen führen zu HTTP 400 bei
CalDAV oder einer verständlichen Importfehlermeldung.

## Fehler- und Ausfallverhalten

- Syntaxfehler, fehlende Pflichtfelder, doppelte UIDs, mehr als acht Alarme und
  unsichere Aktionen brechen den gesamten Import vor dem Speichern ab.
- Ein veralteter Webstand überschreibt keine zwischenzeitliche Änderung.
- Ein nur lesender Benutzer erhält bei Mutationen eine Fehlermeldung.
- Ist eine Serienregel ungültig oder überschreitet die Abfrage Grenzen,
  erscheinen keine unvollständigen Teilergebnisse.
- Ohne laufenden Client wird kein sichtbarer Hinweis garantiert. Der gespeicherte
  Alarm bleibt erhalten und wird beim nächsten Abruf synchronisiert.
- Externe Ausfälle sind ausgeschlossen, weil kein externer Dienst beteiligt ist.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenmigration. Bestehende Termine ohne Feld `alarms` werden wie
Termine mit leerer Alarmliste behandelt. Alte ICS-/CalDAV-Clients können
`VALARM` ignorieren, ohne den übrigen Termin unlesbar zu machen.

Webänderungen an bereits roh importierten Terminen entfernen die
`raw_ics`-Zwischendarstellung und erzeugen kanonische ICS-Daten neu. Unterstützte
Termin-, Serien-, Teilnehmer- und Alarmfelder bleiben erhalten; proprietäre,
nicht modellierte Erweiterungen können dabei entfallen.

## Tests

Automatisiert geprüft werden:

- RFC-Dauerparser und -Serializer inklusive Negativwerten,
- relative, absolute und auf das Ende bezogene Trigger,
- Wiederholung nur mit `REPEAT` plus `DURATION`,
- UID-Eindeutigkeit, acht-Alarme-Grenze und Größenlimits,
- Ablehnung von EMAIL und leerer DISPLAY-Beschreibung,
- atomarer CalDAV-PUT bei ungültigen Alarmen,
- Trennung von Ereignis- und Alarmbeschreibung,
- ICS- und CalDAV-Roundtrip,
- Serientermine über Sommerzeitwechsel,
- Bestätigung und Snooze-Beziehung nach RFC 9074,
- Eigentümer-, Bearbeiter-, Leser- und Fremdbenutzerrechte,
- optimistische Webkonflikte,
- benutzerbezogene, begrenzte JSON-Abfrage,
- Audit- und CalDAV-Sync-Aktualisierung.

## Bekannte Grenzen

- SimpleOffice4Me hat keinen Hintergrundprozess zur minutengenauen Zustellung.
- Fällige Erinnerungen werden in der Kalenderseite für sieben Tage voraus
  berechnet; die JSON-API erlaubt frei gewählte Intervalle bis 31 Tage.
- Wiederholungsalarme sind auf zehn Wiederholungen und 24 Stunden Abstand
  begrenzt.
- Serienausnahmen verwenden die Alarme des Masters.
- Eine Bestätigung gilt für den Alarm und unterdrückt ältere Triggerinstanzen;
  geräteindividuelle Bestätigungszustände werden nicht geführt.
- Google Kalender kann importierte Alarmtypen oder Produktgrenzen abweichend
  darstellen; maßgeblich ist der standardisierte `DISPLAY`-Roundtrip.

## Deaktivierung und Rückkehr zum vorherigen Verhalten

Es gibt keinen globalen Schalter und keine automatische Aktivierung. Ohne
angelegte `VALARM`-Komponente verhält sich der Kalender wie zuvor. Einzelne
Erinnerungen können im Termin mit **Entfernen** gelöscht werden.

Für eine vollständige Rückkehr:

1. alle Erinnerungen der betroffenen Termine entfernen,
2. bei Bedarf CalDAV im Client deaktivieren,
3. den vorherigen Programmstand einspielen.

Kalendertermine, Freigaben, Historie und Aufbewahrungsregeln müssen dafür nicht
migriert oder gelöscht werden.
