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
Health erkennt fehlende eigene Tabellen, abweichende Chain-Typen/Hooks,
Prioritäten/Policies, inaktive Tabellen, fehlende oder zusätzliche Regeln und
deaktiviertes IPv4-Forwarding. Die von SimpleOffice erzeugten Regelausdrücke werden einschließlich Reihenfolge,
Interface-Richtung, Verbindungszuständen, Quellnetz und Aktion verglichen. Bestätigte Healthfehler lösen
die begrenzte Worker-Recovery aus; fremde Tabellen werden dabei nicht verändert.
Ein unlesbarer Status löst keine Regeländerung aus. Windows-Regeländerungen sind nicht transaktional
wie nftables; Plattformtests bleiben erforderlich.

## Tests

`test_gateway_lifecycle`, `test_mini_network_recovery`, `test_mini_network_settings`,
`test_mini_control_api`: Stopfehler, atomare Linux-Transaktion, fehlende Rechte,
Istzustand, manuelle Stop-Semantik, Netzwerkwechsel, persistente validierte
Einstellungen und Auth/CSRF ohne privilegierte Hardwaretests.

## Reload unter Linux

Bei einem aktiven Linux-Gateway ersetzen Konfigurationsänderungen und expliziter
Neustart die eigenen nftables-Tabellen in einer Transaktion. Der Worker entfernt
sie davor nicht mehr. Lehnt nft die Transaktion ab, bleiben die bisherigen Regeln
bestehen. Gespeicherte Wunschkonfiguration und zuletzt erfolgreich angewendete
Konfiguration können dann voneinander abweichen; Diagnose und Reload-Fehler sind
im Status sichtbar. Nach einer Korrektur erneut speichern oder Neustart auslösen.
Die Laufzeit beginnt bei einem erfolgreichen Reload nicht erneut.

Die Ownership-Datei wird vor Anwendung geschrieben. Beide Linux-Modi verwenden
dieselben festen Tabellennamen; die Datei ermöglicht daher auch nach einem
Prozessabsturz das Aufräumen. Scheitert schon das Schreiben, wird nft nicht
aufgerufen. Ein Timeout bestätigt weder Erfolg noch Ablehnung: Regeln werden
nicht vorsorglich gelöscht, der Status wird degraded und erneut geprüft. Der
Healthcheck ersetzt dabei keine vollständige Prüfung der angewendeten Regeln.

Die Atomarität betrifft ausschließlich die nft-Transaktion. IPv4-Forwarding ist
gemeinsamer Hostzustand und kann vor der Transaktion aktiviert werden; es wird
nicht automatisch zurückgesetzt. Windows-Reload, Neuaufbau nach Stop und
Health-Recovery besitzen diese Garantie nicht. Deaktivieren entfernt weiterhin
nur die eigenen Regeln.

Regression: `test_gateway_reload` prüft Konfigurationswechsel, identischen
Neustart, abgewiesene Anwendung, Timeout, Ownership-Schreibfehler, Deaktivieren
und den unveränderten Windows-Pfad ohne Eingriff in das Hostnetzwerk.

## Lesende Prüfung der Linux-Regelstruktur

Nach erfolgreicher Anwendung enthält der Laufzeitstatus die erwarteten
Regelanzahlen je Chain. Der Healthcheck liest ausschließlich die eigenen
Tabellen mit `nft -j list table` (jeweils zwei Sekunden Timeout) und prüft
Filter-/NAT-Chain, Hook, numerische Priorität, Policy, Tabellenflags sowie
Regelanzahl und Chain-Zuordnung. Ein bewusst leerer Forward-Regelsatz mit
Drop-Policy ist gültig. Veränderte Handles beeinflussen die Prüfung nicht.

Fehlende Soll-Metadaten, ungültiges JSON und Lesefehler ergeben einen unbekannten
Healthstatus statt eines bestätigten Fehlers; dadurch wird keine automatische
Recovery aufgrund eines Parser-/Berechtigungsfehlers ausgelöst. Strukturfehler
sind bestätigte Healthfehler und verwenden die vorhandene begrenzte Recovery.
Zusätzlich werden die Regelausdrücke mit der zuletzt erfolgreich angewendeten
Konfiguration verglichen. Ausgetauschte Adressen oder Aktionen werden auch bei
gleicher Regelanzahl erkannt. Tatsächlicher Paketfluss bleibt außerhalb der Prüfung. Es wird kein Internetzugang getestet.

Schema/CLI: [nftables-Dokumentation](https://netfilter.org/projects/nftables/manpage.html).
`test_gateway_rule_structure` prüft gültige/leere Chains, abweichende Attribute,
NAT, entfernte/zusätzliche Regeln, veränderte Handles, ungültige JSON-Antworten und
Soll-Metadaten. Kein nft-Paket installiert und keine echten Kernelregeln verändert.

## Regelinhalte im Healthcheck

Der Laufzeitstatus enthält `rule_expressions` als Sollwerte für die eigenen
Forward- und Postrouting-Regeln. Der Vergleich umfasst alle Statements und ihre
Reihenfolge. Handles werden nicht verglichen; die Reihenfolge der Namen in einer
Conntrack-Bitmaskenliste und leere Masquerade-Optionen (`{}`/`null`) werden
normalisiert. Zusätzliche Statements, andere Operatoren, Interfaces, Präfixe,
Zustände oder NAT-Optionen gelten als Abweichung. Die Regelanzahl wird aus
denselben Sollausdrücken abgeleitet.

Fehlende Sollausdrücke ergeben unbekannten Healthstatus. Der Vergleich ist auf
die von SimpleOffice erzeugten Regeln begrenzt, kein allgemeiner semantischer
Firewall-Vergleich. Andere äquivalente Darstellungen werden nicht pauschal als
gleich angenommen. Tests verwenden das dokumentierte JSON-Schema; die Abnahme
gegen reale unterstützte nft-Versionen und Kernel-Paketfluss bleibt offen.

Referenz: [libnftables-json(5), mit dem nftables-Paket ausgeliefertes Handbuch](https://man.archlinux.org/man/libnftables-json.5.en).

## Windows-Neustart bei unverändertem NAT

Bleiben Betriebsart, NAT-Name und internes Netz gleich, verwendet der Worker
jetzt einen Reload ohne vorherigen Stop. Die Anwendung liest das NAT mit
terminierenden Fehlern, prüft das vorhandene Präfix und erstellt nur ein fehlendes
Objekt. Ein vorhandenes passendes NAT wird nicht gelöscht. Ein abweichendes
Präfix führt vor den Forwarding-Befehlen zum Fehler. Bei Reload-Fehlern bleibt die
Ownership erhalten; der Worker führt keine pauschale Ressourcenlöschung aus.

Namens-, Netz- oder Betriebsartwechsel nutzen weiterhin Stop/Start. Auch der
Reload ist keine Windows-Transaktion: Teiländerungen des gemeinsamen Forwardings
und unklarer Ausgang bei Timeout sind möglich. Reale PowerShell-/Windows-NAT-
Tests bleiben offen. Gemockte Systemgrenzen prüfen Befehlsreihenfolge,
fehlende Löschbefehle, Fehlerweitergabe und Worker-Ressourcenerhalt.
