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

Ein bereits heruntergeladener Extrakt kann ohne erneuten Download vollständig
neu indexiert werden:

```bash
python tools/osm_index_worker.py --root /pfad/zum/dokument-root --force
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
