# SimpleOffice4Me Desktop

Die Desktop-Ausgabe besteht bewusst aus zwei getrennten Build-Bereichen:

- `electron/` – nativer Desktop-Wrapper, Installer und Fenster-Lifecycle.
- `python-setup/` – reproduzierbarer PyInstaller-Build des lokalen Python-Backends.

Electron zeigt **keine Kopie der Anwendung**. Es startet das echte SimpleOffice4Me-Backend ausschließlich auf `127.0.0.1`, wartet auf dessen HTTP-Endpunkt und lädt danach genau diese lokale Anwendung in ein abgesichertes `BrowserWindow`.

## Entwicklung

```bash
cd desktop/electron
npm install
npm start
```

`npm start` verwendet zuerst einen bereits gebauten Python-Runtime-Binary. Wenn keiner vorhanden ist, wird im Entwicklungsmodus `python-setup/runtime_entry.py` mit dem lokalen Python gestartet.

## Komplettes Desktop-Paket bauen

```bash
cd desktop/electron
npm install
npm run dist
```

`npm run dist` ruft zuerst `python-setup/build_backend.py` auf. Das Script erzeugt eine eigene Build-Virtualenv, installiert SimpleOffice4Me samt Laufzeitabhängigkeiten und PyInstaller und baut `simpleoffice-python` als eigenständigen Runtime-Binary. Danach packt `electron-builder` den Binary unter `resources/backend/` in die Desktop-App.

Die Builds werden immer **auf dem Zielbetriebssystem** erzeugt. Insbesondere Windows-EXE/NSIS, macOS-DMG und Linux-AppImage/DEB sollten in separaten CI-Jobs auf Windows, macOS und Linux gebaut werden.

## Laufzeitdaten

Programmdateien und Nutzdaten werden getrennt:

- Electron-Profil: plattformspezifisches `userData`-Verzeichnis.
- SQLite/Instanzdaten: `<userData>/backend/database` und `<userData>/backend/instance`.
- Dokumente: beim ersten Start standardmäßig `Documents/SimpleOffice4Me`; ein bereits gespeicherter Dokumentpfad bleibt bei späteren Starts erhalten.
- HTTP: ausschließlich Loopback (`127.0.0.1`), Port wird beim Start dynamisch gewählt.

Das verhindert Schreibzugriffe in `Program Files`, `/opt`, ein `.app`-Bundle oder das temporäre PyInstaller-Verzeichnis.

## Sicherheit

Der Electron-Renderer erhält kein Node.js. `nodeIntegration` ist aus, `contextIsolation`, `sandbox` und `webSecurity` sind an. Navigation außerhalb des lokalen SimpleOffice-Ursprungs wird abgefangen und normale `http(s)`-Links werden im Systembrowser geöffnet. Berechtigungen werden auf den lokalen Ursprung begrenzt.

## Externe Werkzeuge

Optionale Systemprogramme wie LibreOffice, Ghostscript oder ClamAV werden nicht heimlich in das Electron-Paket kopiert. SimpleOffice erkennt sie wie bisher zur Laufzeit und verwendet seine vorhandenen Fallbacks. Das hält Lizenzierung, Updates und Sicherheitsgrenzen transparent.
