# Mini Services: gemeinsamer Betrieb

## Einstieg

`./start.sh` startet SimpleOffice und den vorhandenen Mini-Services-Worker.
`./start.sh status`, `restart` und `stop` verwenden dieselbe Prozessverwaltung.
`./start.sh mini-services [start|status|restart|stop]` steuert ausschließlich den
Netzwerkworker. Unter Windows stehen dieselben Gruppenbefehle über `start.bat`
zur Verfügung. Weitere Startskripte sind nicht erforderlich; `start2.sh` bleibt
nur als Kompatibilitätsweiterleitung bestehen.

SIP startet automatisch auf einer privaten lokalen IPv4-Adresse, sonst Loopback.
DHCP, DNS, TFTP und Routing benötigen eine bewusste Aktivierung. Ein automatisch
gestarteter konkurrierender DHCP-Server oder geänderte Host-Routingregeln wären
keine sinnvollen Standardwerte. Audio-Autostart und Hardwarebedingungen sind in
[Audio-Streamer](AUDIO_STREAMER.md) und [Audio-Ausgabe](AUDIO_OUTPUT.md) beschrieben.

Die Oberfläche **Administration → Mini Services** zeigt alle registrierten Dienste
mit Status, Health, Fehler und Aktionen. Eine laufende Aktion erhält unmittelbare
Rückmeldung. Die Unterseiten ergänzen Fachkonfiguration, Geräte und Diagnose.

## Gemeinsame Konfiguration

`SIMPLEOFFICE_MINI_SERVICES_CONFIG` bestimmt die Konfigurationsdatei, normalerweise
`instance/mini-services.json`. Der bestehende Legacy-Pfad eine Verzeichnisebene
darüber bleibt nutzbar, wenn am neuen Standardpfad noch keine Datei liegt.
Webserver und Worker müssen dieselbe Datei und dasselbe State-Verzeichnis verwenden.
`--config DATEI` überschreibt den Pfad für einen separat gestarteten Worker.

Der Unterordner `mini-services/` enthält Status, begrenzte Logs und die lokale
Steuerdatenbank. `enabled`/`autostart` der gemeinsamen Worker-Steuerung sind von
der fachlichen Aktivierung getrennt: Start erfordert beide Aktivierungen, Stop
gilt für die aktuelle Worker-Sitzung. Beim nächsten Start entscheidet Autostart.
HTTP/PXE hat als Bestandteil des Webservers stattdessen einen persistenten
Auslieferungsschalter. Es gibt keinen zweiten HTTP-Prozess.

`SIMPLEOFFICE_MINI_SERVICES_AUTOSTART=0` unterbindet den vom Launcher gestarteten
Netzwerkworker, etwa wenn systemd ihn bereits verwaltet. Die mitgelieferte
systemd-Konfiguration trennt Web- und Netzwerkrechte. `SIMPLEOFFICE_MINI_SERVICES_PYTHON`
kann den Python-Interpreter des Shell-Steuerpfads wählen. Keine Rechteerhöhung
erfolgt automatisch durch eine Startaktion der Oberfläche.

## Status, Fehler und Recovery

Gemeinsame Zustände: `unavailable`, `stopped`, `starting`, `running`, `degraded`,
`stopping`, `failed`; ergänzend `waiting` und `disabled`. Text und Symbol erläutern
die Zustände, Farbe ist nicht die einzige Information. Diagnose kann aufgeklappt
werden. Die App-Version ist von Datenformat-/Schemaschlüsseln zu unterscheiden.

Der Worker schreibt regelmäßig einen Heartbeat. Nach 30 Sekunden ohne frischen
Status gilt er als nicht erreichbar. Einzelne Startfehler beenden keine anderen
Dienste. Wiederholungen erfolgen nach 2/4/8/16/32 Sekunden; anschließend sind
Konfigurationskorrektur oder expliziter Neustart erforderlich. Verlorene bekannte
IPv4-Bindings führen zu `waiting`; alle 30 Sekunden wird geprüft, ob die Adresse
zurück ist. Manuell gestoppte Dienste werden dabei nicht neu gestartet.

Normale Oberflächen zeigen eine verständliche Meldung und Handlung. Technische
Diagnose nennt Fehlertyp/errno; strukturierte Laufzeitereignisse enthalten Dienst,
Ereignis, Schweregrad, Zeitpunkt und gegebenenfalls Operation-ID. Request-Payloads,
Passwörter und Tokens gehören nicht in diese Logs. DNS-Anfragelogs sind fachliche
Diagnosedaten und enthalten abgefragte Namen; sie können deaktiviert und geleert werden.

## Gemeinsame API

Alle folgenden Endpunkte erfordern vorhandene Webanmeldung und Adminrechte;
schreibende Browserzugriffe zusätzlich CSRF. Antworten sind nicht cachebar.

