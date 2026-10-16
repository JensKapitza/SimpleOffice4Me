# Bibliothek und Etikettendruck

SimpleOffice4Me nutzt für Bücher weiterhin den normalen `ObjectStore`. Die Bibliothek legt deshalb kein zweites Inventar an, sondern ergänzt Inventarobjekte um einen strukturierten Bibliotheksstandort und stellt dafür kurze Arbeitsabläufe bereit.

## Werk, Ausgabe und physisches Exemplar

In der Bücherliste legt die Kopieren-Aktion ein weiteres physisches Exemplar an. Beim ersten Aufruf erhält das Ausgangsbuch stabile UUIDs für Werk, Ausgabe und Exemplar. Das neue Inventarobjekt übernimmt Werk- und Ausgabe-ID sowie ISBN und bibliografische Felder, erhält aber eine eigene Exemplar-ID und Inventarnummer. Standort, individueller Barcode, Serien-/Inventarnummer, Dokumentanhänge und Notizen werden nicht kopiert. Das neue Exemplar bleibt daher zunächst **nicht zugeordnet** und kann anschließend bewusst einem Regal zugewiesen werden.

Die Felder `library_work_id`, `library_edition_id` und `library_copy_id` liegen additiv im bestehenden ObjectStore-Datensatz; `library_copy_source_id` dokumentiert nur den Ursprung der Kopieraktion. Der ObjectStore bleibt die Autorität für physische Bücher. Bestehende Datensätze werden nicht im Hintergrund migriert, sondern erst bei einer ausdrücklichen Kopieraktion ergänzt. Alte Clients ignorieren die Zusatzfelder und bleiben kompatibel. Mehrere Exemplare derselben ISBN sind ausdrücklich zulässig und bleiben durch Objekt- und Exemplar-ID unterscheidbar.

Rollback benötigt keine Datenmigration: Die neuen Felder können von älteren Versionen ignoriert werden. Ein bereits bewusst angelegtes physisches Exemplar wird nicht automatisch gelöscht; es bleibt ein reguläres Inventarobjekt und kann dort verwaltet werden.


Über die Aktion **Exemplare anzeigen** in der Bücherliste werden ausschließlich
Objekte mit derselben expliziten `library_edition_id` zusammengeführt. Eine
gleiche ISBN allein verknüpft keine Datensätze, damit unabhängige Altbestände
nicht versehentlich zu einer Ausgabe verschmolzen werden. Angezeigt werden
Inventarnummer und aktueller Standort; nicht zugeordnete Exemplare bleiben
sichtbar. Der angemeldete JSON-Vertrag
`GET /library/books/<object_id>/copies` liefert Werk-/Ausgabe-ID und höchstens
500 Exemplare mit ihren stabilen Objekt-/Exemplar-IDs. Antworten sind
`no-store`; unbekannte oder Nicht-Buch-Objekte ergeben HTTP 404.

Die Abfrage ist rein lesend. Ein Altbestand ohne Identitätsfelder erscheint als
ein einzelnes Exemplar und wird durch das Anzeigen weder migriert noch
verändert. Dadurch ist ein Rollback weiterhin ohne Datenbereinigung möglich.

## Ein Regal erfassen

1. Unter **Bibliothek** einen Raum, ein Regal oder ein Fach anlegen.
2. Der Standort erhält einen dauerhaften Code wie `LIB-L0001`; alternativ kann ein eigener Code vergeben werden.
3. **Barcode drucken** erzeugt ein Code-128-Etikett für diesen Standort.
4. Unter **Ein Regal voller Bücher erfassen** das Regal einmal auswählen oder dessen Barcode scannen.
5. **Sitzung starten** wählen. Danach ISBN-/Barcodes der Bücher nacheinander scannen.
6. Bereits bekannte Bücher werden sofort dem Standort zugeordnet. Noch unbekannte Bücher werden zur Inventar-Schnellerfassung weitergeleitet und als offen gezählt.
7. Mit **Pause** wird die Erfassung gesperrt. **Fortsetzen** stellt dieselbe Sitzung nach einem Seitenneuladen oder einer erneuten Anmeldung wieder her; **Abschließen** verwirft nur den Sitzungsfortschritt, nicht die bereits erfolgten Standortzuordnungen.

