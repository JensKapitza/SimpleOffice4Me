# VM-Betrieb

Die vollständige Anleitung steht in [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md). Diese Kurzliste ist für das Anlegen einer neuen VM gedacht.

## Empfohlen

- Debian 12/13 oder aktuelle Ubuntu LTS
- 2 vCPU, 2–4 GB RAM
- Systemplatte plus ausreichend Speicher für `/var/lib/simpleoffice4me`
- feste IP oder feste DHCP-Reservierung
- Web/Federation: NAT-NIC ist möglich
- DHCP/DNS/TFTP/PXE: gebridgte/External-NIC direkt im LAN erforderlich
- Router/NAT: vorzugsweise zwei NICs, LAN und Uplink getrennt

## Installation in der VM

Das native Debian-Paket ist die empfohlene Installationsart:

```bash
sudo apt install ./simpleoffice4me-client_*.deb
sudo systemctl enable --now simpleoffice4me
sudo systemctl enable --now simpleoffice-mini-services
systemctl status simpleoffice4me simpleoffice-mini-services
```

Vor dem Aktivieren von DHCP/DNS/TFTP:

```bash
ip -br address
ip route
sudo ss -lntup | grep -E ':(53|67|69)\b' || true
```

Für Router/NAT zusätzlich IPv4-Forwarding dauerhaft einschalten:

```bash
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-simpleoffice-router.conf
sudo sysctl --system
```

Der Webdienst sollte für Internetbetrieb weiterhin nur lokal lauschen und hinter einem HTTPS-Reverse-Proxy stehen. Bei genau einem Proxy `SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1` setzen.

Wichtig: Bei DHCP muss die virtuelle Netzwerkkarte tatsächlich im LAN gebridgt sein. Hypervisor-NAT transportiert die notwendigen DHCP-Broadcasts nicht zuverlässig zum physischen Segment.
