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

### Suchstatus und Diagnose im Hub

Die Servicekarten zeigen „Suche läuft“, „Suche fehlgeschlagen“, „Keine Treffer“
oder die Trefferzahl mit Suchbereich und Zeitpunkt. Ein Scan-Fehler ist kein
Nachweis, dass keine Geräte vorhanden sind. Fehlerhinweis und nächste mögliche
Aktion stehen auf der Karte. Nach fehlgeschlagenen Aktionen wird der Status
aktualisiert. Unter „Einstellungen und Diagnose“ stehen außerdem Dienstversion,
Eigentümer sowie die gemeldeten requires/optional_requires/provides-Beziehungen.

### Audio-Programmprüfung

Die gemeinsame Status-API liefert für Audio-Dienste `dependencies`: Programm bzw.
Alternativen, `required`, `available`, Zweck und `scope: executable-only`.
Die Übersicht nennt fehlende Pflichtprogramme; alle bedingten Funktionen stehen
unter „Einstellungen und Diagnose“. Die Prüfung sucht nur im PATH und startet
oder installiert nichts. Hardware, Codecs, Audioberechtigungen und laufende
PulseAudio-/PipeWire-Dienste sind dadurch nicht bestätigt. Laufzeit-Health und
Status bleiben eigenständige Informationen.

Sender benötigen FFmpeg; pactl dient bei Pulse nur der Gerätesuche. Receiver
benötigen FFmpeg und für Lautsprecher paplay (Linux) bzw. FFplay (Windows).
Das virtuelle Mikrofon unter Linux benötigt sowohl paplay als auch pactl, auch
ohne ausgewählte Lautsprecher. Beide werden vor dem Ersetzen einer laufenden
Receiver-Session geprüft. Unter Windows bleibt das virtuelle Mikrofon unsupported.
Beim Ansage-Worker hängen Programme von der Funktion ab: pactl für Discovery,
paplay für erkannte Ausgänge, pw-play/aplay/ffplay als Alternativen für manuelle
lokale Ausgänge, Piper plus vorhandenes Modell nur für Sprachansagen. Fehlende
optionale Programme verhindern daher nicht pauschal den Worker-Start.

### Gateway-Recovery nach Healthcheck

Der bestehende Healthcheck läuft alle 15 Sekunden. Bestätigt er fehlende eigene
Gateway-Tabellen, abweichende Chain-Struktur/Regelanzahl oder deaktiviertes IPv4-Forwarding, wechselt der Dienst nach
`failed` und verwendet den gemeinsamen Retry mit Backoff (2/4/8/16/32 Sekunden,
zusätzlich zum Worker-Takt). Nach fünf Wiederanläufen führt ein weiterer Fehler
zu `failed` ohne automatischen Retry. Expliziter Neustart oder eine geänderte
Konfiguration setzt das Budget zurück; Stop verwirft den anstehenden Retry.

Ein unlesbarer Status (`health.ok: null`, beispielsweise fehlende Leserechte)
führt nur zu `degraded`, ohne aufgrund dieser Diagnose Regeln zu ändern. Nach
einem Wiederanlauf wird der Healthcheck erneut ausgeführt. Wiederherstellung
verwendet ausschließlich die vorhandenen Start-/Stop- und Ownership-Pfade.
Keine automatische Rechteerhöhung oder Installation zusätzlicher Programme.

Der Linux-Check prüft Tabellen, Chain-Struktur, Regelanzahl, die von SimpleOffice
erzeugten Regelausdrücke und IPv4-Forwarding. Grenzen bleiben tatsächlicher
Pakettransport sowie die Abnahme gegen reale unterstützte nft-Versionen. Der
Reload aktiver Linux-Gateways verwendet nun die vorhandene nft-Transaktion ohne
vorherigen Stop. Windows erhält bei unverändertem NAT-Namen/Netz ebenfalls das
bestehende Objekt. Windows-Änderungen von Name/Netz/Modus und die Recovery nach
bestätigtem Healthfehler verwenden weiterhin Stop/Start; ein plattformübergreifend
atomarer Reload ist nicht umgesetzt.

### Linux-IPv6 bei Netzwerkwechsel

Die vorhandene `ip -j address show`-Abfrage erfasst IPv4 und IPv6. Adressen in
DAD-Prüfung (`tentative`) oder mit fehlgeschlagener DAD (`dadfailed`) gelten noch
nicht als nutzbar. Der Snapshot gibt die geprüften Familien explizit als
`address_families` an. Die Bindingprüfung normalisiert IP-Schreibweisen und
berücksichtigt bei IPv6 eine angegebene Zone als Interface-Name oder Index.

