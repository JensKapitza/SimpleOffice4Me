# Erster Start

## Einfach starten

- **Linux:** `./start.sh`
- **Windows:** `start.bat` doppelklicken.
- **macOS:** `start.command` doppelklicken.

Beim ersten Start erzeugt der Starter automatisch eine lokale Python-Umgebung,
installiert die benötigten Pakete und öffnet einen kurzen Einrichtungsassistenten.
Mit Enter werden die vorgeschlagenen Werte übernommen. Anschließend läuft die
Anwendung unter `http://127.0.0.1:8080`.

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

Der Scan kann ohne Risiko wiederholt werden. Er berechnet fehlende Prüfsummen,
erkennt gleiche Dateien, protokolliert Symlinks und baut den Index nach einem
Abbruch selbst erneut auf.

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
