# Bibliothek und Etikettendruck

SimpleOffice4Me nutzt für Bücher weiterhin den normalen `ObjectStore`. Die Bibliothek legt deshalb kein zweites Inventar an, sondern ergänzt Inventarobjekte um einen strukturierten Bibliotheksstandort und stellt dafür kurze Arbeitsabläufe bereit.

## Ein Regal erfassen

1. Unter **Bibliothek** einen Raum, ein Regal oder ein Fach anlegen.
2. Der Standort erhält einen dauerhaften Code wie `LIB-L0001`; alternativ kann ein eigener Code vergeben werden.
3. **Barcode drucken** erzeugt ein Code-128-Etikett für diesen Standort.
4. Unter **Ein Regal voller Bücher erfassen** das Regal einmal auswählen oder dessen Barcode scannen.
5. Danach ISBN-/Barcodes der Bücher nacheinander scannen. Der aktive Standort bleibt im Browser erhalten, bis er gewechselt wird.
6. Bereits bekannte Bücher werden sofort dem Standort zugeordnet. Noch unbekannte Bücher werden zur Inventar-Schnellerfassung weitergeleitet.

Die lesbare Standortbezeichnung wird zusätzlich in das normale `location`-Feld des Inventarobjekts geschrieben. Dadurch bleibt die vorhandene Objekt- und Schnellsuche nutzbar. Die dauerhafte Standort-ID und der Standortcode werden zusätzlich als strukturierte Felder gespeichert.

## Buch wiederfinden

Die Bibliothekssuche akzeptiert unter anderem ISBN, Barcode, Inventarnummer und Titel. Bei einem Treffer wird der vollständige Standortpfad, z. B. `Wohnzimmer / Regal 2 / Fach B`, angezeigt.

## Brother-Drucker

Die sichtbare Druckoberfläche ist Bestandteil von SimpleOffice4Me. Die alten Projekte `brother_ql` und `brother_ql_web` werden nicht als Laufzeitabhängigkeit eingebunden; sie dienten zusammen mit der dokumentierten Brother-Rastersprache als Referenz.

Unterstützte Modellprofile umfassen aktuell:

- QL-500, QL-550, QL-560, QL-570, QL-580N, QL-650TD
- QL-700, QL-710W, QL-720NW
- QL-800, QL-810W, QL-820NWB
- QL-1050, QL-1060N
- QL-1100, QL-1110NWB, QL-1115NWB
- PT-P750W und PT-P900W

Die Oberfläche kann Text, Code-128-Barcodes und Bilder rendern. Schrift, Schriftgröße, Farbe, Ausrichtung, Fettdruck, Etikettenformat und automatischer Schnitt werden gespeichert und gelten für folgende Druckaufträge weiter.

### Verbindungen

**WLAN/LAN**

`tcp://192.168.1.50:9100`

Die Anwendung sendet Brother-Rasterdaten direkt an Port 9100. Aus Sicherheitsgründen sind nur lokale/private, Loopback- und Link-Local-Ziele zulässig; öffentliche Internet-Adressen werden abgewiesen.

**USB unter Linux**

`usb:///dev/usb/lp0`

Alternativ wird `/dev/lp0` unterstützt. Das Benutzerkonto des SimpleOffice-Prozesses benötigt Schreibrechte auf dem Gerät. Es wird bewusst kein allgemeiner Dateipfad akzeptiert.

**Bluetooth RFCOMM**

`bluetooth://AA:BB:CC:DD:EE:FF/1`

Der letzte Wert ist der RFCOMM-Kanal. Bluetooth funktioniert nur, wenn Betriebssystem, Python-Laufzeit und Berechtigungen RFCOMM bereitstellen. Insbesondere auf Android/Termux hängt die direkte Nutzbarkeit von den Geräte- und Android-Berechtigungen ab; die Anwendung behauptet daher nicht, dass jeder Brother-Bluetoothdrucker ohne Systemkonfiguration direkt erreichbar ist.

**Automatische Auswahl**

`auto://`

Die Erkennung ist absichtlich nur Best-Effort und führt keinen Netzwerkscan durch. Sie berücksichtigt lokale `/dev/...`-Druckgeräte, vorhandene CUPS/socket-Einträge und bereits vom Betriebssystem bekannte Brother-Bluetoothgeräte. Ein Netzwerkdrucker kann jederzeit direkt per IP eingetragen werden.

## Zweifarbdruck

Rotdruck ist nur mit einem zweifarbfähigen QL-8xx-Modell und dem passenden `62red`-Medium vorgesehen. Bei unpassender Kombination wird der Auftrag abgewiesen, statt möglicherweise ein falsches Etikett zu erzeugen.

## Protokollierung

Bibliotheksaktionen erhalten ein kurzes, für Anwender sichtbares Verlaufsprotokoll mit Zeitpunkt und Benutzer. Erfasst werden insbesondere:

