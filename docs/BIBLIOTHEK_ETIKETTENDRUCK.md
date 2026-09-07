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
