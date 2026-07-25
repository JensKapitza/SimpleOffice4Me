# Kontakte und Thunderbird

Kontakte liegen dateibasiert in `.simpleoffice-meta/contacts.json`; ihre
Änderungen werden zusätzlich im lokalen Revisions-Git gespeichert.

## Feldmodell und fremde APIs

Die Anwendung arbeitet intern mit kanonischen Feldern wie `first_name`,
`last_name`, `display_name`, `email`, `phone`, `birthday` und `company`.
Unter `/documents/contacts` ist konfigurierbar, welche Eingabe- oder API-Keys
auf welches kanonische Feld zeigen. Damit können etwa `Vorname`, `givenName`
und `first_name` denselben Wert liefern. Die Pflichtfelder sind ebenfalls
konfigurierbar. Zusätzliche Werte werden über `custom_<kennung>` gespeichert.

## Thunderbird

Ein Kontakt kann als vCard 4.0 (`.vcf`) exportiert und direkt in Thunderbird
importiert werden. Zusätzlich kann CardDAV unter `/documents/contacts`
aktiviert werden. Die Oberfläche erzeugt ein separates App-Passwort; dieses
ist vom Web-Login getrennt und wird nur als scrypt-Hash abgelegt. Thunderbird
erhält die dort angezeigte HTTPS-URL, den Web-Benutzernamen und dieses
App-Passwort.

Der Endpunkt unterstützt das Standard-Adressbuch mit `PROPFIND`, `REPORT`,
`GET`, `PUT` und `DELETE`, inklusive ETags. Ein öffentlich erreichbarer
CardDAV-Endpunkt muss hinter HTTPS betrieben werden.
