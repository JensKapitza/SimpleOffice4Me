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
`GET`, `PUT` und `DELETE`. `addressbook-multiget` und
`addressbook-query` mit den üblichen Property-/Text-/Parameterfiltern werden
ausgewertet. ETags werden aus der tatsächlich ausgelieferten vCard berechnet;
auch der Collection-CTag ändert sich deshalb, wenn sich nur die globale
vCard-Feldfreigabe ändert. Ein öffentlich erreichbarer CardDAV-Endpunkt muss
hinter HTTPS betrieben werden.

### Kompatibler vCard-Import

Beim Dateiimport und bei CardDAV-`PUT` werden gefaltete vCard-Zeilen vor der
Auswertung wieder zusammengesetzt. Maskierte Textzeichen wie `\\,`, `\\;`,
`\\\\` und `\\n` werden dekodiert; Feldgruppen wie `item1.EMAIL` werden dem
eigentlichen Feld `EMAIL` zugeordnet. Dadurch bleiben Namen und Firmen mit
Kommas oder Semikolons sowie mehrzeilige Anzeigenamen aus Thunderbird, Google
Kontakte und anderen vCard-Anwendungen erhalten. Strukturierte Namensfelder
werden nur an nicht maskierten Semikolons getrennt.

Als Eingabe werden vollständige UTF-8-vCards der Versionen 3.0 und 4.0
akzeptiert. Jede Einzelkarte muss genau ein `BEGIN:VCARD`, direkt danach eine
unterstützte `VERSION`, mindestens ein `FN` und genau ein abschließendes
`END:VCARD` enthalten. Unvollständige, verschachtelte oder nicht unterstützte
Karten werden vor einer Änderung abgewiesen. Der CardDAV-Server veröffentlicht
als auslieferbares Format ausschließlich vCard 4.0, weil auch der kanonische
SimpleOffice-Export 4.0 erzeugt.

Unbekannte Erweiterungen und zusätzliche Standardfelder bleiben roundtrip-fähig.
Eingebettete Base64-Fotos in PNG, JPEG, GIF oder WebP werden mit Typ- und
Größenprüfung übernommen; der dekodierte Bildinhalt ist auf 8 MiB begrenzt.
Quoted-Printable aus älteren vCard-2.1-Dateien gehört weiterhin nicht zum
unterstützten Importformat. Lange Exportzeilen werden UTF-8-sicher nach RFC
6350 auf höchstens 75 Octets gefaltet.

`ADR` wird nicht mehr nur als rohe vCard-Zeile konserviert, sondern in die
strukturierten SimpleOffice-Adressen übernommen. Postfach, erweiterte
Adressangabe, Straße, Ort, Region, Postleitzahl und Land bleiben dabei erhalten.
Beim erneuten Export wird daraus genau eine ADR-Eigenschaft erzeugt, sodass
keine Doppeladressen entstehen.

Eine externe `UID` bleibt als vCard-Identität erhalten. Für den internen
Web-/CardDAV-Ressourcenpfad werden URI-artige oder anderweitig ungeeignete UIDs
deterministisch auf eine sichere lokale ID abgebildet; beim Export erscheint
weiter die ursprüngliche UID. Dadurch können beispielsweise `urn:uuid:...`
oder UIDs mit Schrägstrichen keine ungültigen Ressourcenpfade erzeugen.

Der Import verwendet weiterhin Rechteprüfung, UID-basierte Aktualisierung und
Audit-Historie und überträgt keine Daten an externe Dienste. Bei CardDAV gelten
ausgelieferte Standard-vCard-Eigenschaften als vom Client verwaltbar: entfernt
ein Client beispielsweise eine zusätzliche E-Mail, Telefonnummer, ADR, PHOTO
oder IMPP-Eigenschaft, wird diese Entfernung übernommen. Nicht standardisierte
serverseitige Erweiterungen bleiben dagegen erhalten. Felder, die durch die
vCard-Freigabepolitik verborgen sind, werden durch einen CardDAV-`PUT` niemals
gelöscht.

Grundlage sind vCard 4.0 nach RFC 6350 und CardDAV nach RFC 6352.

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
