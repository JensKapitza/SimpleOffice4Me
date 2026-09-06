# SimpleOffice4Me als Debian-Systempaket

Das Build-Script `packaging/build-fpm.sh` erzeugt mit **fpm** eine installierbare `.deb`-Datei.

## Einfachster Build

Auf Debian/Ubuntu reicht:

```bash
./build-dep.sh
./packaging/build-fpm.sh
```

`build-dep.sh` installiert automatisch Python, pip/venv, Ruby, fpm, Compiler, Debian-Paketwerkzeuge sowie die Header und Rust/Cargo-Fallbacks, die fuer native Python-Pakete benoetigt werden koennen.

Das fertige Paket landet standardmaessig unter `dist/packages/`.

## Was im Paket enthalten ist

- Anwendung unter `/opt/simpleoffice4me`
- eigenes Python-Venv unter `/opt/simpleoffice4me/.venv`
- beim Build erzeugtes Wheelhouse fuer eine Offline-Installation der Python-Abhaengigkeiten
- Kommando `/usr/bin/simpleoffice4me`
- systemd-Unit `simpleoffice4me.service`
- Konfiguration `/etc/simpleoffice4me/simpleoffice.env`
- persistente Instanzdaten unter `/var/lib/simpleoffice4me`

Die Laufzeit-Pythonpakete werden **nicht erst auf dem Zielsystem aus PyPI geladen**. Das Build-System erzeugt vorher Wheels und nimmt sie in das Paket auf.

## Installation

```bash
sudo apt install ./dist/packages/simpleoffice4me_1.0.0-1_amd64.deb
sudo systemctl start simpleoffice4me
systemctl status simpleoffice4me
```

Standardmaessig lauscht SimpleOffice4Me nur auf `127.0.0.1:8080`. Fuer einen Reverse Proxy oder einen anderen Port `/etc/simpleoffice4me/simpleoffice.env` aendern und anschliessend:

```bash
sudo systemctl restart simpleoffice4me
```

## Abhaengigkeiten

Als zwingende Debian-Laufzeitabhaengigkeiten werden Python >= 3.10, `python3-venv`, `git` und `ca-certificates` eingetragen. `git` ist erforderlich, weil die Anwendung die lokale Revisionshistorie darueber fuehrt.

Zusaetzliche Funktionspakete werden als Debian-Recommends hinterlegt, darunter Poppler, Tesseract OCR, ImageMagick, Ghostscript, FFmpeg, ClamAV und LibreOffice. Damit koennen Vorschauen, OCR, Virenscan und Dokumentkonvertierung auf einem normalen System ohne manuelles Suchen nach Paketnamen nachinstalliert werden.

## Daten und Updates

Programmdateien liegen unter `/opt`, Benutzerdaten und Instanzkonfiguration unter `/var/lib/simpleoffice4me`. `/opt/simpleoffice4me/instance` ist nur ein Symlink auf den persistenten Bereich. Paketupdates ueberschreiben daher keine Dokumente oder Instanzdaten.

Auch bei `apt purge` loescht das Paket `/var/lib/simpleoffice4me` absichtlich nicht automatisch. Das verhindert Datenverlust durch versehentliches Entfernen des Pakets.

## Paketparameter

```bash
SIMPLEOFFICE_PACKAGE_VERSION=1.0.1 \
SIMPLEOFFICE_PACKAGE_ITERATION=2 \
SIMPLEOFFICE_PACKAGE_OUT=/tmp/packages \
bash packaging/build-fpm.sh
```

Das Paket ist architekturspezifisch, sobald native Python-Wheels enthalten sind. Fuer amd64, arm64 usw. sollte deshalb jeweils auf der Zielarchitektur oder in einer passenden Build-Umgebung gebaut werden.