Geht eine konfigurierte IPv6-Adresse verloren, verwendet der Worker denselben
waiting-/Stop-/Wiederanlaufpfad wie für IPv4. Eine zurückkehrende Adresse startet
nur weiterhin angeforderte Dienste neu. Manuelles Stop und unbekanntes Inventar
behalten ihr bisheriges Verhalten. Diese Prüfung erweitert weder den IPv4-only
Gateway/DHCP-Backend noch automatisch die Socket-Fähigkeiten anderer Dienste.
Ältere Snapshots bestätigen weiterhin nur IPv4; fehlende IPv6-Daten werden dort
nicht als Adressverlust ausgelegt. Reale IPv6-LAN- und Windows-Abnahme bleiben offen. Keine neue Dependency und kein zusätzlicher Netzwerkdienst.

### Windows-IPv6-Inventar

Windows verwendet nun `Get-NetIPConfiguration -All` zusammen mit
`Get-NetIPAddress`, ausschließlich lesend im bestehenden PowerShell-Aufruf mit
drei Sekunden Timeout. Damit werden auch virtuelle und getrennte Interfaces
sowie IPv6 erfasst. IPv4-Standardrouten bleiben Grundlage der IPv4-Gateway-Auswahl.

Preferred- und Deprecated-Adressen bleiben im Binding-Inventar; Invalid,
Tentative und Duplicate werden ausgeschlossen. Deprecated bedeutet dabei nicht,
dass die Adresse für neue Verbindungen bevorzugt wird. IPv6-Zonen verwenden die
bestehende Prüfung nach Interface-Name oder Index. Leere, erfolgreich gelesene
Adresslisten können Verlust bestätigen; fehlerhafte Daten, unbekannte Zustände
oder Adressen ohne zugeordnetes Interface markieren den gesamten Scan als
unbekannt und stoppen keine laufenden Dienste. Legacy-Snapshots bleiben IPv4-only.

