# SimpleOffice4Me auf Raspberry Pi

Diese Variante nutzt das veroeffentlichte Multi-Arch-Docker-Image von SimpleOffice4Me. Auf einem Raspberry Pi 4 oder 5 mit 64-Bit Raspberry Pi OS wird automatisch das ARM64-Image verwendet; auf normalen x86-64-Servern dasselbe Deployment mit dem AMD64-Image.

## Schnellinstallation

Auf einem frischen Raspberry Pi OS 64 Bit:

```bash
curl -fsSL https://raw.githubusercontent.com/JensKapitza/SimpleOffice4Me/main/deploy/pi/bootstrap.sh | sudo bash
```

Der Bootstrap legt die Deployment-Dateien unter `/opt/simpleoffice4me` ab, installiert bei Bedarf Docker aus den Distribution-Paketen und startet SimpleOffice4Me.

Danach ist die Anwendung im LAN erreichbar unter:

```text
http://<IP-DES-RASPBERRY-PI>:8080
```

Die Anwendungsdaten liegen dauerhaft im Docker-Volume `simpleoffice4me-data`. Ein Container- oder Image-Update loescht diese Daten nicht.

## Update

```bash
sudo /opt/simpleoffice4me/update.sh
```

Das Skript zieht das aktuelle Image und startet den Container neu, ohne das Daten-Volume zu entfernen.

## Backup

```bash
sudo /opt/simpleoffice4me/backup.sh /pfad/zu/backups
```

Das Backup enthaelt den kompletten persistenten Inhalt von `simpleoffice4me-data`.

## Version festpinnen

Standardmaessig wird `latest` verwendet. Fuer reproduzierbare produktive Installationen kann vor dem Start ein konkreter Image-Tag gesetzt werden:

```bash
export SIMPLEOFFICE_TAG=1.0.0
sudo -E /opt/simpleoffice4me/install.sh
```

## Port und Bind-Adresse

Standard ist Port `8080` auf allen Interfaces im lokalen Netz.

```bash
export SIMPLEOFFICE_HTTP_PORT=8088
export SIMPLEOFFICE_BIND_ADDRESS=192.168.1.20
sudo -E /opt/simpleoffice4me/install.sh
```

Fuer einen Reverse Proxy auf demselben Host sollte die Anwendung nur lokal gebunden werden:

```bash
export SIMPLEOFFICE_BIND_ADDRESS=127.0.0.1
sudo -E /opt/simpleoffice4me/install.sh
```

## Produktiver Betrieb

Fuer einen Internet-zugaenglichen Betrieb sollte SimpleOffice4Me nicht direkt ueber Port 8080 ins Internet gestellt werden. Empfohlen ist ein HTTPS-Reverse-Proxy vor dem Container und `SIMPLEOFFICE_BIND_ADDRESS=127.0.0.1`. Die persistenten Daten sollten regelmaessig mit `backup.sh` gesichert werden.

## Stoppen / Starten

```bash
cd /opt/simpleoffice4me
sudo docker compose -f compose.yaml stop
sudo docker compose -f compose.yaml start
```

Bei Systemen mit altem `docker-compose` funktioniert entsprechend:

```bash
sudo docker-compose -f /opt/simpleoffice4me/compose.yaml stop
sudo docker-compose -f /opt/simpleoffice4me/compose.yaml start
```

## Komplett entfernen

Container entfernen, Daten behalten:

```bash
cd /opt/simpleoffice4me
sudo docker compose -f compose.yaml down
```

Auch die Daten loeschen:

```bash
sudo docker volume rm simpleoffice4me-data
```

Der letzte Befehl ist absichtlich getrennt, damit ein normales Neuinstallieren oder Update keine Nutzdaten vernichtet.
