# Externe Kontaktupdates und CardDAV-Datenerhalt

## Externer Aktualisierungslink

Ein Bearbeiter kann für einen Kontakt einen einmal verwendbaren
Aktualisierungslink erzeugen. Das öffentliche Formular ändert den Kontakt nicht
direkt, sondern erstellt einen internen Vorschlag. Bearbeitbar sind Name,
Organisation, Funktion, mehrere E-Mail- und Telefonarten, Fax, Geburtstag,
Webseite, Notizen, Tags sowie eine strukturierte Adresse.

Adressfelder stehen immer in der Reihenfolge Ort, PLZ, Straße. Ab drei Zeichen
fragt das Formular den lokalen OSM-Index über einen tokengebundenen Endpunkt ab.
Der Endpunkt gibt höchstens acht Vorschläge zurück und ist nach Verwendung des
Links nicht mehr erreichbar.

Die interne Freigabeansicht zeigt aktuellen und vorgeschlagenen Wert
nebeneinander. Jedes Feld kann getrennt übernommen werden. Auch ein leerer Wert
ist ein ausdrücklicher Vorschlag und kann so ein vorhandenes Feld entfernen.
Nicht vorgeschlagene oder nicht ausgewählte Felder bleiben unverändert.

## Schutz vor Feldverlust

Die Übernahme nutzt einen atomaren Feld-Patch statt den vollständigen Kontakt
aus dem Formular neu aufzubauen. Dadurch bleiben insbesondere erhalten:

- nicht im Formular enthaltene benutzerdefinierte Felder
- unbekannte Thunderbird-/vCard-Eigenschaften
- zusätzliche E-Mail-Adressen und Telefonnummern
- Gruppen, wenn nur Tags geändert werden
- Eigentümer, Freigaben, interne Adressen und Änderungshistorie

Nur Benutzer mit Bearbeitungsrecht für den jeweiligen Kontakt dürfen Vorschläge
sehen, annehmen oder ablehnen. Erzeugung, einmalige Nutzung und Auflösung der
Vorschläge sind gegen parallele Schreibzugriffe gesperrt.

## CardDAV-Roundtrip

Beim CardDAV-Import werden rohe Mehrfachfelder intern neu nummeriert. Der
Abgleich verwendet deshalb nicht mehr nur den Eigenschaftsnamen und verlässt
sich nicht auf diese interne Nummer. Er vergleicht Eigenschaft, Parameter und
vorhandene Werte als Multimenge. Bei einer Schlüsselüberschneidung erhält ein
bewahrtes Feld einen freien internen Schlüssel.

Damit überlebt zum Beispiel der Ablauf

`CardDAV -> SimpleOffice -> externer Vorschlag -> Freigabe -> CardDAV`

auch mit mehreren `EMAIL`, `TEL`, `ADR`, `IMPP` und unbekannten
Thunderbird-Eigenschaften ohne stillen Feldverlust.
