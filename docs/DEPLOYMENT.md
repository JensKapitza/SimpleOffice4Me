# SimpleOffice4Me bereitstellen

SimpleOffice4Me kann nativ, in einer Linux-VM oder per Docker betrieben werden. Entscheidend ist, ob nur Web/Federation oder auch DHCP, DNS, TFTP/PXE und Routing/NAT genutzt werden sollen.

## Entscheidungshilfe

| Variante | Web/Federation | Druck | DHCP/DNS/TFTP | Routing/NAT | Empfehlung |
| --- | --- | --- | --- | --- | --- |
| Docker Standard | ja | Linux-CUPS optional | nein | nein | Web/Federation |
| Docker LAN | ja | Linux-CUPS optional | ja, Linux + Host-Netz | ja, mit Host-Vorbereitung | kompakter Linux-Server |
| Linux-VM | ja | Netzwerkdruck/CUPS | ja | ja | beste Isolation für alle Funktionen |
| Debian/Ubuntu nativ | ja | ja | ja | ja | kleinster Overhead |

Federation läuft über HTTP(S) und benötigt keine privilegierten Netzwerkrechte. Privilegien sind nur für den getrennten Mini-Services-Worker nötig.

## Vor dem öffentlichen Betrieb

Die Waitress-Anwendung sollte nicht direkt aus dem Internet auf Port 8080 erreichbar sein. Empfohlen ist ein Reverse Proxy wie Caddy, nginx oder Traefik mit TLS. SimpleOffice bleibt intern auf `127.0.0.1:8080`; hinter genau einem vertrauenswürdigen Proxy wird `SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1` gesetzt.

Jede Federation-Instanz benötigt einen langen zufälligen `SIMPLEOFFICE_FEDERATION_TOKEN` und eine stabile `SIMPLEOFFICE_FEDERATION_PEER_ID`. Geheimnisse gehören weder in Images noch in Git.

DHCP, DNS und TFTP niemals ungefiltert ins Internet freigeben. Diese Dienste gehören auf die LAN-Schnittstelle. Vor Aktivierung prüfen, ob dort bereits DHCP/DNS/TFTP läuft.

## 1. Docker Standard: Web und Federation

Voraussetzungen: Docker Engine und Docker Compose Plugin auf Linux.

```bash
cp deploy/docker/.env.example deploy/docker/.env
# .env bearbeiten; Federation-Token und Peer-ID setzen

docker compose --env-file deploy/docker/.env \
  -f deploy/docker/compose.yaml up -d --build

docker compose -f deploy/docker/compose.yaml ps
```

Standardmäßig wird nur `127.0.0.1:8080` veröffentlicht. Der interne Container-Port bleibt fest 8080; der Host-Port kann mit `SIMPLEOFFICE_HTTP_PORT` geändert werden. Für einen LAN-Test kann `SIMPLEOFFICE_BIND_ADDRESS=0.0.0.0` gesetzt werden. Für einen öffentlichen Server sollte der Reverse Proxy dagegen auf den lokalen Port weiterleiten.

Daten liegen persistent im Volume `simpleoffice-data`. Ein Image- oder Container-Update löscht dieses Volume nicht.

### Rechte des Webcontainers

Der Container startet nur zur Initialisierung eines neuen Volumes als root. Dafür bleiben nach `cap_drop: ALL` lediglich `CHOWN`, `SETUID` und `SETGID`. Anschließend wird Waitress mit `gosu` als Benutzer `simpleoffice` (UID/GID 10001) gestartet. Der Webprozess besitzt keine DHCP-/Routing-Capabilities.

### Host-Drucker aus Docker

Unter Linux kann SimpleOffice den CUPS-Spooler des Hosts verwenden. Dazu in `deploy/docker/compose.yaml` den vorhandenen optionalen Mount aktivieren:

```yaml
- /run/cups/cups.sock:/run/cups/cups.sock
```

Für Netzwerkdrucker kann alternativ ein erreichbarer CUPS-Server genutzt werden. Ein Linux-Container kann den Windows-Host-Spooler nicht wie eine native Windows-Anwendung verwenden; dafür native Installation oder Netzwerkdrucker einsetzen.

## 2. Docker LAN: DHCP, DNS, TFTP/PXE und Routing

Nur für Linux-Docker-Hosts. DHCP-Broadcasts und echte Router-Funktion benötigen das Host-Netz. Deshalb läuft ausschließlich der separate `mini-services`-Container mit `network_mode: host`.

Er wird **nicht** mit `privileged: true` gestartet. Nach `cap_drop: ALL` erhält er:

- `DAC_OVERRIDE`, ausschließlich damit der getrennte Worker auf das gemeinsam gemountete State-Volume zugreifen kann; das Image-Dateisystem bleibt read-only
- `NET_BIND_SERVICE` für DNS/DHCP/TFTP-Ports
- `NET_RAW` für DHCP-/Netzwerkfunktionen
- `NET_ADMIN` für Interface-, Routing- und nftables-Funktionen

Zusätzlich gelten `no-new-privileges` und ein read-only Root-Filesystem. Der Webcontainer bleibt davon getrennt.

Start:

```bash
docker compose --env-file deploy/docker/.env \
  -f deploy/docker/compose.yaml \
  -f deploy/docker/compose.lan.yaml \
  up -d --build
```

Vor Aktivierung in der Admin-Oberfläche:

```bash
ip -br address
ip route
sudo ss -lntup | grep -E ':(53|67|69)\b' || true
```

