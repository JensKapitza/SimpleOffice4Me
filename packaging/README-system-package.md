# SimpleOffice4Me als Debian-Systempaket

Die Paketierung trennt Client- und Lizenz-Master-Builds bewusst. `packaging/build-fpm.sh` ist nur das interne Backend und soll nicht direkt gestartet werden.

## Build-Abhaengigkeiten

```bash
./build-dep.sh
```

`build-dep.sh` installiert Python, pip/venv, Ruby, fpm, Compiler, Debian-Paketwerkzeuge sowie Header und Rust/Cargo-Fallbacks fuer native Python-Pakete.

## Client bauen

Einmalig die lokale Vorlage kopieren:

```bash
cp packaging/build-client.local.sh.example packaging/build-client.local.sh
```

Danach in `packaging/build-client.local.sh` die nicht geheime Master-Identitaet eintragen, insbesondere die feste Master-URL. Die Datei ist durch `.gitignore` ausgeschlossen und wird auch explizit nicht in das Paket kopiert.

Build:

```bash
bash packaging/build-client.sh
```

Das erzeugte Paket ist standardmaessig `simpleoffice4me-client`.

## Lizenz-Master / Server bauen

Einmalig:

```bash
cp packaging/build-server.local.sh.example packaging/build-server.local.sh
```

Dort die oeffentliche Server-URL und Peer-ID eintragen. Anschliessend:

```bash
bash packaging/build-server.sh
```

Das erzeugte Paket ist standardmaessig `simpleoffice4me-server` und wird fest im Lizenz-Master-Modus gebaut.

## Geheimnisse

Tokens, Passwoerter und private Schluessel gehoeren **nicht** in:

- `build-client.local.sh`
- `build-server.local.sh`
- Git-Commits
- Build-Parameter
- `app/build_master.py`

Der Lizenz-Master/Federation-Token wird erst auf dem installierten Rechner lokal gesetzt:

```bash
sudo bash /opt/simpleoffice4me/packaging/configure-license-secret.sh
```

Das Skript fragt den Token verdeckt ueber stdin ab und schreibt ihn mit restriktiven Dateirechten nach `/etc/simpleoffice4me/simpleoffice.env`. Dadurch erscheint der Token nicht als Kommandozeilenargument.

## Unveraenderlicher Master im Client

Beim Client-Build werden nur diese nicht geheimen Angaben in `app/build_master.py` des Pakets geschrieben:

- Master-Basis-URL
- Master-Peer-ID
- Rolle Client/Server

Es gibt absichtlich keine Laufzeit-Einstellung fuer die Master-URL. Ein installierter Client kann daher nicht auf einen anderen Abrechnungs-Master umgestellt werden; dafuer ist ein neuer Client-Build erforderlich. Wer den Source Code selbst auscheckt, kann mit seinem eigenen lokalen Build-Skript einen eigenen Master definieren.

## Was im Paket enthalten ist

- Anwendung unter `/opt/simpleoffice4me`
- eigenes Python-Venv unter `/opt/simpleoffice4me/.venv`
- beim Build erzeugtes Wheelhouse fuer Offline-Installation
- Kommando `/usr/bin/simpleoffice4me`
- systemd-Unit `simpleoffice4me.service`
- Laufzeitkonfiguration `/etc/simpleoffice4me/simpleoffice.env`
- persistente Instanzdaten unter `/var/lib/simpleoffice4me`

Lokale Buildkonfigurationen, `*.secret.sh`, `packaging/private/`, Runtime-Datenbanken und VCS-Metadaten werden nicht in das Paket aufgenommen.

## Installation

```bash
sudo apt install ./dist/packages/simpleoffice4me-client_1.0.0-1_amd64.deb
sudo systemctl start simpleoffice4me
systemctl status simpleoffice4me
```

Beim Server entsprechend das `simpleoffice4me-server`-Paket installieren.

Standardmaessig lauscht SimpleOffice4Me nur auf `127.0.0.1:8080`. Fuer Reverse Proxy oder anderen Port `/etc/simpleoffice4me/simpleoffice.env` aendern und anschliessend:

```bash
sudo systemctl restart simpleoffice4me
```

## Abhaengigkeiten und Daten

Als zwingende Debian-Laufzeitabhaengigkeiten werden Python >= 3.10, `python3-venv`, `git` und `ca-certificates` eingetragen. Weitere Funktionspakete wie Poppler, Tesseract OCR, ImageMagick, Ghostscript, FFmpeg, ClamAV und LibreOffice werden als Recommends hinterlegt.

Programmdateien liegen unter `/opt`, Benutzerdaten und Instanzkonfiguration unter `/var/lib/simpleoffice4me`. Paketupdates ueberschreiben keine Dokumente oder Instanzdaten. Auch `apt purge` loescht `/var/lib/simpleoffice4me` absichtlich nicht automatisch.
