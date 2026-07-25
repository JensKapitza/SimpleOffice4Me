# Kalender

Der Kalender liegt dateibasiert in `.simpleoffice-meta/calendar.json`. Termine
haben Titel, Beginn, optionales Ende und optional eine Kontaktverknüpfung.
Jede Änderung wird dem angemeldeten Benutzer zugeordnet und als Git-Revision
gesichert.

Die Oberfläche unter `/documents/calendar` ist für interne Termine aktiv.
Der nächste Ausbauschritt für Thunderbird ist ein aktivierbarer CalDAV-Endpunkt
mit denselben App-Passwörtern wie CardDAV. Er wird getrennt ergänzt, damit
Kontakt- und Kalenderkonflikte nicht unkontrolliert vermischt werden.