Die LAN-Schnittstelle muss eindeutig gewählt werden. Auf demselben Segment darf nur ein autoritativer DHCP-Server für denselben Adressbereich aktiv sein.

### Routing/NAT im Docker-LAN-Modus

IPv4-Forwarding ist eine Host-Kernel-Einstellung und sollte dauerhaft auf dem Docker-Host aktiviert werden:

```bash
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-simpleoffice-router.conf
sudo sysctl --system
```

Danach Routing/NAT in SimpleOffice aktivieren. Der Worker verwaltet eigene nftables-Tabellen und soll fremde Firewall-Tabellen nicht ersetzen. Auf Hosts mit vorhandener Router-, Firewall-, Kubernetes- oder Container-Gateway-Funktion Regeln vorher prüfen.

### Portkonflikte

DNS braucht TCP/UDP 53, DHCPv4 UDP 67 und TFTP UDP 69. `systemd-resolved`, dnsmasq, libvirt oder vorhandene DHCP-Dienste können diese Ports belegen. Nicht blind abschalten: zuerst feststellen, welcher Prozess den Port nutzt und welche Folgen das Abschalten hätte.

## 3. Linux-VM

Für den vollständigen Funktionsumfang ist eine Debian-/Ubuntu-VM die robusteste Variante. Für Web/Federation reicht auch eine NAT-NIC. Für DHCP/PXE/Routing muss die relevante virtuelle Netzwerkkarte als **Bridge/External Network** direkt mit dem LAN verbunden sein; Hypervisor-NAT reicht für DHCP-Broadcasts nicht aus.

Empfohlen:

1. Debian 12/13 oder aktuelle Ubuntu LTS, 2 vCPU, 2–4 GB RAM und ausreichend Dokumentenspeicher.
2. Feste LAN-IP bzw. feste DHCP-Reservierung.
3. Für Mini Services eine gebridgte NIC verwenden.
4. Das Debian-Paket aus dem Abschnitt „Native Installation“ installieren.
5. Web zunächst nur auf `127.0.0.1:8080` belassen und per HTTPS-Reverse-Proxy veröffentlichen.
6. Firewall nur für tatsächlich benötigte Dienste öffnen.
7. `simpleoffice-mini-services.service` kontrollieren und die einzelnen LAN-Dienste erst danach in der Admin-Oberfläche aktivieren.

Für Router-Betrieb sind zwei virtuelle NICs sinnvoll: eine LAN-NIC und eine WAN/Uplink-NIC. Vor NAT die erkannte interne und externe Schnittstelle kontrollieren.

Eine kompakte VM-Checkliste liegt zusätzlich unter `deploy/vm/README.md`.

## 4. Native Debian/Ubuntu-Installation

Das Repository enthält ein installierbares Debian-Paket mit eigenem Benutzer, Venv, persistentem State und getrennten systemd-Units.

Build-Abhängigkeiten:

```bash
./build-dep.sh
```

Client bauen:

```bash
cp packaging/build-client.local.sh.example packaging/build-client.local.sh
$EDITOR packaging/build-client.local.sh
bash packaging/build-client.sh
```

Lizenz-Master/Server entsprechend mit `packaging/build-server.sh` bauen.

Installation:

```bash
sudo apt install ./dist/packages/simpleoffice4me-client_*.deb
sudo systemctl enable --now simpleoffice4me
sudo systemctl enable --now simpleoffice-mini-services
```

Status und Logs:

```bash
systemctl status simpleoffice4me simpleoffice-mini-services
journalctl -u simpleoffice4me -f
journalctl -u simpleoffice-mini-services -f
```

Der Webprozess läuft als `simpleoffice` ohne privilegierte Netzwerkrechte. Nur `simpleoffice-mini-services.service` erhält nativ `CAP_NET_BIND_SERVICE`, `CAP_NET_RAW` und `CAP_NET_ADMIN`.

Laufzeitdaten liegen unter `/var/lib/simpleoffice4me`, die Laufzeitkonfiguration unter `/etc/simpleoffice4me/simpleoffice.env`. Paketupdates löschen die Instanzdaten nicht. Siehe auch `deploy/install/README.md` und `packaging/README-system-package.md`.

## Reverse Proxy

Bei einem externen TLS-Proxy:

```ini
SIMPLEOFFICE_HOST=127.0.0.1
SIMPLEOFFICE_PORT=8080
SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1
```

Nur 80/443 werden aus dem Internet erreichbar gemacht. Federation nutzt dieselbe HTTPS-Basis-URL.

## Backup und Update

Vor Updates mindestens `/var/lib/simpleoffice4me` sichern. Bei Docker das Volume sichern; bei VM/nativ zusätzlich sind VM-Snapshots möglich. Ein Snapshot ersetzt kein Backup auf einem anderen Datenträger.

Nach Updates:

```bash
# Docker
docker compose -f deploy/docker/compose.yaml ps

# VM/nativ
systemctl --no-pager --full status simpleoffice4me simpleoffice-mini-services
```

Danach im Webinterface Federation-Peers, Drucker und bei Nutzung der Mini Services DHCP-/DNS-/TFTP-Status kontrollieren.

## Sicherheitsgrenze

Docker wird absichtlich nicht pauschal mit `--privileged` betrieben. Web/Federation benötigen diese Rechte nicht. Nur der getrennte Netzwerkworker erhält die dokumentierten Capabilities. Wer `--privileged` verwendet, erweitert Angriffsfläche und Verantwortung erheblich und verlässt die unterstützte Standardkonfiguration.
