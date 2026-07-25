# Kalender

Der Kalender liegt dateibasiert in `.simpleoffice-meta/calendar.json`. Termine
haben Titel, Beginn, optionales Ende, Tags und optional eine
Kontaktverknüpfung. Jede Änderung wird dem angemeldeten Benutzer zugeordnet und
als Git-Revision gesichert.

Die Oberfläche unter `/documents/calendar` ist für Anlegen, Ändern und Löschen
aktiv. Ein Termin ist standardmäßig `private` und wird nicht veröffentlicht.

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
