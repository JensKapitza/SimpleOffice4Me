# OSM-Index und Rechnungsfinalisierung

## OSM-Adressindex neu aufbauen

Fehlt einem exportierten OSM-Objekt eine echte ID, erzeugt der Import eine
stabile SHA-256-ID aus Adresse, Koordinaten und OSM-Typ. Eine batchabhängige
Ersatz-ID wird nicht verwendet. Identische Datensätze, Änderungen bestehender
OSM-Objekte und unerwartete Hash-Kollisionen werden getrennt gezählt.

Der Abschlussbericht enthält:

- `processed`
- `inserted`
- `updated`
- `duplicates`
- `id_collisions`
- `rejected`
- `stored`

`stored` wird nach dem Import unmittelbar mit `SELECT COUNT(*) FROM address`
aus SQLite gelesen. Eine unplausibel stark eingebrochene Datenmenge beendet
den Import als Fehler.

Während des Neuaufbaus meldet der Status zuerst die Phase `filtering` und danach
`exporting_importing`. In der zweiten Phase aktualisiert der Worker ungefähr
alle zwei Sekunden
`processed`, `inserted`, `updated`, `duplicates`, `rejected`, Laufzeit und
Datensätze pro Sekunde. Diese Werte stehen im Worker-Log und in der
CRM-Administration. HTTP-Statusabfragen führen währenddessen kein zusätzliches
`COUNT(*)` über die wachsende Tabelle aus. Der Administrator sieht außerdem
den tatsächlich verwendeten `DOCUMENT_ROOT`, den absoluten Pfad der
`addresses.sqlite3` sowie getrennt die Gesamtgröße des Index und die Anzahl
der bei einer Suche angezeigten Treffer.

`osmium tags-filter` entfernt mit `--remove-tags` die Tags lediglich zur
Geometrieauflösung benötigter Referenzobjekte. `osmium export` gibt diese
Objekte deshalb nicht mehr als Millionen irrelevanter Features aus. Die echten
OSM-Attribute `type` und `id` werden ausdrücklich exportiert und als stabile
Objektidentität gespeichert.

Die gefilterte PBF-Zwischenbasis liegt dauerhaft unter
`.simpleoffice/osm-addresses/build/addresses.filtered.osm.pbf`. Sie wird bei
gleichem Download nicht erneut erzeugt. Der Import schreibt in
`addresses.staging.sqlite3` und bestätigt jeweils 2.000 Eingabedatensätze in
einer eigenen SQLite-Transaktion. Erst ein vollständig exportierter und
plausibler Staging-Index ersetzt atomar den aktiven `addresses.sqlite3`.

Wird Anwendung, Worker oder `osmium` unterbrochen, bleiben gefilterte Basis,
Staging-Datenbank und bestätigte Datensatzposition erhalten. Beim nächsten
Start erzeugt `osmium` denselben deterministischen Stream erneut; der Import
überspringt dessen bereits bestätigten Präfix und arbeitet danach weiter. Ein
neuer Download ist nur erforderlich, wenn sich die Quelldatei geändert hat.
Ein fortlaufender SHA-256-Fingerabdruck prüft dabei den gesamten bestätigten
Stream-Präfix. Bei abweichender Reihenfolge wird Staging verworfen, statt
unbemerkt unvollständige Daten zu veröffentlichen.
Ist der Export bereits vollständig und nur die Veröffentlichung wurde
unterbrochen, veröffentlicht der Neustart direkt den fertigen Staging-Index;
der OSM-Stream wird dann nicht erneut abgespielt.

`osmium export` schreibt seine Diagnose dauerhaft in
`.simpleoffice/osm-addresses/build/osmium-export.log`. Dadurch kann keine
unbeachtete `stderr`-Pipe volllaufen. Es gibt keinen festen Gesamtlaufzeit-
Timeout mehr: Solange Datensätze geliefert werden, darf auch ein mehr als sechs
Stunden dauernder Deutschland-Export weiterlaufen. Nur wenn standardmäßig 30
Minuten keinerlei Ausgabe erfolgt, beendet ein Leerlauf-Watchdog den Prozess.
Der Wert kann über `SIMPLEOFFICE_OSM_EXPORT_IDLE_TIMEOUT` zwischen 300 und
86.400 Sekunden gesetzt werden. Die nächste Ausführung setzt am letzten
bestätigten Batch fort.

Der Download verwendet eine `.part`-Datei und HTTP-Range-Requests. Ein
abgebrochener Download wird daher ab dem bestätigten Byte fortgesetzt, sofern
Geofabrik Range-Requests für die Datei akzeptiert.

Ein bereits heruntergeladener Extrakt kann ohne erneuten Download vollständig
neu indexiert werden:

```bash
python tools/osm_index_worker.py --root /pfad/zum/dokument-root --force
```

Das Log zeigt währenddessen beispielsweise:

```text
OSM-Index: processed=1200000 inserted=1185000 updated=0 duplicates=12000 rejected=3000 rate=24500/s
```

Nach der Aktualisierung muss der Deutschland-Index einmal mit `--force` neu
aufgebaut werden. Der produktive Datenbestand wird nicht durch Tests oder ein
Anwendungsupdate automatisch heruntergeladen.

## Rechnungsfinalisierung diagnostizieren

Die Finalisierung misst unter anderem Laden von Rechnung, Kontakt und
Projektpositionen, PDF-Rendering, Ghostscript/PDF-A, XML-Erzeugung,
Einbettung, Validierung, Dateispeicherung, Kontaktverknüpfung und Audit-Historie.
Jeder Schritt über 500 ms erscheint als Warnung; zusätzlich wird die Gesamtzeit
geloggt. Externe PDF/A- und XML-Prüfungen werden nach 90 Sekunden beendet.

Technische Endzustände sind:

- `validated`
- `validation_failed`
- `pdfa_failed`
- `embedding_failed`
- `xml_generation_failed`

`embedded_unvalidated` ist kein Endzustand mehr. Exit-Codes und Ausgaben der
Validatoren werden an der Rechnung gespeichert; vollständige Fehler stehen im
Anwendungslog und die Rechnungsansicht zeigt den Fehler sichtbar an.

Die EN16931-Prüfung ist immer aktiv. `start.sh` und `start.bat` installieren
beim ersten Start automatisch den fest versionierten Mustang-CLI-Validator
2.25.0 aus Maven Central in `.runtime-tools/` und prüfen den Download anhand
der stärksten von Maven Central veröffentlichten Prüfsumme. Fehlt wie bei
Mustang 2.25.0 die SHA-256-Sidecar-Datei, wird die veröffentlichte SHA-1-
Prüfsumme geprüft und anschließend für lokale Folgeprüfungen ein SHA-256-Hash
gespeichert. Mustang validiert CII/EN16931 und die
PDF/A-Eigenschaften; eine Rechnung wird ohne erfolgreichen Abschluss nicht
finalisiert. Erforderlich ist eine Java-Laufzeit. Ein vorhandenes separates
veraPDF wird zusätzlich erkannt. `SIMPLEOFFICE_ZUGFERD_VALIDATOR` dient nur als
optionaler Override für eine eigene Validatorinstallation; eine Konfiguration
ist für den Standardbetrieb nicht erforderlich.

Ghostscript erhält ausschließlich Leserechte auf das verwendete ICC-Profil;
der Sicherheitsmodus wird nicht global deaktiviert. Wenn Java, Ghostscript
oder der verifizierte Validator nicht verfügbar sind, bleibt die Rechnung als
Entwurf erhalten und die technische Ursache wird in UI und Log ausgegeben.
