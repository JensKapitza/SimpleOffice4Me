# Routing / NAT

## Zweck

Der vorhandene Netzwerkworker richtet lokale IPv4-Weiterleitung und optional NAT
ein. Linux verwendet eigene nftables-Tabellen, Windows einen benannten NetNat-
Eintrag. Bestehende Firewallregeln anderer Anwendungen werden nicht gelöscht.

## Voraussetzungen

Linux: vorhandene Werkzeuge `ip`, `nft`, `sysctl` und gezielt eingerichtete Rechte.
Windows: vorhandene PowerShell-Netzwerkcmdlets, für NAT `New-NetNat`, passende
Systemrechte. Ohne diese Voraussetzungen meldet der Dienst einen Fehler. Keine
automatische Installation, kein automatisches Root, kein Stop des NetworkManagers.

## Standardbetrieb

`./start.sh` startet den Worker, aber Routing bleibt standardmäßig deaktiviert.
Unter **Mini Services → Netzwerkdienste → Routing / NAT** die benötigte Betriebsart
und das interne Netz wählen. Status, Start/Stop/Restart und Scan sind in der
Dienstübersicht. Der [gemeinsame Betrieb](MINI_SERVICES.md) erklärt Autostart und CLI.

## Konfiguration

Datei: `mini-services/gateway.json` neben der gemeinsamen Konfiguration.

| Option | Standard / Wirkung |
|---|---|
| `version` | automatisch `1` |
| `enabled` | `false`; fachliche Aktivierung |
| `mode` | `off`; alternativ `route` oder `nat` |
| `auto_detect` | `true`; interne Adresse/Netz und Standardroute auswerten |
| `internal_interface` | leer; automatisch, manuelle Auswahl hat Vorrang |
| `external_interface` | leer; automatisch, für NAT erforderlich |
| `internal_network` | deaktiviertes Beispiel `192.168.178.0/24`; an eigenes Netz anpassen |
| `forward_ipv4` | `true`; Linux setzt globales IPv4-Forwarding, wenn aktiviert |
| `allow_established` | `true`; Linux erlaubt bestehende Verbindungen |
| `allow_lan_to_wan` | `true`; Linux erlaubt interne → externe Schnittstelle |
| `allow_wan_to_lan` | `false`; Linux erlaubt diesen Weg nur nach Aktivierung |
| `nat_name` | `SimpleOfficeMiniNat`; ausschließlich dieser Windows-NAT-Eintrag wird verwaltet |

Bei aktiviertem DHCP verwendet Gateway dessen Netz. Bei deaktiviertem DHCP gilt
`internal_network` unabhängig von den DHCP-Beispielwerten. Windows richtet die
erforderliche Interface-Weiterleitung ein; Richtungsfilter bleiben bei der
bestehenden Windows-Firewall. Linux-Richtungsoptionen sind keine Windows-Firewall-
Konfiguration. Zurücksetzen stellt die deaktivierten Standardwerte wieder her.

## Discovery

Scan zeigt lokale Interfaces und IPv4-Präfixe. Automatik wählt eine Schnittstelle
im internen Netz und eine andere Schnittstelle mit Standardroute als WAN. Eine
manuelle Auswahl bleibt gespeichert. Nach Netzwerkwechsel werden effektive
Bindings erneut bestimmt; identische Werte lösen keinen Neustart aus.

## Ports

Gateway öffnet keinen eigenen Listener. Es verändert IPv4-Weiterleitung und bei
NAT Quelladressübersetzung. Portfreigaben ins interne Netz werden nicht erzeugt.

## Security

Admin und CSRF schützen Einstellungen und Aktionen. Befehle werden als Argument-
Listen ausgeführt; Interface-Namen werden auch nach Discovery validiert. Linux
ändert nur `inet simpleoffice_mini` und `ip simpleoffice_mini_nat`. Ersetzen und
Entfernen erfolgen als eine nftables-Transaktion; fehlgeschlagene Änderungen
lassen vorherige Regeln bestehen. Die eigene Linux-Forward-Chain hat eine
Drop-Policy; zusätzliche Host-Firewalls können den Verkehr weiterhin blockieren.

Stop entfernt eigene Regeln/NAT, schaltet aber globales Forwarding nicht ab,
weil andere Dienste es benötigen können. Ein gescheiterter Stop wird nicht als
Erfolg gemeldet; der Worker behält die Ressourcen-Zuordnung für erneute Versuche.

## Fehlerdiagnose

Alle 15 Sekunden werden eigene Tabellen/NAT und IPv4-Forwarding geprüft. Fehlende
Regeln bedeuten `degraded`; nicht lesbarer Status bleibt unbekannt. Dies ist keine
Prüfung des Internetzugangs. Bei fehlender Adresse meldet der Worker `waiting`
und prüft alle 30 Sekunden erneut. Ein expliziter Stop beendet diesen Wiederanlauf.

Bei Fehlern: Schnittstellen neu suchen, Netz/Modus prüfen, danach Dienstrechte
und Werkzeuge prüfen. „Start vorgemerkt“ bedeutet noch nicht „Routing läuft“.
Diagnose und Ereignisse stehen in der Dienstübersicht; keine Rechteerhöhung
aus dem Browser heraus.

## API

ID `gateway` in der [gemeinsamen API](MINI_SERVICES.md).
`POST /admin/mini-services/gateway/settings` speichert die Formularfelder oder
setzt mit `action=reset` zurück. Einstellungen werden vor Anwendung validiert;
ungültige Eingaben bleiben im Formular sichtbar.

## Plattformen

Linux und Windows besitzen eigene bestehende Backends. Reale Routingänderungen
auf Zielhardware wurden hier nicht ausgeführt. Android/WebView ist eine
Bedienoberfläche; Android ist kein unterstützter Gateway-Host.

## Einschränkungen

IPv4, kein vollständiger Firewallmanager, kein IPv6-NAT, keine WAN-Portfreigaben.
Nach externen Regeländerungen zeigt Health den abweichenden Zustand; automatisches
Zurücksetzen fremder Firewallentscheidungen erfolgt nicht. Neustart kann die
eigenen Regeln erneut anwenden. Windows-Regeländerungen sind nicht transaktional
wie nftables; Plattformtests bleiben erforderlich.

## Tests

`test_gateway_lifecycle`, `test_mini_network_recovery`, `test_mini_network_settings`,
`test_mini_control_api`: Stopfehler, atomare Linux-Transaktion, fehlende Rechte,
Istzustand, manuelle Stop-Semantik, Netzwerkwechsel, persistente validierte
Einstellungen und Auth/CSRF ohne privilegierte Hardwaretests.
