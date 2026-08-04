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

### Konfliktschutz bei parallelen Änderungen

CardDAV wertet bei `PUT` und `DELETE` die HTTP-Vorbedingungen `If-Match` und
`If-None-Match: *` aus. Ein Client kann damit nur den Stand ändern, dessen ETag
er zuvor gelesen hat. Wurde der Kontakt zwischenzeitlich im Browser, durch
einen anderen Benutzer oder ein anderes Gerät geändert, antwortet der Server
mit `412 Precondition Failed` und dem aktuellen ETag. Die neuere Fassung bleibt
unverändert; Thunderbird beziehungsweise der DAV-Client kann sie neu laden und
den Konflikt anzeigen oder erneut zusammenführen. `If-None-Match: *` verhindert
außerdem, dass das Anlegen eines Kontakts eine bereits vorhandene Ressource
gleichen Namens überschreibt.

Die Prüfung gilt nur für Kontakte, die der CardDAV-Benutzer bereits sehen und
bearbeiten darf. Sie lockert keine Freigabe und schreibt weder Passwörter noch
Kontaktdaten in Protokolle. Clients ohne HTTP-Vorbedingung funktionieren aus
Kompatibilitätsgründen wie bisher; für sicheren Mehrgerätebetrieb sollte ein
Client ETags und bedingte Schreibzugriffe verwenden. Es gibt keine zusätzliche
Konfiguration. Durch Rückkehr zur vorherigen Programmversion lässt sich die
Prüfung deaktivieren; Datenformat und gespeicherte Kontakte bleiben kompatibel.

Getestet werden erfolgreiche bedingte Anlage, Änderung und Löschung sowie die
Ablehnung veralteter ETags ohne Datenverlust. Bekannte Grenze: Die Anwendung
führt bei einem Konflikt keine automatische Feldzusammenführung durch. Das ist
bewusst Aufgabe des Clients oder Benutzers, damit keine Kontaktdaten unbemerkt
verworfen werden.

Protokollgrundlage sind CardDAV nach RFC 6352 und die HTTP-Vorbedingungen nach
RFC 9110.

## Bearbeiten und gemeinsam verwalten

Ein Klick auf eine Kontaktkarte öffnet die Detailansicht. Dort lassen sich
Stammdaten und Adressen bearbeiten. Der Eigentümer kann weitere registrierte
SimpleOffice-Benutzer als Verwalter auswählen. Diese Benutzer sehen den Kontakt
anschließend in ihrer Kontaktliste und können ihn sowohl im Browser als auch
über ihr eigenes CardDAV-Adressbuch bearbeiten. Nur der Eigentümer darf die
Freigaben ändern.

Feldänderungen enthalten Altwert, Neuwert, Zeitpunkt und handelnden Benutzer.
Zusätzlich bleibt jede Änderung als Git-Revision erhalten. Parallele
Thunderbird-Synchronisationen werden serialisiert, damit weder `contacts.json`
noch der Git-Index durch gleichzeitige `PUT`-Anfragen beschädigt werden.