Der Sitzungsstatus ist an den angemeldeten Benutzer und einen festen Standort
gebunden. Er enthält Zähler sowie höchstens 25 gekürzte Ergebniszeilen und liegt
serverseitig unter
`.simpleoffice-meta/library/scan-sessions.json`. Ein Eintrag speichert keine
Kameraaufnahmen, OCR-Inhalte oder Klartext-Barcodes. Zufällige Scan-IDs machen
wiederholte Browseranfragen idempotent; eine pausierte, fremde, veraltete oder
auf einen anderen Standort gerichtete Sitzung wird vor der Buchänderung
abgewiesen. Gleichzeitige Benutzer erhalten getrennte Sitzungen. Der temporäre,
benutzergebundene Sitzungsfortschritt wird nicht über das generische S3-Overlay
bereitgestellt.

Für einen sofortigen Rollback kann die Anwendung gestoppt und ausschließlich
`scan-sessions.json` gelöscht werden. Dadurch gehen nur noch offene
Sitzungszähler verloren. Bücher, ObjectStore-Revisionen und
Standortzuordnungen bleiben unverändert. Die neue Datei benötigt keine
Datenmigration; Installationen ohne sie beginnen mit keiner aktiven Sitzung.

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


## Metadatenquellen und Konflikte

Die ISBN-Abfrage verwendet weiterhin zuerst Open Library und nur bei fehlendem Titel Google Books als Fallback. Die Antwort enthält neben den übernommenen Feldern bis zu drei gekürzte Kandidatenwerte der tatsächlich abgefragten Quelle. Stimmen Titelwerte nicht überein, zeigt die Erfassungsmaske einen Konflikthinweis mit den Quellen. Kandidaten werden nicht automatisch gespeichert; vorhandene manuelle Werte bleiben geschützt, solange „Vorhandene Buchdaten ersetzen“ nicht aktiviert ist. Nicht erreichbare oder unvollständige Anbieter werden als Hinweis gemeldet, ohne rohe Provider-Antworten zu speichern oder anzuzeigen.

Erfolgreiche ISBN-Antworten werden pro Instanz für 24 Stunden lokal wiederverwendet. Der Cache ist auf 500 ISBNs begrenzt und enthält ausschließlich die normalisierten Anzeigefelder sowie höchstens drei gekürzte Quellenkandidaten; Provider-Rohantworten, Fehlerdetails und Zugangsdaten werden nicht gespeichert. Ein frischer Treffer wird vor externen Anfragen verwendet und verbraucht deshalb kein Provider-Rate-Limit. Sind alle Anbieter nicht erreichbar, darf ein höchstens 30 Tage alter Treffer mit einem sichtbaren Offline-Hinweis angezeigt werden. Ein erreichbarer Anbieter ohne passenden Datensatz führt dagegen weiterhin zu „nicht gefunden“ und reaktiviert keine alten Angaben. Browserantworten bleiben `no-store`; der serverseitige Cache ändert keine Buchobjekte und kann für ein sofortiges Rollback durch Stoppen der Anwendung und Löschen von `.simpleoffice-meta/inventory/book-metadata-cache.json` geleert werden.

## Bücheretiketten für eine Auswahl

In **Bücher & Standorte** können angemeldete Benutzer eine oder mehrere Buchzeilen auswählen und **Etiketten für Auswahl drucken** auslösen. Es werden höchstens 100 verschiedene Buchobjekte je Auftrag angenommen; Mehrfachauswahl derselben Objekt-ID erzeugt kein doppeltes Etikett. Die druckbare Kennung wird in dieser Reihenfolge aus vorhandenem Barcode, ISBN, Objektkennung, Inventarnummer oder stabiler Objekt-ID gewählt. Ein Objekt ohne nutzbare Kennung wird vor dem ersten Druck abgelehnt.

