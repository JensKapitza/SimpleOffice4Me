# SimpleOffice4Me bereitstellen

SimpleOffice4Me kann als normaler Linux-Dienst, in einer Linux-VM oder per Docker betrieben werden. Die richtige Variante hängt vor allem davon ab, ob nur Web/Federation oder auch DHCP, DNS, TFTP/PXE und Routing/NAT genutzt werden sollen.

## Entscheidungshilfe

| Variante | Web/Federation | Druck | DHCP/DNS/TFTP | Routing/NAT | Empfehlung |
| --- | --- | --- | --- | --- | --- |
| Docker Standard | ja | Linux-CUPS optional | nein | nein | Webserver/Federation |
| Docker LAN | ja | Linux-CUPS optional | ja, Linux + Host-Netz | ja, mit Host-Vorbereitung | kompakter Linux-Server |
| Linux-VM | ja | Netzwerkdruck/CUPS | ja | ja | beste Isolation für alle Funktionen |
| Debian/Ubuntu nativ | ja | ja | ja | ja | kleinster Overhead |

Federation selbst läuft über HTTP(S) und braucht keine privilegierten Netzwerkrechte. Die privilegierten Rechte sind ausschließlich für die separaten Mini Services erforderlich.

## Vor dem öffentlichen Betrieb

Die Waitress-Anwendung sollte im Internet nicht direkt auf Port 8080 veröffentlicht werden. Empfohlen ist ein Reverse Proxy wie Caddy, nginx oder Traefik mit gültigem TLS-Zertifikat. Der interne SimpleOffice-Port bleibt dabei auf `127.0.0.1:8080` gebunden. Hinter genau einem Proxy wird `SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1` gesetzt.

Für Federation muss jede Instanz einen langen, zufälligen `SIMPLEOFFICE_FEDERATION_TOKEN` und eine stabile `SIMPLEOFFICE_FEDERATION_PEER_ID` bekommen. Geheimnisse gehören weder in das Image noch in Git.

DHCP, DNS und TFTP niemals ungefiltert ins Internet freigeben. Diese Dienste gehören auf die LAN-Schnittstelle. Vor Aktivierung muss geprüft werden, ob dort bereits ein anderer DHCP- oder DNS-Server läuft.

## 1. Docker Standard: Web und Federation

Voraussetzungen: Docker Engine und Docker Compose Plugin auf einem Linux-Host.

```bash
cp deploy/docker/.env.example deploy/docker/.env
# .env bearbeiten und Federation-Token/Peer-ID setzen

docker compose --env-file deploy/docker/.env \
  -f deploy/docker/compose.yaml up -d --build

docker compose -f deploy/docker/compose.yaml ps
```

Standardmäßig wird nur `127.0.0.1:8080` veröffentlicht. Für einen reinen LAN-Test kann `SIMPLEOFFICE_BIND_ADDRESS=0.0.0.0` gesetzt werden. Für einen öffentlichen Server sollte stattdessen der Reverse Proxy auf `127.0.0.1:8080` weiterleiten.

Die persistenten Daten liegen im Docker-Volume `simpleoffice-data`. Ein Container-Update oder Neubau löscht dieses Volume nicht.

### Host-Drucker aus Docker

Unter Linux kann SimpleOffice den CUPS-Spooler des Hosts verwenden. Dazu in `deploy/docker/compose.yaml` den Mount aktivieren:

```yaml
- /run/cups/cups.sock:/run/cups/cups.sock
```

Danach Container neu erstellen. Für Netzwerkdrucker kann alternativ ein erreichbarer CUPS-Server verwendet werden. Ein Linux-Container kann den Windows-Host-Spooler nicht wie eine native Windows-Anwendung ansprechen; dafür ist eine native Installation oder ein Netzwerkdrucker sinnvoller.

## 2. Docker LAN: DHCP, DNS, TFTP/PXE und Routing

Diese Variante ist nur für einen Linux-Docker-Host vorgesehen. DHCP-Broadcasts und echte Router-Funktion benötigen Zugriff auf das Host-Netz. Deshalb läuft nur der separate Mini-Services-Container mit `network_mode: host`.

Er erhält **nicht** `privileged: true`, sondern ausschließlich:

- `NET_BIND_SERVICE`
- `NET_RAW`
- `NET_ADMIN`

Der Webcontainer bleibt unprivilegiert.

Start:

```bash
docker compose --env-file deploy/docker/.env \
  -f deploy/docker/compose.yaml \
  -f deploy/docker/compose.lan.yaml \
  up -d --build
```

Vor dem Aktivieren der Dienste in der Admin-Oberfläche:

```bash
ip -br address
ip route
sudo ss -lntup | grep -E ':(53|67|69)\b' || true
```

Die LAN-Schnittstelle muss in der Mini-Services-Konfiguration eindeutig gewählt werden. Auf demselben Segment darf nur ein autoritativer DHCP-Server für denselben Adressbereich aktiv sein.

### Routing/NAT im Docker-LAN-Modus

Der Worker verwaltet ausschließlich eigene nftables-Tabellen. IPv4-Forwarding ist eine Host-Kernel-Einstellung und sollte auf dem Docker-Host dauerhaft aktiviert werden:

