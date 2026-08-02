# Erster Start

## Einfach starten

- **Linux:** `./start.sh`
- **Windows:** `start.bat` doppelklicken.
- **macOS:** `start.command` doppelklicken.

Beim ersten Start erzeugt der Starter automatisch eine lokale Python-Umgebung,
installiert die benötigten Pakete und öffnet einen kurzen Einrichtungsassistenten.
Mit Enter werden die vorgeschlagenen Werte übernommen. Anschließend läuft die
Anwendung unter `http://127.0.0.1:8080`.

Beim ersten Aufruf führt die Startseite zur Anmeldung. Dort einmalig
**Registrieren** auswählen und das lokale Benutzerkonto anlegen. Nach der
Anmeldung öffnet sich direkt die Systemübersicht. Die Benutzertabelle wird
beim Start angelegt, vorhandene Konten werden dabei nicht verändert.

Ein Update erfolgt mit `./update.sh` bzw. `update.bat`. Das führt nur
`git pull --ff-only` aus, damit lokale Änderungen nicht überschrieben werden,
installiert bei Bedarf neue Abhängigkeiten und startet anschließend neu.

## Manuelle Alternative

```bash
git clone https://github.com/JensKapitza/SimpleOffice4Me.git
cd SimpleOffice4Me
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .

# Einmalig einen Dokumentbaum vorbereiten
python -m flask --app app init-document-store /srv/simpleoffice/documents

# Vorhandene Dateien einlesen, Hashes prüfen und Suchcache aufbauen
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app scan-documents
```

## Was im Dokumentordner entsteht

- `.simpleoffice-folder.json`: Rechte- und Vererbungsflag für den Ordner.
- `.simpleoffice-meta/`: Sidecar-Metadaten, Ereignischronik und ein
  löschbarer SQLite-Suchcache.

Die Originaldateien bleiben unverändert in ihrer Verzeichnisstruktur. Soweit
das Dateisystem es kann, schreibt SimpleOffice zusätzlich ID, SHA-256 und Tags
als Extended Attributes direkt an die Datei. Auf SMB, FAT oder in Sicherungen
ohne Extended Attributes funktioniert der Sidecar-Speicher weiter.

## Regelmäßiger Betrieb

Der Scan kann ohne Risiko wiederholt werden. Beim normalen Folgescan werden
bereits bekannte SHA-256-Werte wiederverwendet, wenn Pfad, Größe und
Änderungszeit unverändert sind. Eine Verschiebung innerhalb desselben
Dateisystems wird zusätzlich über Geräte- und Inode-ID erkannt; Dokument-ID,
Prüfsumme und Historie bleiben erhalten. Der erste Lauf nach einem Update kann
diese Dateisystemkennungen noch nachtragen.

Für eine vollständige Integritätsprüfung kann das erneute Lesen aller Dateien
ausdrücklich angefordert werden:

```bash
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app scan-documents --verify-hashes
```

Der Scan erkennt gleiche Dateien, protokolliert Symlinks und baut den
verzichtbaren SQLite-Index nach einem Abbruch selbst erneut auf. Fehlende
Dokument-Sidecars werden aus einer vorhandenen Indexidentität repariert.

### Ressourcen beim Hintergrundscan

Der Starter schreibt Scan-Status und Konsolenausgabe gebündelt: spätestens
nach 250 weiteren Dateien oder nach zwei Sekunden. Der Abschlussstand wird
immer sofort geschrieben. Das vermeidet tausende kleine Schreibvorgänge, ohne
den eigentlichen Scan oder seine Chronik auszulassen.

Tesseract erhält standardmäßig höchstens einen OpenMP-Thread pro OCR-Vorgang.
Für einen leistungsfähigeren Server kann die Grenze bewusst auf 1 bis 8 gesetzt
werden:

```bash
SIMPLEOFFICE_OCR_THREADS=2 ./start.sh
```