| Methode / Pfad | Bedeutung |
|---|---|
| `GET /api/mini-services` | Katalog, Status und Fähigkeiten aller registrierten Dienste |
| `GET /api/mini-services/<id>` | Einzelstatus und letzter Scan |
| `POST /api/mini-services/<id>/start` | Start an den bestehenden Eigentümer delegieren |
| `POST /api/mini-services/<id>/stop` | Stop an den bestehenden Eigentümer delegieren |
| `POST /api/mini-services/<id>/restart` | Kontrollierter Neustart / Reload |
| `POST /api/mini-services/<id>/scan` | Fachlich passende Erkennung / Aktualisierung |
| `POST /api/mini-services/<id>/settings` | Unterstützte Einstellungen; Worker erwartet boolesche `enabled` und `autostart` |
| `GET /api/mini-services/operations/<id>` | Ergebnis einer vorgemerkten Worker-Aktion |

Worker-Aktionen liefern `202` und eine Operation-ID. Dies bedeutet **vorgemerkt**;
die Oberfläche wartet auf das tatsächliche Ergebnis. Das lokale SQLite-Postfach
ist auf 32 offene Befehle begrenzt. Nicht abgeholte Befehle verfallen nach 60 Sekunden.
Web-eigene Dienste antworten direkt. `capabilities` ist maßgeblich; HTTP/PXE nutzt
für Fachkonfiguration seinen geschützten Editor und bietet keinen künstlichen
Worker-Autostart-Schalter an.

## Service-Beziehungen

| Dienst | Requires | Optional | Provides |
|---|---|---|---|
| DHCP | Mini-Services-Worker, passendes IPv4-Netz | HTTP/PXE, TFTP für Bootoptionen | DHCP-Leases und Clientoptionen |
| DNS | Mini-Services-Worker, konkrete Bind-Adresse | Upstream für nicht lokale/cachebare Namen | DNS UDP/TCP |
| TFTP | Mini-Services-Worker, lokale Bootdateien | DHCP/PXE-Ankündigung | lesende TFTP-Transfers |
| Routing/NAT | Mini-Services-Worker, OS-Werkzeuge/-Rechte | DHCP-Netz, WAN-Schnittstelle | lokale Forwarding-/NAT-Regeln |
| SIP | Mini-Services-Worker, Telefoniedatenbank | private LAN-Adresse, SIP-Endgeräte | Registrar und lokale Redirects |
| HTTP/PXE | Webserver, Bootprofil/-dateien | DHCP, TFTP, Föderations-Peers | Bootskript und HTTP-Dateien |

## Plattformen und technische Grenzen

Linux- und Windows-Interfaces verwenden dieselbe Discovery-Quelle. Fehlende
Systemwerkzeuge gelten als unbekannter Hardwarestatus; sie werden nicht installiert.
IPv6-Bindings des DNS-Servers werden durch den derzeitigen IPv4-Inventarvergleich
nicht auf Netzwerkwechsel geprüft. Der Socket-/Listener-Healthcheck bleibt aktiv.
Android WebView kann die Oberfläche bedienen; privilegierte Netzwerkserver auf
Android sind kein Standardbetrieb.

Für reale LAN-/Firewall-/Audiohardware, Windows und Android ist zusätzliche
Abnahme erforderlich. Die [Qualitätsmatrix](MINI_SERVICES_REVIEW.md) dokumentiert
den Fortschritt und offene Punkte. [Deployment](DEPLOYMENT.md) beschreibt die
vorhandenen systemd-/Container-Pfade.

## Handbücher

- [DHCP](MINI_DHCP.md)
- [DNS](MINI_DNS.md)
- [Routing / NAT](MINI_GATEWAY.md)
- [SIP / Telefonie](TELEPHONY.md)
- [HTTP/PXE und TFTP](NETWORK_BOOT.md)
- [Audio-Ausgabe / Durchsagen](AUDIO_OUTPUT.md)
- [Audio-Sender und Receiver](AUDIO_STREAMER.md)

### Gateway-Recovery nach Healthcheck

Der bestehende Healthcheck läuft alle 15 Sekunden. Bestätigt er fehlende eigene
Gateway-Tabellen oder deaktiviertes IPv4-Forwarding, wechselt der Dienst nach
`failed` und verwendet den gemeinsamen Retry mit Backoff (2/4/8/16/32 Sekunden,
zusätzlich zum Worker-Takt). Nach fünf Wiederanläufen führt ein weiterer Fehler
zu `failed` ohne automatischen Retry. Expliziter Neustart oder eine geänderte
Konfiguration setzt das Budget zurück; Stop verwirft den anstehenden Retry.

Ein unlesbarer Status (`health.ok: null`, beispielsweise fehlende Leserechte)
führt nur zu `degraded`, ohne aufgrund dieser Diagnose Regeln zu ändern. Nach
einem Wiederanlauf wird der Healthcheck erneut ausgeführt. Wiederherstellung
verwendet ausschließlich die vorhandenen Start-/Stop- und Ownership-Pfade.
Keine automatische Rechteerhöhung oder Installation zusätzlicher Programme.

Grenzen: Der Linux-Check bestätigt Tabellenexistenz und IPv4-Forwarding, nicht
die Vollständigkeit einzelner Regeln oder tatsächlichen Pakettransport. Der
Worker-Neustart entfernt weiterhin eigene Regeln vor erneutem Anwenden; ein
plattformübergreifend atomarer Reload ist damit noch nicht umgesetzt.
