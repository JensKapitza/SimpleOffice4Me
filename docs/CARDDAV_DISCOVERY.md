# CardDAV-Autoerkennung für Thunderbird

## Zweck und Nutzen

SimpleOffice stellt die standardisierte CardDAV-Erkennung bereit, damit
Thunderbird und andere DAV-Clients aus der Serveradresse selbstständig den
angemeldeten Benutzer und dessen Standardadressbuch finden können. Benutzer
müssen dadurch nicht mehr zwingend den vollständigen Pfad
`/carddav/addressbooks/<benutzer>/default/` kennen.

## Bedienung und Konfiguration

CardDAV wird wie bisher unter **Kontakte** mit einem separaten App-Passwort
aktiviert. Im Client genügen Servername, Web-Benutzername und App-Passwort,
sofern der Client RFC-6764-Autoerkennung unterstützt. Der Server leitet
`/.well-known/carddav` mit HTTP 307 auf `/carddav/` um. Anschließend liefern
WebDAV-Eigenschaften den aktuellen Principal, dessen Adressbuch-Home und das
Standardadressbuch. Die direkte, bisher angezeigte Adressbuch-URL funktioniert
weiterhin.

Bei Betrieb hinter einem Reverse Proxy müssen HTTPS und Hostname wie in
`PROXY_HTTPS.md` beschrieben korrekt weitergegeben werden. Es gibt keine neue
Umgebungsvariable. Optionale DNS-SRV- oder TXT-Einträge sind nicht erforderlich
und werden von SimpleOffice nicht automatisch verwaltet.

## Sicherheit, Datenschutz, Rechte und Freigaben

Der Well-known-Endpunkt gibt ohne Anmeldung nur einen Redirect zum
CardDAV-Kontext zurück. Principal-, Home- und Adressbuchinformationen benötigen
weiterhin gültige HTTP-Basic-Anmeldung mit dem CardDAV-App-Passwort. Anfragen
nach dem Principal oder Adressbuch eines anderen Benutzers erhalten HTTP 404.
Kontakte werden ausschließlich nach der bestehenden Eigentümer- und
Verwalterprüfung ausgeliefert; die Discovery lockert keine Freigabe.

Zugangsdaten werden weder im Redirect noch in XML-Antworten ausgegeben. Für
Netzbetrieb ist HTTPS erforderlich, da HTTP Basic sonst keinen ausreichenden
Transportschutz bietet. Es werden keine Daten an externe Dienste übertragen.

## Protokoll- und Formatkompatibilität

Die Erkennung implementiert `/.well-known/carddav` nach RFC 6764,
`DAV:current-user-principal` nach RFC 5397 und
`CARDDAV:addressbook-home-set` nach RFC 6352. Das Standardadressbuch meldet
zusätzlich `text/vcard` in Version 4.0. Bestehende Methoden `OPTIONS`,
`PROPFIND`, `REPORT`, `GET`, `PUT` und `DELETE` sowie ETags bleiben unverändert.

## Fehler- und Ausfallverhalten

Ohne oder mit falschem App-Passwort antwortet der CardDAV-Kontext weiterhin
mit HTTP 401 und einer Basic-Auth-Challenge. Unbekannte und fremde Discovery-
Pfade liefern HTTP 404. Der Redirect wird eine Stunde cachebar ausgeliefert;
nach einer Änderung des externen Proxy-Pfads kann ein Client daher kurzzeitig
den alten Pfad verwenden. Die direkte Adressbuch-URL bleibt als Rückfalloption.

## Tests und bekannte Grenzen

Automatisierte Tests prüfen Redirect und Cache-Header, Principal-, Home- und
Adressbuch-Erkennung, vCard-4.0-Ankündigung sowie die Isolation fremder
Principals. DNS-SRV/TXT-Veröffentlichung, mehrere Adressbücher pro Benutzer,
Adressbuch-Erstellung per `MKCOL` und CalDAV sind nicht Bestandteil dieser
Änderung.

## Deaktivierung und Rückkehr

Zur Deaktivierung kann auf die vorherige Version zurückgegangen werden oder der
Proxy den Well-known-Pfad nicht veröffentlichen. Es gibt keine Migration, keine
neuen Geheimnisse und keine Änderung an Kontakt-, Freigabe- oder
Aufbewahrungsdaten.
