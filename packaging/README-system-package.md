# SimpleOffice4Me als Debian-Systempaket

Das Build-Script `packaging/build-fpm.sh` erzeugt mit **fpm** eine installierbare `.deb`-Datei.

## Build-Abhängigkeiten

Auf Debian/Ubuntu beispielsweise:

```bash
sudo apt install python3 python3-pip python3-venv ruby ruby-dev build-essential git
sudo gem install --no-document fpm
bash packaging/build-fpm.sh
```

Das fertige Paket landet standardmäßig unter `dist/packages/`.

## Was im Paket enthalten ist

- Anwendung unter `/opt/simpleoffice4me`
- eigenes Python-Venv unter `/opt/simpleoffice4me/.venv`
- beim Build erzeugtes Wheelhouse für eine Offline-Installation der Python-Abhängigkeiten
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

Standardmäßig lauscht SimpleOffice4Me nur auf `127.0.0.1:8080`. Für einen Reverse Proxy oder einen anderen Port `/etc/simpleoffice4me/simpleoffice.env` ändern und anschließend:

```bash
sudo systemctl restart simpleoffice4me
```

## Abhängigkeiten

Als zwingende Debian-Laufzeitabhängigkeiten werden Python >= 3.10, `python3-venv`, `git` und `ca-certificates` eingetragen. `git` ist erforderlich, weil die Anwendung die lokale Revisionshistorie darüber führt.

Zusätzliche Funktionspakete werden als Debian-Recommends hinterlegt, darunter Poppler, Tesseract OCR, ImageMagick, Ghostscript, FFmpeg, ClamAV und LibreOffice. Damit können Vorschauen, OCR, Virenscan und Dokumentkonvertierung auf einem normalen System ohne manuelles Suchen nach Paketnamen nachinstalliert werden.

## Daten und Updates

Programmdateien liegen unter `/opt`, Benutzerdaten und Instanzkonfiguration unter `/var/lib/simpleoffice4me`. `/opt/simpleoffice4me/instance` ist nur ein Symlink auf den persistenten Bereich. Paketupdates überschreiben daher keine Dokumente oder Instanzdaten.

Auch bei `apt purge` löscht das Paket `/var/lib/simpleoffice4me` absichtlich nicht automatisch. Das verhindert Datenverlust durch versehentliches Entfernen des Pakets.

## Paketparameter

```bash
SIMPLEOFFICE_PACKAGE_VERSION=1.0.1 \
SIMPLEOFFICE_PACKAGE_ITERATION=2 \
SIMPLEOFFICE_PACKAGE_OUT=/tmp/packages \
bash packaging/build-fpm.sh
```

Das Paket ist architekturspezifisch, sobald native Python-Wheels enthalten sind. Für amd64, arm64 usw. sollte deshalb jeweils auf der Zielarchitektur oder in einer passenden Build-Umgebung gebaut werden.
