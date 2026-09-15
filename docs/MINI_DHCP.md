# DHCPv4

## Zweck

Lokale IPv4-Adressvergabe mit Leases, Reservierungen, Ausschlüssen, DNS-/Router-
Optionen und optionalen PXE-Bootinformationen. Der vorhandene Python-DHCP-Kern
läuft im gemeinsamen Mini-Services-Worker.

## Voraussetzungen

Ein bewusst ausgewähltes LAN-Segment und eine feste lokale Serveradresse.
Pro Segment darf kein unbeabsichtigt konkurrierender DHCP-Server aktiv sein.
UDP 67 benötigt auf Linux gezielt eingerichtete Dienstrechte. Ein Interface-Bind
verwendet `SO_BINDTODEVICE`, soweit die Plattform dies unterstützt. Die Python-
Implementierung benötigt keine zusätzliche Bibliothek; Ping-Prüfung benötigt
bei Aktivierung das bereits vorhandene Systemprogramm `ping`.

## Standardbetrieb

`./start.sh` startet den Worker. DHCP bleibt deaktiviert, bis Serveradresse, Netz
und Pool zum eigenen LAN passen. Unter **Mini Services → Netzwerkdienste** die
Werte prüfen, DHCP aktivieren und speichern. Einzelaktionen und Autostart sind
in der gemeinsamen Dienstübersicht. [Gemeinsamer Betrieb und CLI](MINI_SERVICES.md).

## Konfiguration

Abschnitt `dhcp` in `mini-services.json`. JSON-Schalter müssen echte Booleans sein.
Die Oberfläche zeigt erkannte Interfaces und erlaubt weiterhin manuelle Eingaben.

| Option | Standard / Wirkung |
|---|---|
| `enabled` | `false`; bewusste Aktivierung erforderlich |
| `bind` | `127.0.0.1`; für LAN-Betrieb konkrete passende lokale Adresse wählen |
| `port` | `67`, Bereich 1–65535 |
| `interface` | leer; optional explizites OS-Interface |
| `server_ip` | Beispiel `192.168.178.1`; tatsächliche lokale DHCP-Serveradresse |
| `network` | Beispiel `192.168.178.0/24` |
| `pool_start`, `pool_end` | Beispiele `192.168.178.20` bis `192.168.178.200`; gültige Hostadressen im Netz |
| `routers` | Beispiel `192.168.178.1`; angekündigte Router |
| `dns_servers` | Beispiel `192.168.178.1`; angekündigte DNS-Server |
| `domain`, `domain_search` | `home.arpa`; lokale Domain und Suchliste |
| `ntp_servers` | leer; optionale NTP-Adressen |
| `lease_time` | `86400` Sekunden |
| `renewal_time`, `rebinding_time` | `43200`, `75600` Sekunden; Erneuerung und Rebind |
| `authoritative` | `true`; nur im bewusst verwalteten Segment verwenden |
| `ping_check` | `false`; optionale Konfliktprüfung vor Vergabe |
| `decline_hold_seconds` | `600`; abgelehnte Adressen vorübergehend sperren |
| `mtu` | `1500`; angekündigte MTU |
| `next_server`, `tftp_server`, `boot_file` | leer; optionale Bootwerte, aktive PXE-Profile ergänzen sie |
| `reservations` | leere Liste; Objekte mit `mac` oder `client_id`, `ip`, optional `hostname` |
| `exclusions` | leere Liste; nicht automatisch zu vergebende Adressen |
| `static_routes` | leere Liste; Objekte mit `network` und `gateway`, DHCP-Option 121 |
| `custom_options` | leeres Objekt; numerische Optionscodes und Textwerte, alternativ Präfixe `hex:`, `base64:`, `ip:`, `u32:` |

Beispiel einer Reservierung: `[{"mac":"02:00:00:00:00:10","ip":"192.168.178.10","hostname":"drucker"}]`.
Dieses Beispiel ist an das eigene Netz anzupassen. Doppelte Reservierungen,
ungültige Netze/Adressen, unzulässige Optionscodes und zu große Optionswerte
werden abgewiesen. „DHCP auf deaktivierte Standardwerte zurücksetzen“ betrifft
nur DHCP; die DNS-Konfiguration bleibt erhalten.