- Standort angelegt
- Buch einem Standort zugeordnet
- Druckeinstellungen geändert
- Druckauftrag erfolgreich oder fehlgeschlagen

Der Verlauf enthält keine Bilddaten und wird auf die letzten 500 Bibliotheksereignisse begrenzt. Zusätzlich bleiben die normalen ObjectStore-Revisionen für Änderungen am Inventar erhalten.

## Sicherheitsgrenzen

- Druckerziele werden validiert; Netzwerkdrucke dürfen nicht als beliebiger SSRF-Kanal ins Internet verwendet werden.
- Bild-Uploads für Etiketten sind auf 12 MiB begrenzt.
- Alle Browser-Schreibaktionen verwenden den vorhandenen CSRF-Schutz.
- Druckeinstellungen enthalten keine Kennwörter. Falls ein Druckweg künftig Zugangsdaten benötigt, gehören diese in den bestehenden Secret-/Credential-Mechanismus und nicht in `library/state.json`.


## Gesamt- und Auswahl-Export

Administratoren erreichen CSV, RIS (Zitavi) und BibTeX im Bibliothekskopf.
Die Checkboxen bei **Bücher & Standorte** wählen einzelne Exemplare aus;
ohne Auswahl exportiert das jeweilige Format den gesamten gültigen Buchbestand.
Der Gesamtexport besitzt kein 500-Zeilen-Limit der Übersichten. Mehrere Exemplare
mit derselben ISBN bleiben eigene Datensätze mit ihrer Inventarnummer.

Der bestehende physische ObjectStore ist ein gemeinsamer Instanzbestand.
Gesamt- und Auswahl-Exporte erfordern die zentrale Administratorberechtigung;
reguläre Benutzer erhalten HTTP 403. Daten werden ausschließlich aus dem
konfigurierten Dokumenten-Root gelesen. Benutzer-, Root- oder Tenant-Angaben in
URL-Parametern ändern weder Rolle noch Datenquelle. `created_by` bleibt
Erstellungsprovenienz, es wird keine parallele Objekt-Rechteverwaltung eingeführt.

Die öffentlichen APIs sind `GET /library/export/books.csv`,
`GET /library/export/books.ris`, `GET /library/export/books.bib` und
`GET /library/export/books.calibre.zip`. Wiederholte
`object_id`-Parameter wählen UUIDs oder bestehende Inventarnummern aus. Doppelte
Referenzen werden zusammengeführt. Maximal 500 Referenzen pro Auswahl sind
zulässig (darüber HTTP 400); unbekannte, ungültige, außerhalb der Datenquelle
liegende oder Nicht-Buch-Referenzen ergeben HTTP 404 vor der Ausgabe. Alle drei
Textformate und das Calibre-Archiv verwenden dieselbe zentrale
Administratorprüfung und ausschließlich den konfigurierten Dokumenten-Root.
Ohne Parameter umfassen die Exporte alle Buchdatensätze. Textformate werden
zeilenweise ausgegeben. Bestehende Standortzuordnungen liefern Standortpfad und
Regalcode auch ohne kopierte Standortfelder im Objekt. Der Standortindex wird
einmal pro Export gelesen; Legacy-Inventarnummern behalten ihre bisherige
Vergabereihenfolge.

Spalten: `ID`, `Titel`, `ISBN`, `Autoren`, `Genre`, `Reihe`, `Band`, `Standort`,
`Regalcode`. Ausgabe: UTF-8 mit BOM, CSV-Quoting, feste Dateibezeichnung,
`Cache-Control: no-store` und `X-Content-Type-Options: nosniff`. Formelanfänge
`=`, `+`, `-`, `@` werden auch hinter führenden Steuerzeichen, Leerraum oder BOM
mit einem Apostroph geschützt. Die Originalwerte in der Bibliothek bleiben
unverändert. Unbekannte Zusatzfelder werden nicht exportiert. Der Export ist eine
Bibliotheksübersicht; die bestehende Datensicherung wird weiterhin separat genutzt.

Der zentrale Audit erfasst Benutzer, Ergebnis und Zahl der erzeugten Zeilen,
keine Titel, Bilder oder fachlichen Nutzdaten. Erfolg entsteht erst nach vollständiger
Erzeugung; ein vorzeitig beendeter Stream wird als unvollständig protokolliert.
Eine erzeugte Ausgabe ist keine Bestätigung, dass der Client sie gespeichert hat.

