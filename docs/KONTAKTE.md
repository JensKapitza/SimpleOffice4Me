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
importiert werden. Für eine echte laufende Zwei-Wege-Synchronisation benötigt
Thunderbird einen CardDAV-Endpunkt. Dieser wird als eigene, authentifizierte
Schnittstelle umgesetzt, weil CardDAV mit Passwortwechseln, Löschkonflikten,
ETags und Berechtigungen korrekt arbeiten muss. Ein unsicherer Web-Upload oder
ein offen erreichbares JSON-API wäre dafür keine sinnvolle Alternative.
