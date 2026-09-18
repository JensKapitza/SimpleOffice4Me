# DNS

## Zweck

Lokaler UDP-/TCP-Resolver mit eigenen Records, Cache, Weiterleitung an Upstreams,
Allowlist und optionalen Domain-Blocklisten. DNS kann Namen auflösen oder
umleiten, aber keine HTTP-URL-Pfade verändern.

## Voraussetzungen

Eine konkrete lokale Bind-Adresse. Für nicht lokale Namen ein erreichbarer DNS-
Upstream. Niedrige Ports erfordern passende OS-Dienstrechte; Port 53 wird häufig
bereits von einem Systemresolver verwendet. Der Kern nutzt Python-Standardbibliotheken.

## Standardbetrieb

`./start.sh` startet den Worker. DNS bleibt zunächst deaktiviert. Unter
**Mini Services → Netzwerkdienste** Bind-Adresse und Upstreams prüfen, aktivieren
und speichern. DHCP-Clients verwenden diesen Resolver erst, wenn seine Adresse
als DNS-Server angekündigt/eingetragen ist. [Gemeinsamer Betrieb](MINI_SERVICES.md)
beschreibt Start/Stop/Restart, Autostart, CLI und Status.

## Konfiguration

Abschnitt `dns` in `mini-services.json`:

| Option | Standard / Bedeutung |
|---|---|
| `enabled` | `false` |
| `bind` | `["127.0.0.1"]`; konkrete IPv4-/IPv6-Adressen, kein Wildcard-Bind |
| `port` | `53`, Bereich 1–65535, UDP und TCP |
| `upstreams` | `["1.1.1.1","9.9.9.9"]`; bewusst auswählbare DNS-Ziele |
| `timeout` | `2.0` Sekunden, Bereich 0,2–30 |
| `cache_enabled` | `true` |
| `cache_max_entries` | `10000`; begrenzter Cache |
| `query_log` | `true`; DNS-Anfragen als fachliches Diagnoselog |
| `query_log_max_bytes` | 10 MiB; begrenztes Log |
| `records` | leere Liste; eigene DNS-Regeln |
| `manual_blocks` | leere Liste; manuelle Domain-Sperren |
| `allowlist` | leere Liste; Ausnahmen von Sperren |
| `block_mode` | `zero`; alternativ `nxdomain` oder `refused` |
| `blocklist_urls` | leere Liste; höchstens 20 HTTPS-Feeds |
| `blocklist_refresh_hours` | `24`; periodische Aktualisierung |

Eigene Records unterstützen `A`, `AAAA`, `CNAME`, `TXT`, `PTR`, `MX`, `SRV`.
Beispiel: `[{"name":"nas.home.arpa","type":"A","value":"192.168.178.5","ttl":300}]`.
Bei MX enthält `value` den Text `Priorität Hostname`, bei SRV
`Priorität Gewicht Port Ziel`. Wildcard-Namen wie `*.intern.example` sind möglich. Die vorhandenen
Validatoren prüfen Werte, TTLs und Grenzen vor Änderung der Listener.
Reset stellt nur DNS auf deaktivierte Standardwerte zurück und erhält DHCP.

## Discovery

Scan zeigt lokale Netzwerkinterfaces/IPv4-Adressen. Upstreams sind bewusst
konfigurierte Resolver; es werden keine fremden DNS-Dienste ungefragt als
vertrauenswürdig übernommen. Der Worker prüft bekannte IPv4-Bindings bei
Netzwerkwechsel alle 30 Sekunden und nimmt die gespeicherte Auswahl wieder auf.

## Ports

UDP und TCP am konfigurierten Port, standardmäßig 53. Ausgehende Anfragen gehen
an konfigurierte Upstreams. Blocklisten-Downloads verwenden HTTPS. Dieser Dienst
ist kein DoH-/DoT-Server und öffnet keinen zusätzlichen Verwaltungsport.