## Discovery

Erkennung lokaler Interfaces und IPv4-Adressen bei Start/Scan; keine automatische
Übernahme eines geratenen DHCP-Pools. Ein verschwundenes bekanntes Interface oder
eine entfernte Serveradresse führt zu `waiting`; Rückkehr wird alle 30 Sekunden
geprüft. Gespeicherte Auswahl und manuelle Stop-Entscheidung bleiben erhalten.
Leases zeigen Clients, die den Dienst tatsächlich angefragt haben; sie sind keine
vollständige Inventur aller Geräte im LAN.

## Ports

Server UDP 67, Clients üblicherweise UDP 68. Firewall und Broadcast-Verkehr müssen
zum Segment passen. Kein zusätzlicher HTTP-Listener für DHCP selbst.

## Security

Admin/CSRF schützen Konfiguration und Aktionen. Der Worker benötigt passende
OS-Rechte; er beschafft sie nicht selbst. Mehrfacher Start erzeugt keinen zweiten
Listener. Eine explizite Interface-Bindung schlägt auf nicht unterstützten
Plattformen verständlich fehl, statt still auf alle Interfaces auszuweichen.
Leases und Konfiguration werden atomar geschrieben. DHCP besitzt protokollbedingt
keine Authentifizierung des Clients; daher auf das verwaltete LAN begrenzen.

## Fehlerdiagnose

| Problem | Nächste Prüfung |
|---|---|
| Berechtigung fehlt | Niedriger Port und eingerichtete Dienstrechte |
| Adresse/Port belegt | Vorhandenen DHCP-Dienst und Bind-Konfiguration prüfen |
| Wartet auf Netzwerk | Interface/Serveradresse wieder verbinden oder passend auswählen |
| Kein Client erhält eine Adresse | Segment, Broadcast/Firewall, Pool und Client-Port prüfen |
| Konfiguration ungültig | Netz/Pool/Serveradresse, Reservierungen und JSON-Optionen prüfen |

Health bestätigt gebundenen Socket und aktiven Listener. Leases und Events liefern
fachliche Aktivität. Ein echter PXE-/DHCP-Clienttest ist zusätzlich erforderlich.
Startfehler verwenden begrenzten Backoff; Netzwerk-Wartezustand prüft langsam
weiter und kann durch Stop beendet werden. Leases lassen sich in der UI leeren;
dies beendet bestehende IP-Nutzung der Clients nicht sofort.

## API

ID `dhcp` in der [gemeinsamen API](MINI_SERVICES.md). Bestehende Fachkonfiguration:
`POST /admin/mini-services/save`; Reset:
`POST /admin/mini-services/network/dhcp/reset`; Leases leeren:
`POST /admin/mini-services/leases/clear`. Admin und CSRF erforderlich.

## Plattformen

Linux ist der vorgesehene Netzwerkworker-Host. Windows verwendet den gleichen
UDP-Kern, unterstützt aber das Linux-spezifische `SO_BINDTODEVICE` nicht.
Realer DHCP-Broadcast auf Windows wurde nicht abgenommen. Android/WebView kann
administrieren; ein privilegierter DHCP-Server auf Android ist nicht Standardbetrieb.

## Einschränkungen

IPv4, kein DHCPv6, kein automatischer Ersatz fremder DHCP-Server. Beispielnetze
werden niemals allein durch Discovery aktiv geschaltet. Automatische Netzänderung
könnte Clients in ein falsches Segment verschieben und ist deshalb nicht vorgesehen.

## Tests

`test_mini_services`, `test_mini_lifecycle`, `test_mini_network_recovery`,
`test_mini_network_settings`, `test_mini_control_api`: Protokoll-/Optionsprüfung,
Start/Stop/Restart, belegter Port, Fehlerisolation, Netzwerkentfernung/-rückkehr,
Reset und geschützte Steuerung ohne Spezialhardware.
