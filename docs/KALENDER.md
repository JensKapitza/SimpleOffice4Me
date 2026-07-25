# Kalender

Der Kalender liegt dateibasiert in `.simpleoffice-meta/calendar.json`. Termine
haben Titel, Beginn, optionales Ende, Tags und optional eine
Kontaktverknüpfung. Jede Änderung wird dem angemeldeten Benutzer zugeordnet und
als Git-Revision gesichert.

Die Oberfläche unter `/documents/calendar` ist für Anlegen, Ändern und Löschen
aktiv. Ein Termin ist standardmäßig `private` und wird nicht veröffentlicht.

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