Unter Windows kann derselbe Wert vor `start.bat` mit
`set SIMPLEOFFICE_OCR_THREADS=2` gesetzt werden. Ungültige Werte fallen auf
1 zurück; Werte über 8 werden begrenzt. Diese Einstellung betrifft Tesseract,
nicht normale Dateizugriffe oder externe Office-/PDF-Werkzeuge.

### Uploadgröße und unvollständige Importe

Web-Uploads sind standardmäßig auf insgesamt 512 MiB pro HTTP-Anfrage
begrenzt. Mehrere gleichzeitig ausgewählte Dateien teilen sich dieses Limit.
Für größere Scanstapel kann es zwischen 1 und 4096 MiB angepasst werden:

```bash
SIMPLEOFFICE_MAX_UPLOAD_MIB=1024 ./start.sh
```

Unter Windows wird vor `start.bat` entsprechend
`set SIMPLEOFFICE_MAX_UPLOAD_MIB=1024` verwendet. Ungültige Werte fallen auf
512 MiB zurück. Ein vorgeschalteter Reverse Proxy muss mindestens dasselbe
Limit erlauben. Bricht ein Import mit einem Fehler ab oder überschreitet er die
Grenze, wird seine unvollständige Staging-Datei automatisch entfernt. Bei einem
harten Prozessabbruch kann sie beim nächsten Start weiterhin sichtbar sein.

Standardmäßig folgt der Scanner keinen Symlinks und überschreitet keine
Dateisystemgrenze, z. B. keinen Mount, Bind-Mount oder Overlay-Einstieg. In
`.simpleoffice-folder.json` kann das nur für einen konkreten Ordner freigegeben
werden:

```json
"scan": {
  "follow_symlinks": true,
  "allow_other_filesystems": true
}
```

Auch bei einer bewussten Freigabe verhindert die Kombination aus Geräte- und
Inode-ID Endlosschleifen. Übersprungene Links und Dateisystemgrenzen erscheinen
in der Chronik.

```bash
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app scan-documents
```

Für den späteren automatischen Betrieb ist ein systemd-Timer oder ein
Container-Job sinnvoll. Die Anwendung soll dabei nur den Dokumentbaum und
nicht das ganze System lesen.

## Mit Dokumenten arbeiten

Die Metadaten bleiben neben den Dateien und können bereits über die
Kommandozeile bearbeitet werden. `DOKUMENT` kann die relative Datei oder ihre
Dokument-ID sein.

```bash
# Notiz und fachlichen Zustand setzen
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app document-note "inbox/rechnung.pdf" "Rückfrage an Peter offen" --user jens
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app document-state "inbox/rechnung.pdf" "wartet_auf_antwort" --user jens

# Zwei Dokumente verbinden und die Mindmap-Daten ausgeben
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app document-link "inbox/rechnung.pdf" "vertrag.pdf" --type bezieht_sich_auf --user jens
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app document-graph "inbox/rechnung.pdf"

# Eine neue Datei als nächste Version ablegen
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app import-file ./rechnung-korrigiert.pdf --version-of "rechnung" --user jens
```

Notizen und Zustandswechsel sind chronologisch gespeichert. Beziehungen sind
gerichtet und beschriftet; die Graph-Ausgabe enthält ein Dokument, seine
eingehenden/ausgehenden Verbindungen und seine Versionsreihe. Eine grafische
Mindmap-Oberfläche kann diese Ausgabe ohne erneute Datenmigration verwenden.

## SciServer-Prinzipien

Aus dem SciServer-Konzept sind drei Funktionen direkt übernommen: schnelle
Suche, fachliche Modellierung und Datenintegrität. Freie Attribute erlauben
z. B. Messreihe, Projekt, Material oder Aktenzeichen ohne Datenbankänderung.
Sie sind zusammen mit Pfad, Zustand, Tags, Notizen und späterem OCR-Text
durchsuchbar.

