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

Der `osmium export` schreibt seine Diagnose in eine temporäre Datei. Dadurch
kann keine unbeachtete `stderr`-Pipe volllaufen und den Export blockieren. Ein
festgefahrener Export wird standardmäßig nach sechs Stunden abgebrochen; der
Wert kann mit `SIMPLEOFFICE_OSM_EXPORT_TIMEOUT` zwischen einer und 24 Stunden
gesetzt werden. Eine unterbrochene SQLite-Transaktion wird zurückgerollt.

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

Für eine vollständige EN16931-Prüfung werden veraPDF und ein über
`SIMPLEOFFICE_ZUGFERD_VALIDATOR` konfigurierter ZUGFeRD-Validator benötigt.
Ghostscript erhält ausschließlich Leserechte auf das verwendete ICC-Profil;
der Sicherheitsmodus wird nicht global deaktiviert.