```bash
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-simpleoffice-router.conf
sudo sysctl --system
```

Danach Routing/NAT erst in SimpleOffice aktivieren. Falls der Host bereits als Router, Firewall oder Kubernetes-/Container-Gateway arbeitet, die bestehenden Regeln zuerst prüfen. SimpleOffice soll fremde Firewall-Tabellen nicht ersetzen.

### Portkonflikte

DNS braucht TCP/UDP 53, DHCPv4 UDP 67 und TFTP UDP 69. `systemd-resolved`, dnsmasq, libvirt oder vorhandene DHCP-Dienste können diese Ports bereits belegen. Nicht blind abschalten: zuerst feststellen, welcher Prozess die Ports nutzt und ob der Host dadurch seine eigene Namensauflösung verlieren würde.

## 3. Linux-VM

Für den vollständigen Funktionsumfang ist eine kleine Debian- oder Ubuntu-VM die robusteste Variante. Für Web/Federation allein reicht auch eine NAT-Netzwerkkarte. Für DHCP/PXE/Routing muss die relevante VM-Netzwerkkarte jedoch als **Bridge/External Network** direkt mit dem LAN verbunden sein; Hypervisor-NAT reicht für DHCP-Broadcasts nicht aus.

Empfohlener Aufbau:

1. Debian 12/13 oder aktuelle Ubuntu-LTS-VM erstellen, mindestens 2 vCPU, 2–4 GB RAM und ausreichend Datenträger für Dokumente.
2. Für Mini Services eine gebridgte virtuelle NIC verwenden und der VM eine feste LAN-IP geben.
3. Das Debian-Paket wie im Abschnitt „Native Installation“ installieren.
4. Webdienst zunächst auf `127.0.0.1:8080` belassen und HTTPS über Reverse Proxy bereitstellen.
5. Firewall nur für benötigte Dienste öffnen. DHCP/DNS/TFTP nur auf der LAN-Seite zulassen.
6. `simpleoffice-mini-services.service` kontrollieren und erst danach die einzelnen Mini Services in der Admin-Oberfläche aktivieren.

Bei zwei virtuellen NICs ist für Router-Betrieb die Trennung besonders klar: eine LAN-NIC für DHCP/DNS und eine WAN/Uplink-NIC. In SimpleOffice interne und externe Schnittstelle kontrollieren, bevor NAT aktiviert wird.

## 4. Native Debian/Ubuntu-Installation

Das Repository enthält ein installierbares Debian-Paket mit eigenem Benutzer, Venv, persistentem Datenverzeichnis und systemd-Units. Das Webinterface und der privilegierte Netzwerkworker sind getrennte Dienste.

Build-Abhängigkeiten installieren:

```bash
./build-dep.sh
```

Client-Paket vorbereiten und bauen:

```bash
cp packaging/build-client.local.sh.example packaging/build-client.local.sh
$EDITOR packaging/build-client.local.sh
bash packaging/build-client.sh
```

Lizenz-Master/Server entsprechend mit `packaging/build-server.sh`. Installation:

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

Der Webprozess läuft als Benutzer `simpleoffice` ohne privilegierte Netzwerkrechte. Nur `simpleoffice-mini-services.service` erhält `CAP_NET_BIND_SERVICE`, `CAP_NET_RAW` und `CAP_NET_ADMIN`.

Laufzeitdaten liegen unter `/var/lib/simpleoffice4me`, Konfiguration unter `/etc/simpleoffice4me/simpleoffice.env`. Paketupdates sollen das Datenverzeichnis nicht löschen.

## Reverse-Proxy-Beispiel

Bei einem externen TLS-Proxy bleibt SimpleOffice lokal gebunden:

```ini
SIMPLEOFFICE_HOST=127.0.0.1
SIMPLEOFFICE_PORT=8080
SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1
```

Der Proxy terminiert HTTPS und leitet HTTP lokal an Port 8080 weiter. Nur 80/443 werden aus dem Internet erreichbar gemacht. Federation nutzt dieselbe HTTPS-Basis-URL.

## Backup und Update

Vor Updates mindestens `/var/lib/simpleoffice4me` sichern. Bei Docker das benannte Volume sichern; bei VM/nativer Installation das Verzeichnis bzw. einen VM-Snapshot verwenden. Ein Snapshot ersetzt kein zusätzliches Backup auf einem anderen Datenträger.

Nach Updates prüfen:

```bash
# Docker
docker compose -f deploy/docker/compose.yaml ps

# VM/nativ
systemctl --no-pager --full status simpleoffice4me simpleoffice-mini-services
```

Zusätzlich im Webinterface Federation-Peers, Drucker und bei Nutzung der Mini Services DHCP-/DNS-/TFTP-Status kontrollieren.

## Sicherheitsgrenze

Docker wird absichtlich nicht pauschal mit `--privileged` betrieben. Web und Federation brauchen diese Rechte nicht. Für LAN-Funktionen bekommt nur der Netzwerkworker die drei notwendigen Linux-Capabilities. Wer aus Kompatibilitätsgründen `--privileged` verwendet, erweitert die Verantwortung und Angriffsfläche erheblich und verlässt die hier dokumentierte Standardkonfiguration.