```bash
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app document-attribute "vertrag.pdf" "projekt" "Musterbau 2026" --user jens
SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents \
  python -m flask --app app search-documents "Musterbau"
```

Der beim ersten Scan ermittelte SHA-256 bleibt als Original-Prüfsumme erhalten.
Eine bei einem normalen Scan erkannte Änderung von Größe oder Änderungszeit
führt erneut zur Hashprüfung. Der Schnellpfad vertraut bei unveränderten Werten
auf diese Dateisystemangaben; nach einem Verdacht auf Manipulation und
regelmäßig nach dem eigenen Sicherungskonzept sollte deshalb
`scan-documents --verify-hashes` ausgeführt werden. Eine erkannte Abweichung
erscheint als `integrity_changed` in Metadaten und Chronik. Eine inhaltliche
Änderung soll als neue Version importiert werden, nicht als Überschreiben der
alten Datei.

## Revisionsarchiv und Benutzerzuordnung

Schreibende Befehle verlangen `--user`. Notiz, Zustand, Attribut, Beziehung und
neue Version werden mit diesem Benutzer in der Ereignischronik und zusätzlich
im lokalen Git-Repository `.simpleoffice-history/` abgelegt. Dort liegen
Metadaten- und Konfigurationssnapshots getrennt vom Programm-Repository.

Die Metadaten liegen zentral sowie zusätzlich ordnernah unter
`.simpleoffice-meta/<Dokument-ID>.json`. Kurze Notizen und der Zustand werden,
falls das Dateisystem dies zulässt, als Extended Attributes direkt an die Datei
geschrieben. Bei großen Notizen verweist ein Attribut auf die ordnernahe
Sidecar-Datei.

## Weboberfläche für Versionen, Wiki und Logbuch

Nach der Anmeldung stehen diese Seiten zur Verfügung:

- `/documents/`: alle indexierten Dokumente.
- `/documents/<Dokument-ID>`: jede Version derselben Datei, Notizen, Zustand
  und das Logbuch genau dieses Dokuments. Neue Notizen und Zustände speichern
  immer den angemeldeten Benutzer als Autor und erzeugen eine Git-Revision.
- `/documents/wiki/notes`: ein dokumentübergreifendes Notiz-Wiki.
- `/documents/logbook`: alle Benutzer-Revisionen und Scannerereignisse in
  umgekehrt chronologischer Reihenfolge.
- `/documents/images`: Bildergalerie mit höchstens 500 Vorschaubildern pro
  Seite sowie Vor- und Zurück-Navigation.

Damit ist auch bei einem Neustart nachvollziehbar, wer wann welche Notiz oder
Änderung vorgenommen hat; die Anzeige liest ausschließlich die dateibasierten
Metadaten und das lokale Revisionsarchiv.

Dateien lassen sich auf der Dokumentseite auch mehrfach direkt hochladen. Mit
„Direkt ins feste Archiv“ werden sie hashbasiert unter `archive/` einsortiert;
der Import ist vollständig nachvollziehbar und die Originale werden nicht
überschrieben. Unter `/documents/archives` können externe Platten mit Name,
ID und Tags registriert und später nach dem Einhängen wiedergefunden werden.

Hat ein Dokument mehrere Versionen, kann die Dokumentansicht ältere Versionen
per „Alte Versionen auslagern“ auf einen eingehängten Datenträger verschieben.
Die letzte Version bleibt lokal. Jede ältere Datei wird zuerst in den
Datenträger kopiert und per SHA-256 gegen die bekannte Prüfsumme geprüft. Nur
bei Übereinstimmung wird die lokale Kopie gelöscht und der externe Archivort
mit Archiv-ID in den Metadaten notiert.

`--version-of` akzeptiert ID, Pfad, Dateinamen, Tags, Zustände, Notiztext oder
freie Attribute. Vor dem Import zeigt die Anwendung alle Treffer und fragt
nach Auswahl und Bestätigung. Erst danach wird die neue Version übernommen.
