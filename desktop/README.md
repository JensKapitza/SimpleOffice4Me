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

`npm run dist` synchronisiert zuerst die Desktop-Version mit `pyproject.toml` und ruft danach `python-setup/build_backend.py` auf. Das Script erzeugt eine eigene Build-Virtualenv, installiert SimpleOffice4Me samt Laufzeitabhängigkeiten und PyInstaller und baut `simpleoffice-python` als eigenständigen Runtime-Binary. Danach packt `electron-builder` den Binary unter `resources/backend/` in die Desktop-App.

Die Builds werden immer **auf dem Zielbetriebssystem** erzeugt. Insbesondere Windows-EXE/NSIS, macOS-DMG und Linux-AppImage/DEB sollten in separaten CI-Jobs auf Windows, macOS und Linux gebaut werden.

Der Linux-CI-Build veröffentlicht zusätzlich das direkt installierbare Artefakt `simpleoffice4me-linux-appimage-installable`. Darin liegen `SimpleOffice4Me-Linux-x86_64.AppImage` und die zugehörige SHA-256-Datei. Vor dem Upload werden Architektur, AppImage-Runtime und Projektversion geprüft.

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

## Prüfung des eingefrorenen Backends

Der PyInstaller-Build setzt den UTF-8-Modus bereits bei der Initialisierung
des eingebetteten Python-Interpreters. Damit startet das Backend auch unter
Windows mit einer anderen System-Codepage. Der Test verlangt weiterhin eine
UTF-8-kodierte öffentliche Anwendungsseite. Die dynamisch geladenen ReportLab-Barcode-Module und Ressourcen
werden mitgepackt; dieselben bestehenden QR-/PDF-Funktionen bleiben verfügbar.

Vor dem Electron-Packaging startet die CI auf Linux, Windows und macOS den
tatsächlichen Runtime-Binary in einem temporären Datenverzeichnis und prüft
dessen öffentliche HTTP-Antwort. Ein fehlendes Modul, eine ungeeignete
Interpreter-Initialisierung oder ein Startabsturz muss dieses Gate fehlschlagen
lassen. Die Backend-Ausgabe wird bei einem Fehler zur Diagnose ausgegeben.

Die ausschließlich generierte Build-Virtualenv `.build-venv` wird wie `.venv`
von den Quellcodegrößen- und Repository-Secret-Prüfungen ausgenommen. Anwendung,
Tests, Build-Skripte und versionierte Konfiguration bleiben vollständig geprüft.

Unter Windows beendet die Testbereinigung den eigenen Onefile-Prozessbaum,
bevor temporäre Dateien entfernt werden. Der Electron-Launcher verwendet auch
beim erzwungenen Abschluss nach seinem regulären Wartefenster den Prozessbaum;
das Beenden nur des Bootloader-Elternprozesses würde das Backend zurücklassen.

## Windows-Start-Smoke-Test des Electron-Pakets

Nach `npm run package:desktop` startet die Desktop-CI unter Windows das erzeugte
`dist/win-unpacked/SimpleOffice4Me.exe` samt unveränderter `resources/app.asar`
und eingebettetem Python-Backend. Das ist ein Laufzeittest des gepackten
Anwendungsinhalts; NSIS-Installation und Portable-Selbstentpackung werden damit
nicht getestet. Die vorhandenen Backend-, Paket- und LAN-Proxy-Tests bleiben
zusätzliche, getrennt benannte Prüfungen.

Voraussetzungen werden per PowerShell geprüft und protokolliert: tatsächliche
Runner-Image-/Windows-Version, Architektur, interaktive Sitzung, Bildschirm,
CIM-Prozessabfrage und `taskkill`. Fehlende Fähigkeiten führen zum Fehler;
der Lauf wird nicht durch eine statische Prüfung ersetzt. Node 24 stellt den
WebSocket-Client für den Electron-Main-Process-Inspector bereit; zusätzliche
Testpakete oder Änderungen am Produktionscode sind nicht erforderlich.

Der Inspector bindet ausschließlich an Loopback mit dynamischem Port und
pausiert vor dem tatsächlichen ASAR-Einstieg `main.js`. Erst nach Umleitung von
`userData`, `sessionData` und `documents` in ein frisches temporäres Profil läuft
die Anwendung weiter. Geerbte Python-/Node-/Desktop-Konfiguration wird bereinigt;
externe Fehlerberichte sind sowohl in der Umgebung als auch in den isolierten
Dokumenteinstellungen deaktiviert. Chromium erhält zusätzlich den isolierten
`--user-data-dir`. Sandbox und Web-Sicherheit der Windows-Anwendung bleiben aktiv.

Das Gate verlangt `app.isPackaged`, passende EXE-/ASAR-Pfade, einen tatsächlich
gestarteten eingebetteten Backend-Kindprozess, eine geladene Loopback-Seite im
Electron-Fenster und eine unabhängige HTTP-200-Antwort mit UTF-8 und
Anwendungskennung. Fehlende Module, Python-Tracebacks und unbehandelte
Main-Process-Fehler führen zum Fehlschlag, auch beim Beenden. Der reguläre
`app.quit()` muss Exitcode 0 liefern; bekannte Nachkommen (einschließlich
PyInstaller-Onefile-Kind), Backend-Listener und temporäre Daten müssen verschwinden.
Prozesse werden über Paketpfad, Elternbeziehungen und Startzeit verfolgt.
Ein nötiger erzwungener Abbruch ist ausschließlich Fehlerbereinigung und lässt
den Test niemals bestehen.

Nach einem Windows-Build im Repository-Root ausführbar:

```powershell
node desktop/ci/smoke-windows.cjs desktop/electron/dist/win-unpacked/SimpleOffice4Me.exe artifacts/windows-start-smoke
```

`windows-start-smoke-evidence` enthält auch bei einem fehlgeschlagenen Test
`result.json`, `electron.log` und die Runner-Diagnose. `runtime_executed`, `stage`,
`passed`, `forced_cleanup` und `cleanup_complete` unterscheiden Startversuch,
Prüfergebnis und Bereinigung. Fehlt der Lauf wegen eines vorherigen Buildfehlers,
ist damit kein Laufzeiterfolg nachgewiesen.

`node --test tests/desktop_start_smoke.test.cjs` prüft den Testtreiber mit
Positiv-/Negativfällen und echten Node-Kindprozessen/Inspector-Verbindungen.
Diese Tests sind ausdrücklich kein Windows-Electron-Paketnachweis.