## Security

Konfiguration und Aktionen erfordern Admin/CSRF. Bind nur an benötigten lokalen
Adressen; keine unnötige Internet-Exposure. Maximal 32 gleichzeitige Anfragen;
kein unbegrenzter Thread-/Submission-Pool. Partieller UDP-/TCP-Start räumt bereits
geöffnete Sockets auf. Upstreams und interne HTTPS-Feeds sind administrative
Vertrauensentscheidungen, keine öffentliche URL-Fetch-Funktion.

Blocklisten erlauben nur HTTPS ohne Zugangsdaten. Jeder Redirect wird vor dem
nächsten Request geprüft; ein HTTP-Downgrade wird abgewiesen. Downloads und
Domainanzahl sind begrenzt. Ein Download läuft getrennt vom Worker-Heartbeat;
Stop verhindert weitere Downloads und Veröffentlichung nach Abbruch.
DNS-Logs enthalten Clientadressen und Domainnamen; bei Bedarf deaktivieren oder
über die Oberfläche löschen. Keine Tokens oder Request-Header in Laufzeitlogs.

## Fehlerdiagnose

| Problem | Prüfung / nächste Aktion |
|---|---|
| Port belegt | Systemresolver oder anderen DNS-Server prüfen; konkrete Bind-Adresse/Port auswählen |
| Keine externen Antworten | Upstream, Timeout und Netzwerkverbindung prüfen |
| Lokaler Name falsch | Record-Typ/Wert, Allowlist/Blockregel und Client-DNS-Einstellung prüfen |
| Blockliste offline | Vorhandene Liste bleibt nutzbar; Feed-Adresse/TLS prüfen, erneut aktualisieren |
| Wartet auf Netzwerk | Gespeicherte IPv4-Adresse ist entfernt; neu verbinden oder Auswahl ändern |

Health bestätigt UDP-/TCP-Sockets und aktive Listener. Er beweist keine erfolgreiche
Upstream-Auflösung; Anfragelog und Clienttest ergänzen ihn. Lokale Records und
vorhandene Cachewerte können offline nutzbar bleiben; neue externe Antworten
benötigen einen erreichbaren Upstream. Fehlende IPv4-Bindings erholen sich bei
Rückkehr, Startfehler verwenden begrenzten Backoff.

## API

ID `dns` in der [gemeinsamen API](MINI_SERVICES.md).
`POST /admin/mini-services/save` speichert die vorhandene Fachkonfiguration,
`POST /admin/mini-services/network/dns/reset` setzt DNS zurück und
`POST /admin/mini-services/dns-log/clear` leert das Querylog. Die Oberfläche bietet
zusätzlich Blocklisten-Aktualisierung. Alle Mutationen sind Admin-/CSRF-geschützt.

## Plattformen

Gleicher Python-UDP-/TCP-Kern unter Linux und Windows; Bind-/Firewallregeln müssen
auf dem Zielsystem geprüft werden. Android/WebView ist Bedienoberfläche, kein
Standardhost für den privilegierten Resolver. IPv6 funktioniert als expliziter
Bind, ist aber nicht Bestandteil des automatischen IPv4-Linkverlust-Inventars.

## Einschränkungen

Kein vollständiger Ersatz für alle Funktionen eines großen DNS-Servers; kein
automatischer DNSSEC-Validierungsdienst und kein verschlüsselter Resolverlistener.
LAN-/Windows-Abnahme und echte Clienttests bleiben erforderlich.

## Tests

`test_mini_services`, `test_mini_lifecycle`, `test_mini_security`,
`test_mini_network_recovery`, `test_mini_network_settings`, `test_mini_control_api`:
Konfiguration/Protokoll, Start/Stop/Restart, Portkonflikt, partieller Start,
begrenzte Parallelität, Offline-Blocklisten, Redirectschutz, Netzwerkrückkehr,
Reset und Auth/CSRF.
