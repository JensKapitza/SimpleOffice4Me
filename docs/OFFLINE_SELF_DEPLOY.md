# Self-Deploy und Offline-Updates über Federation

SimpleOffice4Me kann einen installierbaren Softwarestand erzeugen, der ohne GitHub-Zugriff auf einen anderen Rechner kopiert oder über einen bekannten Federation-Peer übertragen wird.

## Releaseformat

Ein Release-ZIP enthält ausschließlich Programmcode und Installationsmaterial:

- `repository.bundle`: Git-Bundle des freigegebenen `main`-Standes,
- `release.json`: Version, Commit, Commit-Anzahl, Build-Zeit, Plattform und SHA-256 des Git-Bundles,
- `INSTALL.py`: eigenständiger Bootstrap für einen neuen Rechner,
- optional `wheelhouse/*.whl`: Python-Abhängigkeiten für eine Installation ohne Internet.

Dokumente, Datenbankinhalte, `.simpleoffice-meta`, Tokens, Passwörter, Session-Schlüssel und sonstige Instanzdaten werden nicht in ein Software-Release aufgenommen.

Standardmäßig lassen sich Releases nur vom Branch `main` bauen. Für Entwicklungs-/Testzwecke kann dies ausdrücklich mit `SIMPLEOFFICE_ALLOW_NON_MAIN_RELEASE=1` freigegeben werden.

## Neuen Rechner klonen

Im Administrationsbereich **Self-Deploy & Offline-Updates** ein Paket bauen. Für einen internetlosen Zielrechner `Wheelhouse` aktivieren. ZIP auf den Zielrechner kopieren, entpacken und ausführen:

```text
python INSTALL.py /pfad/SimpleOffice4Me --offline-install
```

Der Zielrechner benötigt mindestens eine kompatible Python-Version und Git. Das Wheelhouse ist an Betriebssystem, CPU-Architektur und Python-Major/Minor des Build-Rechners gebunden; diese Daten werden vor der Verwendung geprüft. Betriebssystempakete werden nicht in Python-Wheels verpackt. Insbesondere unter Termux bleiben native Pakete wie `cryptography`, Pillow, PyNaCl oder bcrypt Sache des dortigen `pkg`-Setups.

Ohne `--offline-install` wird nur der Git-Stand geklont. Danach kann die normale `start.sh`/`start.bat`-Installation verwendet werden, wenn der Zielrechner Zugriff auf die nötigen Paketquellen hat.

## Federation-Policy

Softwareverteilung ist standardmäßig nicht freigegeben und muss je Peer explizit erlaubt werden:

```json
{"software":{"send":true,"receive":true}}
```

- `send`: dieser Gegenstelle darf eine lokale Version angeboten werden.
- `receive`: von dieser Gegenstelle dürfen Versionen geprüft und Release-Pakete heruntergeladen werden.

Ein eingehendes Angebot ist nur ein Hinweis. Es führt niemals automatisch Code aus. Download und Update werden vom Zielrechner mit seinem lokal konfigurierten Peer und dessen Federation-Zugang durchgeführt.

## Updateablauf

1. Quellrechner baut ein Release.
2. Er bietet es einem bekannten Peer an oder der Zielrechner prüft den Peer aktiv auf eine neuere Version.
3. Der Zielrechner lädt das Release chunkweise über den authentifizierten Federation-Endpunkt. Bereits korrekte Chunks werden bei einem erneuten Versuch wiederverwendet.
4. SHA-256 des Gesamtarchivs und des enthaltenen Git-Bundles werden geprüft.
5. Erst ein Administrator kann ein vollständig gestagtes Release mit der Bestätigung `UPDATE` einspielen.
6. Der Updater stoppt die laufende Instanz, importiert den Commit aus dem Offline-Git-Bundle und akzeptiert ausschließlich einen echten Fast-Forward des vorhandenen Git-Standes. Lokale getrackte Änderungen, Downgrades und divergierende Historien führen zum Abbruch.
7. Ist ein Wheelhouse enthalten, werden Abhängigkeiten ausschließlich daraus installiert und mit `pip check` geprüft.
8. Der Neustart verwendet direkt die vorhandene `.venv` und `python -m tools.launcher start`; `start.sh` wird dabei bewusst nicht ausgeführt, damit der Neustart keinen Internetzugang benötigt.
9. Schlägt die nachgelagerte Offline-Abhängigkeitsinstallation fehl, setzt der Helfer den Git-Stand auf die vorherige Revision zurück und versucht die alte Instanz wieder zu starten.

Damit kann ein Rechner mit neuerem Softwarestand andere bekannte Instanzen versorgen, auch wenn diese selbst keinen Internetzugang besitzen.

## HTTP-Endpunkte

Die Softwareverteilung verwendet die vorhandene Federation-Authentifizierung:

- `GET /federation/v1/software/status`
- `GET /federation/v1/software/releases/current`
- `GET /federation/v1/software/releases/<sha256>/manifest`
- `GET|HEAD /federation/v1/software/releases/<sha256>/blob`
- `GET|HEAD /federation/v1/software/releases/<sha256>/chunks/<index>`
- `POST /federation/v1/software/offers`

Der Chunk-Transfer verwendet dieselben Hash-/Resume-Prinzipien wie die übrige Federation-Datenübertragung.
