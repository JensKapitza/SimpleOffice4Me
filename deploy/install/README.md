# Native Installation

Für Debian und Ubuntu ist das bestehende `.deb`-Paket die bevorzugte Installationsversion. Es trennt den normalen Webdienst vom privilegierten Mini-Services-Worker und hält Laufzeitdaten unter `/var/lib/simpleoffice4me`.

Aus einem Checkout bauen:

```bash
./build-dep.sh
cp packaging/build-client.local.sh.example packaging/build-client.local.sh
$EDITOR packaging/build-client.local.sh
bash packaging/build-client.sh
```

Server/Lizenz-Master entsprechend über `packaging/build-server.sh` bauen. Für automatisierte Builds können `SIMPLEOFFICE_SERVER_PUBLIC_URL` und `SIMPLEOFFICE_SERVER_PEER_ID` statt einer lokalen Konfigurationsdatei gesetzt werden.

Installation:

```bash
sudo apt install ./dist/packages/simpleoffice4me-client_*.deb
sudo systemctl enable --now simpleoffice4me
sudo systemctl enable --now simpleoffice-mini-services
```

Konfiguration:

- `/etc/simpleoffice4me/simpleoffice.env`: Host, Port, Proxy- und Federation-Einstellungen
- `/var/lib/simpleoffice4me`: persistente Daten und Dokumente
- `simpleoffice4me.service`: unprivilegierter Webdienst
- `simpleoffice-mini-services.service`: separater DHCP/DNS/TFTP/Routing-Worker mit gezielten Linux-Capabilities

Für ein fertiges Server-Artefakt kann außerdem der GitHub-Workflow **Server DEB build** manuell gestartet werden. Er verlangt die öffentliche Server-URL, baut das vollständige `simpleoffice4me-server`-Paket, prüft Paketmetadaten und Abhängigkeiten und veröffentlicht das `.deb` zusammen mit SHA-256.

Federation-Geheimnisse nach der Installation lokal setzen; nicht in Build-Skripte oder Pakete einbetten. Für Details, VM- und Docker-Betrieb siehe [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md) und [`packaging/README-system-package.md`](../../packaging/README-system-package.md).