Das Calibre-ZIP enthält `metadata.csv` mit Calibres Standardspalten für Titel,
Autoren, Reihe/Band, Verlag, Jahr, ISBN, Genre, Beschreibung und Sprache sowie
eine OPF-Metadatendatei pro Buch unter `opf/`. Die OPF-Dateien übernehmen auch
Inventarnummer, Standort, Seitenzahl und Ausgabe als Beschreibung; Reihe und
Band stehen in Calibre-Metafeldern. Das Archiv enthält keine Originaldateien.
Der Export wird
mit einem begrenzten Speicherpuffer aus einem einzigen Bestandsdurchlauf
aufgebaut, nutzt die gleiche Auswahlprüfung und protokolliert nur Format,
Zeilenzahl und Ergebnis. Fehlende Sprachangaben werden in OPF als `und`
gekennzeichnet; die gespeicherten Quelldaten bleiben unverändert.
XML-1.0-unzulässige Steuerzeichen werden in Calibre-Feldern ausgelassen;
CSV-Formelanfänge bleiben wie im vorhandenen Export mit Apostroph neutralisiert.

RIS verwendet `TY=BOOK`, `T1` für Titel, wiederholte `AU`-Zeilen für Autoren,
`SN` für ISBN, `PB` für Verlag, `PY` für Jahr, `LA` für Sprache, `ET` für
Ausgabe, `T3`/`VL` für Reihe/Band, `KW` für Genre und `AB` für Beschreibung.
Inventarnummer, Standort und Seitenzahl werden verlustarm als `N1`-Notiz
mitgeführt. BibTeX verwendet einen stabilen, aus der Inventar-ID gebildeten
`@book`-Schlüssel und die klassischen Felder `title`, `author`, `publisher`,
`year`, `edition`, `series`, `volume` und `note`; Metazeichen werden im Feldwert
escaped. ISBN, Sprache, Genre, Beschreibung, Seitenzahl und Inventar-/Standort
bleiben zusätzlich in `note`, damit Importer ohne herstellerspezifische
Erweiterungsfelder keine dieser Angaben verlieren. Seitenzahl wird nicht als
erfundener Seitenbereich ausgegeben. Beide Textformate normalisieren
Zeilenumbrüche, beginnen mit UTF-8-BOM und verwenden feste Downloadnamen.
Unbekannte Zusatzfelder und administrative Secrets werden nicht exportiert.

## Lokale Buchseiten-Vorschau und OCR

Im Massen-Scan können Fotos sowie optional einzelne PDF-Seiten vorbereitet werden.
JPEG, PNG, WEBP, TIFF und BMP werden erkannt. Eingaben sind auf 12 MiB und
24 Megapixel begrenzt; Bildbomben und ungültige Seiten sind HTTP-400-Eingabefehler.
Fehlendes CSRF ergibt wie bei anderen geschützten Schreib-APIs HTTP 403.
Vorschauen verwenden PNG und höchstens 2000 Pixel an der längsten Seite.
Orientierung, Schräglage und Kontrast werden nur in der Vorschau geändert;
das Original wird nicht ersetzt. Nach Wechsel von Datei oder Optionen werden
alte Resultate verworfen, damit schnell folgende Bücher nicht vermischt werden.

PDF-Rasterung verwendet das bereits für Dokumentvorschauen genutzte optionale
Systemwerkzeug `pdftoppm`: eine ausgewählte Seite (1–2000), höchstens 15 Sekunden,
private temporäre Dateien und keine neue Python-Parser-Abhängigkeit. Fehlende
Werkzeuge oder unlesbare/geschützte PDFs benötigen eine Bilddatei als Fallback.
Lokale Texterkennung nutzt den bestehenden Tesseract-Service mit 15 Sekunden
pro Sprachversuch (höchstens zwei Versuche). Bestehende andere OCR-Aufrufer
behalten ihr Zeitbudget. Die Oberfläche zeigt nicht verfügbare Funktionen an.

Pro Instanz läuft nur eine Bild-/OCR-Anfrage gleichzeitig über die vorhandene
Kontroll-Lease; parallele Anfragen erhalten HTTP 503 mit Wiederholungshinweis.
Die APIs `/library/ocr/optimize` und `/library/ocr/analyze` verlangen Anmeldung
und CSRF. Vorschau, erkannter Text und Tag-Vorschläge werden nicht automatisch
als Buch oder Dokument gespeichert. Text und Vorschläge müssen vom Benutzer
geprüft und bewusst übernommen werden; OCR-Ausfälle veröffentlichen keine
Parser-/Prozessdetails oder Bilder im Diagnoseprotokoll.

Diese Erweiterung verwendet die vorhandenen Inventar-, Standort-, Druck-, Auth-
und OCR-Dienste. Die weiteren Anforderungen aus #658, insbesondere getrennte
Werk-/Ausgabenmodelle, Metadatenanbieter, reale Kamera-/Drucker- und Android-
Abnahme sowie Zitavi-Roundtrip-Importtests bleiben offen und werden durch diese
Exportformate nicht als abgenommen markiert.

NUL-Zeichen werden nur im CSV-Ausgabewert entfernt; gespeicherte Metadaten
bleiben unverändert. Das ermöglicht den Export auch mit dem unterstützten
Ubuntu-22.04-System-Python 3.10. Die Formelabsicherung greift danach weiterhin.