Tests decken Scope, Verlust, DAD-Status, getrennte Interfaces und fehlerhafte
Antworten ohne Windows-Hardware ab. Reale Windows-/PowerShell-Abnahme bleibt offen.
Referenzen: [Get-NetIPConfiguration](https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netipconfiguration)
und [Get-NetIPAddress](https://learn.microsoft.com/en-us/powershell/module/nettcpip/get-netipaddress).

### Konfiguration direkt aus der Dienstkarte

Jede Karte der gemeinsamen Übersicht verlinkt ihre vorhandene Fachseite über
„Konfiguration öffnen“. URLs werden serverseitig mit `url_for` erzeugt, damit
Installationen unter einem URL-Präfix funktionieren. DHCP/DNS/Gateway verwenden
die Netzwerkeinstellungen, SIP die Telefonie, TFTP/HTTP-Boot die Bootverwaltung
und Audio die vorhandenen Audioseiten. Die jeweiligen Admin-Prüfungen bleiben
bestehen. Es entsteht kein zweiter Konfigurationsspeicher.

Aktiviert/Autostart besitzen direkt zugeordnete Inline-Hilfe. Konfigurationslinks
und Diagnose-Summary erhalten sichtbaren Tastaturfokus und mindestens 44 Pixel
hohe Interaktionsflächen. Dies ersetzt keine visuelle WCAG-/Mobilabnahme.

### Fehlerhafte Linux-Inventardaten

Fehlende Adresslisten, ungültige Präfixe, widersprüchliche Adressfamilien und
ungültige Flag-Objekte führen zu `available: false`. Laufende Dienste werden
aufgrund dieser unvollständigen Information nicht gestoppt. Leere gültige Listen
bleiben dagegen ein bestätigter Verlust. Das verwendet dieselbe Unterscheidung
zwischen unbekanntem Scan und fehlender Hardware wie das Windows-Inventar.

## Einzelsteuerung über CLI

```sh
./start.sh mini-services status --service dns
./start.sh mini-services restart --service dns --wait 5
./start.sh mini-services stop --service sip
./start.sh mini-services status --service dns --operation AKTIONS_ID
```

Unter Windows dieselben Argumente mit `start.bat`; alternativ plattformübergreifend
`python -m tools.mini_services`. `--config DATEI` wählt wie bisher die Instanz.
Netzwerk-Einzeldienste: dhcp, dns, tftp, sip, gateway. Ohne `--service` bleibt
die bisherige Worker-Gruppensteuerung erhalten. Audio und HTTP-Boot verwenden
über dieselbe CLI die bestehende Admin-API des laufenden Webprozesses (siehe unten).

Einzelaktionen verwenden dieselbe private SQLite-Mailbox wie die Admin-API.
Kein zweiter Worker, keine Installation, keine Rechteerhöhung. Ausführung bleibt
beim vorhandenen Worker samt Validierung und Aktivierungseinstellungen. Lokaler
Zugriff auf die Instanzdateien ist erforderlich; Dateirechte nicht lockern.
Die Ausgabe ist JSON. Exitcode 0: abgeschlossene erfolgreiche Aktion bzw.
laufender/eingeschränkter Dienst; 1: Fehler; 2: ungültige CLI-Argumente;
3: noch offene Aktion oder nicht laufender/nicht erreichbarer Dienst.
`--wait` begrenzt das Polling auf 0–60 Sekunden (Standard 5); Datenbankoperationen
können zusätzlich bis zum bestehenden SQLite-Timeout warten. Ein Warteende ist
kein Abbruch: Aktions-ID aufbewahren und Status prüfen. Wiederholte identische
noch offene Aktionen werden wie in der API dedupliziert. Ein fehlender oder
veralteter Worker-Heartbeat verhindert das Einreihen neuer Aktionen.

### Audio, HTTP-Boot und Discovery über den laufenden Webprozess

```sh
./start.sh mini-services status --service audio-output --username admin
./start.sh mini-services restart --service audio-receiver --username admin
./start.sh mini-services scan --service audio-sender --username admin
./start.sh mini-services scan --service dns --username admin
./start.sh mini-services stop --service http-boot --username admin
./start.sh mini-services status --service http-boot --username admin --web-url 'http://[::1]:8080'
```

Unterstützt: `audio-sender`, `audio-receiver`, `audio-output`, `http-boot` mit
`start`, `stop`, `restart`, `status`, `scan`. Zusätzlich verwenden Netzwerkdienste
`scan --service DIENST --username admin` über die vorhandene Discovery-API.
Der Webprozess muss für diese Aktionen bereits laufen.
Die CLI importiert keine Flask-App und startet keinen zweiten Audio-Worker.
Gespeicherte Geräteeinstellungen und vorhandene API-Validierung bleiben wirksam.
Ein fehlendes Bootprofil liefert beispielsweise `waiting` und Exitcode 3.

Standardadresse ist `http://127.0.0.1:8080`; bei abweichendem Port oder Server
`--web-url` verwenden. HTTP ist ausschließlich für explizite Loopback-IP-Adressen
(IPv4 oder IPv6) zulässig, sonst ist HTTPS mit gültigem Zertifikat erforderlich.
Nur Basisadressen ohne Unterpfad, Query oder eingebettete Zugangsdaten werden
akzeptiert. Weiterleitungen und Umgebungs-Proxys werden nicht verwendet.
`--config` und `--operation` gelten ausschließlich für Netzwerk-Worker.

Die Anmeldung verwendet ein bestehendes SimpleOffice-Administratorkonto mit
Passwort, Sitzungscookies im Arbeitsspeicher und den regulären CSRF-Schutz.
Kontosperren und Login-Drosselung gelten unverändert. Das Passwort wird verdeckt
abgefragt. Für Automatisierung erlaubt `--password-stdin` eine einzelne Zeile aus
einem vorhandenen Secret-Manager; keine Passwörter in Befehle oder Shell-Historien
schreiben. Cookies und Passwort werden nicht auf Datenträger gespeichert.
Reine OAuth-Konten benötigen für diese CLI ein eingerichtetes lokales Passwort.

`--web-timeout 10` begrenzt jede HTTP-Anfrage auf 1–60 Sekunden (Standard 10).
Es gibt keine automatische Wiederholung von Aktionen. Nach Timeout kann eine
Aktion bereits ausgeführt sein: zuerst `status` prüfen. `--wait` ist nur für das
Polling der Netzwerk-Mailbox relevant. Start mit `starting`/`waiting`, deaktivierte
Dienste und nicht laufender Status ergeben Exitcode 3, fehlgeschlagene Aktionen
oder Verbindungen Exitcode 1; bestätigter Stop/Scan sowie laufender/eingeschränkter
Dienst Exitcode 0. Diagnose und Einstellungen bleiben in der gemeinsamen UI.