Die Etikettenart ist auswählbar: **Barcode mit lesbarem Text** verwendet Titel und Autoren als Bildunterschrift; **Nur Barcode** lässt den Klartext weg. Der vorhandene Code-128-Renderer und die konfigurierten Brother-Medien-/Druckereinstellungen werden wiederverwendet. Alle ausgewählten Objekte werden vor dem Druck geprüft. Ein physischer Stapeldruck ist nicht rückrollbar: Falls ein Drucker während des Auftrags ausfällt, meldet die API die Zahl der bereits gesendeten Etiketten und protokolliert Erfolg/Fehler pro Objekt. „Gesendet“ bestätigt wie bei anderen Brother-Aufträgen nicht, dass jedes Etikett mechanisch vollständig ausgegeben wurde.

Zusätzlich stehen **QR-Code mit lesbarem Text** und **Nur QR-Code** für Druck und PDF zur Verfügung. Der QR-Inhalt ist die dauerhafte UUID des Buchobjekts aus dem vorhandenen ObjectStore; Titel und ISBN werden nicht in den Code geschrieben. Dadurch bleibt der Code eindeutig, auch wenn Metadaten oder ISBN fehlen. Der bestehende angemeldete Scan-/Suchablauf `/library/find?q=<UUID>` kann den Code auflösen und liefert den regulären Buchtitel und Inventarlink. QR-Etiketten verwenden dieselben konfigurierten Medien und dieselbe Druckfreigabe wie Barcodes. Für sehr kleine Etiketten kann die lesbare Beschriftung entfallen, um die QR-Fläche zu vergrößern.

Für die gleiche Auswahl kann **Etiketten-PDF herunterladen** verwendet werden. Die PDF-Datei enthält je eindeutigem Buch eine Seite in der konfigurierten Brother-Medienbreite und -länge; bei Endlosmedien wird die Seitenlänge aus dem gerenderten Etikett abgeleitet. Alle vier Etikettenarten einschließlich QR-Code gelten auch für das PDF. Der Download benötigt Anmeldung und CSRF-Schutz und umfasst höchstens 100 Buchobjekte. Es wird kein Drucker angesprochen. Die gerätespezifische Passform und Ausrichtung müssen auf einem Ausdruck geprüft werden.

Für Etikettenbögen kann beim PDF-Download **A4-Bogen · 3 × 7** gewählt werden. Das Raster ist mittig auf A4 angeordnet und verwendet 21 Felder mit je 63,5 × 38,1 mm (gängiges 3×7-Bogenmaß). Weitere ausgewählte Bücher werden auf Folgeseiten ausgegeben. Die Auswahl, Kennungen, Text-/QR-Variante, Authentifizierung, CSRF-Prüfung und 100-Bücher-Grenze entsprechen dem normalen PDF-Download. Das Layout dient als Druckbogen; ein gerätespezifischer Druckversatz oder die Kompatibilität mit einem konkreten Etikettenbogen muss vor dem Stapeldruck auf einem Ausdruck geprüft werden.


## Calibre-Import

Administratoren können ein zuvor von SimpleOffice4Me erzeugtes Calibre-ZIP über **Calibre importieren** wieder einlesen. Unterstützt wird die standardisierte `metadata.csv` mit Titel, Autoren, Reihe/Band, Verlag, Jahr, ISBN, Genre, Beschreibung und Sprache; OPF-Dateien werden nicht blind ausgeführt oder extrahiert. Der Import ist auf 12 MiB Upload, 20 MiB entpackte Archivgröße und 500 Datensätze begrenzt, verwirft NUL-Zeichen und überspringt bereits vorhandene ISBNs. Neue Datensätze werden ausschließlich als Bücher im bestehenden ObjectStore angelegt und über den vorhandenen Auditpfad protokolliert. Der Import benötigt Anmeldung, Administratorrecht und CSRF-Schutz.

