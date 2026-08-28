# Kalender

Serientermine, einzelne Verschiebungen/Absagen, Sommerzeitbehandlung und die
zugrunde liegenden Anforderungen aus RFC 5545 und RFC 4791 sind ausführlich in
[KALENDER_SERIEN_RFC5545.md](KALENDER_SERIEN_RFC5545.md) dokumentiert.
Lokale DISPLAY-Erinnerungen, Bestätigung, Snooze und der ICS-/CalDAV-Roundtrip
sind in
[KALENDER_ERINNERUNGEN_RFC5545_9074.md](KALENDER_ERINNERUNGEN_RFC5545_9074.md)
mit den Anforderungen aus RFC 5545 und RFC 9074 beschrieben.

Der Kalender liegt dateibasiert in `.simpleoffice-meta/calendar.json`. Termine
haben Titel, Beginn, optionales Ende, Tags und optional eine
Kontaktverknüpfung. Jede Änderung wird dem angemeldeten Benutzer zugeordnet und
als Git-Revision gesichert.

Die Oberfläche unter `/documents/calendar` ist für Anlegen, Ändern und Löschen
aktiv. Ein Termin ist standardmäßig `private` und wird nicht veröffentlicht.

## Import und Export

Die Schaltfläche **ICS importieren** öffnet direkt in der Kalenderansicht den
Dateiimport. Unterstützt werden `.ics`-Dateien aus Google Kalender,
Thunderbird und anderen iCalendar-Anwendungen. Importierte Termine bleiben
standardmäßig privat. Enthält eine Datei erneut dieselbe iCalendar-UID, wird
der bestehende importierte Termin aktualisiert und nicht dupliziert. Der Import
wird dem angemeldeten Benutzer zugeordnet und in der Historie protokolliert.

**ICS exportieren** lädt alle für den angemeldeten Benutzer sichtbaren und
aktiven Termine als `simpleoffice-kalender.ics` herunter. Abgesagte, gelöschte
oder verschobene Altstände werden nicht veröffentlicht. Vor größeren Importen
ist ein geprüftes Backup sinnvoll; der Import löscht jedoch keine Termine.

Jeder Termin besitzt einen Eigentümer. Dieser kann weitere registrierte
SimpleOffice-Benutzer zur gemeinsamen Verwaltung freigeben. Freigegebene
Benutzer dürfen den Termin sehen, bearbeiten und seinen Lebenszyklusstatus
ändern; nur der Eigentümer ändert die Freigabeliste. Feldänderungen speichern
Altwert, Neuwert, Zeitpunkt und Benutzer und bleiben zusätzlich als
Git-Revision erhalten.

- `family`: Familienansicht sieht Datum, Titel und Familien-Tags.
- `external`: Externe Ansicht sieht Datum und ausschließlich den hinterlegten
  Hinweistext, etwa „Belegt“, plus externe Tags. Der Titel bleibt verborgen.
- Tags haben jeweils ihre eigene Sichtbarkeit: privat, Familie oder extern.

Die veröffentlichten Ansichten sind `/documents/calendar/published/family` und
`/documents/calendar/published/external`. Sie sollten hinter einer passenden
Zugriffsregel oder nur in einem geschützten Netz bereitgestellt werden.

Der nächste Ausbauschritt für Thunderbird ist ein aktivierbarer CalDAV-Endpunkt
mit denselben App-Passwörtern wie CardDAV. Er wird getrennt ergänzt, damit
Kontakt- und Kalenderkonflikte nicht unkontrolliert vermischt werden.

## Externe Buchung mit Bestätigung

Die Kalenderseite zeigt die vollständige öffentliche Buchungsadresse direkt an. Sie kann dort kopiert oder in einem neuen Tab geöffnet werden; bei deaktivierter Buchung weist die Oberfläche vor dem Teilen auf die notwendige Aktivierung hin.

Unter `/documents/calendar` lassen sich feste Buchungszeiten aktivieren. Die
öffentliche Seite `/documents/calendar/book` zeigt dann ausschließlich freie
Slots; bestehende und bereits angefragte Termine blockieren den Zeitraum. Titel,
Grund, Name und E-Mail-Adresse sind Pflicht.

Eine Anfrage bleibt `pending`. Erst „Bestätigen und ICS senden“ macht sie
verbindlich. Der Termin wird dabei sofort blockiert, auch wenn SMTP nicht
verfügbar ist oder der Versand scheitert. Der Versandstatus bleibt dann
`ausstehend`; im bestätigten Termin steht eine ICS-Datei zum manuellen
Weitergeben bereit. Zusätzlich öffnet „E-Mail im Client vorbereiten" den
Standard-Mailclient mit Empfänger, Betreff und dem ICS-Download-Link. Die
E-Mail kann damit später versendet werden.
Dafür müssen diese Variablen gesetzt sein:

```bash
SIMPLEOFFICE_SMTP_HOST=smtp.example.org
SIMPLEOFFICE_SMTP_FROM=kalender@example.org
SIMPLEOFFICE_SMTP_PORT=587
SIMPLEOFFICE_SMTP_STARTTLS=true
SIMPLEOFFICE_SMTP_USER=kalender@example.org
SIMPLEOFFICE_SMTP_PASSWORD='...'
```

Ohne SMTP-Konfiguration bleibt die Anfrage bewusst offen. In der ICS-Antwort
stehen nur der bestätigte Titel und die Zeit; der Grund wird nicht veröffentlicht.

## ICS-Absagen und sichere UID-Zuordnung

Beim Import aus Thunderbird, Google Kalender oder einer anderen
iCalendar-Quelle verarbeitet SimpleOffice `STATUS:CANCELLED`. Eine Absage mit
derselben `UID` setzt einen zuvor vom selben Benutzer importierten Termin auf
`cancelled`, statt ihn zu löschen. Der Termin verschwindet aus veröffentlichten
Ansichten und blockiert keine Buchungszeit mehr. Zeitpunkt und Benutzer der
Statusänderung bleiben in der Status- und Git-Historie nachvollziehbar. Ein
vollständiger Folgeimport mit `STATUS:CONFIRMED` aktiviert den Termin wieder.

Gelöschte Termine verschwinden aus der aktiven Termin- und Monatsansicht, bleiben
aber unter **Kalender → Gelöschte Termine** für weiterhin leseberechtigte
Benutzer sichtbar. Die Übersicht zeigt den letzten Termininhalt, Eigentümer,
Löschzeitpunkt und Akteur sowie die gespeicherte Feld- und Statushistorie. Sie
ist eine reine Auditansicht: Gelöschte Termine werden nicht wieder in ICS oder
CalDAV veröffentlicht und die Ansicht verändert oder revidiert keine Historie.

Die Zuordnung einer externen `UID` gilt nur innerhalb desselben Benutzers und
der Importquelle `ical_import`. Dadurch kann ein anderer Benutzer mit derselben
UID weder einen fremden Termin ändern noch absagen. Sichtbarkeit und Tags neuer
Importe bleiben privat; vorhandene Freigaben werden bei Aktualisierungen nicht
erweitert. Gleichzeitige ICS-Importe werden über die Kalenderschreibsperre
nacheinander verarbeitet, um verlorene Aktualisierungen zu vermeiden.

Es ist keine Konfiguration und kein externer Dienst erforderlich. Unterstützt
wird UTF-8-iCalendar mit `VEVENT`, `UID`, `SUMMARY`, `DESCRIPTION`, `DTSTART`,
`DTEND`, `CATEGORIES` und `STATUS`. Das Verhalten orientiert sich an RFC 5545
und RFC 5546. Eine Absage ohne passenden eigenen Import wird ignoriert und
erzeugt keinen leeren Termin; enthält eine Datei ausschließlich solche Absagen,
meldet der Import „keine nutzbaren VEVENT-Datensätze“. Netzwerk- oder
Anmeldedaten werden nicht übertragen oder gespeichert.

Automatisierte Tests prüfen Absage, erneute Bestätigung, Freigabe des
Buchungsslots, Benutzertrennung und unbekannte UIDs. Wiederholungsinstanzen,
feldweises Zusammenführen konkurrierender Änderungen und eine laufende
CalDAV-Synchronisation sind weiterhin nicht enthalten. Zur Deaktivierung kann
auf die vorherige Version zurückgegangen werden; das JSON-Format bleibt
kompatibel. Bereits als abgesagt gespeicherte Termine bleiben dabei erhalten
und können über den Lebenszyklusstatus wieder aktiviert werden.