## Vorschau ausgewählter Buchetiketten

Neben dem Druck und dem PDF-Download bietet die Bibliotheksauswahl eine authentifizierte PNG-Vorschau. Sie rendert das erste ausgewählte Buch mit der aktuell gewählten Barcode-/QR-Variante und den gespeicherten Brother-Medieneinstellungen; die übrige Auswahl wird nicht gedruckt. Dadurch kann vor einem physischen Auftrag geprüft werden, ob Kennung, Klartext und QR-Fläche auf dem gewählten Medium passen. Die Vorschau unterliegt weiterhin Login-, CSRF-, Auswahl- und Kennungsprüfung und wird nicht dauerhaft gespeichert.

## Gefilterte Etikettenauswahl

Die Bibliotheksliste kann nach freier Suche, Autor, Genre und Standort gefiltert werden. Die Filter werden serverseitig angewendet und bleiben beim CSV-/RIS-/BibTeX-/Calibre-Export erhalten. Ohne manuelle Auswahl exportieren die Aktionen bis zu 500 passende Bücher; ausgewählte Zeilen haben Vorrang. Die sichtbaren Treffer können anschließend mit Barcode oder QR gedruckt, als PDF bzw. Etikettenbogen ausgegeben oder in der Vorschau geprüft werden. Ein Filter schaltet keine Rechte frei: Die bestehenden Anmelde-, CSRF-, Auswahl- und Objektprüfungen gelten unverändert.

## RIS-Import

Administratoren können UTF-8-kodierte RIS-Dateien mit bis zu 4 MiB und 500 `BOOK`-/`EBOOK`-Datensätzen über **RIS importieren** einlesen. Unterstützt werden Titel, mehrere Autoren, ISBN, Verlag, Jahr, Sprache, Auflage, Reihe/Band, Schlagwörter, Beschreibung und `N1`-Notizen. Vorhandene ISBNs werden übersprungen; Datensätze ohne Titel, nicht abgeschlossene oder fachfremde RIS-Datensätze werden abgelehnt. Importierte `N1`-Notizen bleiben bei einem späteren RIS-Export erhalten, erzeugen aber bewusst keinen Regalstandort und überschreiben keine vorhandene Standortstruktur.

Der Endpunkt benötigt Anmeldung, Administratorrecht und CSRF-Schutz. Eingaben werden begrenzt und normalisiert; es werden weder Dateien ausgeführt noch externe Ressourcen abgerufen. Der Import erzeugt neue Bücher im vorhandenen ObjectStore und protokolliert ausschließlich Format und Zähler im Audit, keine Buchinhalte.



## BibTeX-Import

Administratoren können UTF-8-kodierte BibTeX-Dateien mit bis zu 4 MiB und 500
`@book`-Datensätzen über **BibTeX importieren** einlesen. Der Parser übernimmt
Titel, mehrere mit `and` getrennte Autoren, ISBN, Verlag, Jahr, Sprache,
Auflage, Reihe/Band, Schlagwörter und Beschreibung. Zusätzlich erkennt er die
vom SimpleOffice4Me-Export in `note` geschriebenen ISBN-, Sprach-, Genre- und
Beschreibungsfelder. Inventarnummer, Seitenangabe und Standorttext bleiben als
Bibliografienotiz erhalten, erzeugen aber bewusst keine Standortstruktur.

Der Import akzeptiert geklammerte, zitierte und einfache numerische Feldwerte.
Er führt keine BibTeX-/TeX-Befehle, Makros, Verkettungen oder externen
Ressourcen aus. Andere Eintragstypen, doppelte Felder, nicht abgeschlossene oder
titellose Datensätze sowie ungültiges UTF-8 werden abgelehnt, bevor ein Buch
angelegt wird. Vorhandene ISBNs werden übersprungen. Anmeldung,
Administratorrecht, CSRF-Schutz, ObjectStore und Auditpfad entsprechen dem RIS-
und Calibre-Import; protokolliert werden nur Format und Zähler.
