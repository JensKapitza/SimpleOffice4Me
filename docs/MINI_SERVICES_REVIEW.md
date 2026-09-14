# Mini Services – Bestandsaufnahme und Abnahme

Ausgangsbasis: `4a9b798` (main). Inventur vom 14.09.2026. Dies ist eine
Arbeits- und Abnahmematrix, keine pauschale Produktionsfreigabe. Ein grüner
Build ersetzt weder Hardwaretests noch eine Prüfung der tatsächlich nutzbaren
Ende-zu-Ende-Funktion. Keine neue Drittanbieterabhängigkeit ist vorgesehen.

## Umsetzungspaket 1: Netzwerk-Laufzeit

Der vorhandene Worker bleibt Eigentümer der fünf Netzwerkdienste. Seine
Reload-/Fehlerpfade wurden gezielt vereinheitlicht: effektive Konfigurationen
werden vor dem Stop validiert, unveränderte Dienste bleiben unangetastet und
Startfehler sind je Dienst isoliert. Gemeinsame Lifecycle-Primitiven ersetzen
die drei praktisch gleichen UDP-Start-/Stop-Implementierungen, keine Protokolle.

- DHCP/TFTP/SIP: serialisierte, idempotente Starts; Socket-Aufräumen bei Fehler;
  kein UDP-Port-Sharing; Restart derselben Instanz möglich.
- DNS: partieller TCP/UDP-Start räumt alle Sockets auf; maximal 32 gleichzeitige
  Anfragen, TFTP maximal 16 Transfers; keine unbegrenzte Submission-Queue.
- Worker: strukturierte Einzelzustände, Health aus Socket **und** Listener,
  Startwiederholungen nach 2/4/8/16/32 Sekunden, danach expliziter Eingriff nötig.
  Konfigurationsänderungen setzen das Budget zurück. Fehleralter bleibt erhalten.
- Status-Heartbeat alle zwei Sekunden; nach 30 Sekunden keine Behauptung
  „läuft“ mehr. Gateway-Regelanwendung ist ausdrücklich kein Datenpfad-Healthcheck.
- Blocklist-Download blockiert nicht mehr den Heartbeat; Stop verhindert weitere
  Downloads und Veröffentlichung nach Abbruch. Laufender HTTP-Aufruf bleibt bis
  zum vorhandenen Socket-Timeout begrenzt; keine neue Dependency.
- Atomare private Dateien mit zufälligem exklusivem Temporärnamen. Standardpfad
  im Repository; existierende Alt-Konfiguration eine Ebene darüber wird weiter
  verwendet, solange am korrekten Pfad keine existiert. Migration muss das ganze
  zugehörige State-Verzeichnis erhalten oder explizit den Konfigurationspfad setzen.

Nachweis: `tests/test_mini_lifecycle.py` enthält echte unprivilegierte
Loopback-Start-/Stop-/Restart-/Portkonflikttests und isolierte Worker-Fehlertests.
Noch **keine** Gesamtabnahme: UI/API, persistente Audio-Konfiguration, vollständige
Discovery/Recovery und plattformübergreifende Hardwaretests folgen separat.

## Umsetzungspaket 2: Bedienung des Netzwerkworkers

Die registrierte Admin-API unter `/api/mini-services` stellt dieselben fünf
Worker-Dienste dar. Das lokale SQLite-Postfach enthält ausschließlich Dienst-ID,
allowlist-validierte Aktion, Operation-ID und Ergebnis; keine Shell-Befehle oder
frei übergebenen Argumente. Es ist auf 32 offene Befehle und begrenzte Historie
beschränkt. Befehle verfallen nach 60 Sekunden; unterbrochene Befehle werden
nach Worker-Neustart als fehlgeschlagen markiert, nicht heimlich wiederholt.
SQLite stammt aus der Standardbibliothek und ersetzt keinen Service-Manager.

- `GET /api/mini-services`, `GET /api/mini-services/<id>`: Status und Fähigkeiten.
- `POST /api/mini-services/<id>/{start,stop,restart}`: 202 plus Operation-ID;
  `GET /api/mini-services/operations/<id>` liefert den Abschluss.
- `POST /api/mini-services/<id>/settings`: exakt die booleschen Felder
  `enabled` und `autostart`; persistiert in `mini-services/control.sqlite3`.
  Die bisherigen Fachkonfigurationen bleiben maßgeblich für Ports und Protokolle.
- `POST /api/mini-services/<id>/scan`: Interface-Liste, lokale Bootdateien
  oder registrierte SIP-Telefone; Scope, Trefferzahl und Zeitpunkt werden gezeigt.
  Das ist keine Behauptung einer noch nicht implementierten DLNA-/SIP-Serversuche.
- Alle Endpunkte erfordern Anmeldung/Adminrolle, Mutationen den bestehenden
  CSRF-Token. Antworten sind `no-store`; Worker-Abwesenheit ergibt eine erklärende
  503-Antwort. Neue Netzwerkports entstehen nicht.

Die Oberfläche verwendet vorhandene Theme-Komponenten: responsive Karten,
44px-Bedienziele, sichtbaren Tastaturfokus, Text plus Symbol für Status,
`aria-live` für Aktionsfeedback und ausklappbare Diagnose. Polling ersetzt keine
fokussierten Eingaben. Die Gestaltung folgt den W3C-Erläuterungen zu
[Zielgröße](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
und [Statusmeldungen](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)
sowie Nielsens [Usability-Heuristiken](https://www.nngroup.com/articles/ten-usability-heuristics/)
(Systemstatus, Konsistenz, Fehlererholung, Wiedererkennen statt Erinnern).
Dies ist eine begründete Umsetzung, noch kein vollständiger WCAG-Konformitätsnachweis.

## Grenzen des Subsystems

Der dedizierte Worker besitzt DHCP, DNS, TFTP, Gateway und SIP. HTTP-Netzboot,
Audio-Ausgaben/Ansagen und Live-Audio laufen im Webprozess bzw. werden dort
gesteuert. Diese Grenzen bleiben erhalten; insbesondere bekommt Flask keine
zusätzlichen Netzwerkprivilegien. Konfiguration, Status und Bedienung werden
vereinheitlicht, nicht die Protokollimplementierungen ersetzt.

| Service | Zweck | Start | Status | Konfiguration | UI | Discovery | Tests | Doku | Probleme an der Ausgangsbasis |
|---|---|---|---|---|---|---|---|---|---|
| DHCP | IPv4-Leases, Reservierungen, PXE-Optionen | Worker, opt-in | Objekt vorhanden | mini-services.json/dhcp | network | Interface-Liste | test_mini_services | DEPLOYMENT | mehrfacher Start, Bind-Leak, Fehler beendet DNS/TFTP, statische Beispielnetze |
| DNS | UDP/TCP Resolver, Cache, Blocklisten | Worker, opt-in | Objekt vorhanden | mini-services.json/dns | network | Interface-Liste; Upstreams manuell | test_mini_services | DEPLOYMENT | unbegrenzte Request-Threads, Bind-Leaks, keine fachliche Healthprobe |
| TFTP | read-only Netzboot-Dateien | Worker, opt-in | Objekt vorhanden | mini-services/boot.json | Boot/Federation; kein vollständiger Editor | Dateiinventar | test_mini_services | DEPLOYMENT | unbegrenzte Transfers, Stop beendet Transfers nicht, Wiederanlauf fehlt |
| Gateway | Routing/NAT | Worker, opt-in | letztes Apply-Ergebnis | mini-services/gateway.json | Diagnose; kein vollständiger Editor | Netzwerkinterfaces | test_mini_services, test_network_ui_diagnostics | DEPLOYMENT | Konfigurationsreload schreibt Netzwerkregeln erneut, Health kein Istzustand |
| SIP | Registrar und lokale Redirects | Worker, automatisch | Socket, Registrierungen | telephony.sqlite, SIP_BIND | telephony | private LAN-Adresse | test_sip_runtime, test_telephony_* | TELEPHONY | fehlerhafter Socket kann als laufend gelten; keine automatische Startwiederholung |
| HTTP/PXE | Bootskript, Assets, Peer-Austausch | Flask, opt-in | HTTP-Endpunkte | boot.json, Federation-Rollen | network_boot_federation | lokale Assets/Peers | test_mini_services | DEPLOYMENT | gehört zum Webprozess, kein eigenständiger Prozessstatus |
| Audio-Ausgabe | Lautsprecherregister, Gruppen, Töne, TTS-Aufträge | HTTP-Aufträge | SQLite-Queue | audio-output.sqlite3; PIPER_MODEL | audio_output | manuelles Register | test_audio_output | nur Teilinformationen | lokale Wiedergabe nicht als vollständiger Queue-Worker integriert; Onlinewerte teils manuell |
| Live-Audio Sender | Mikrofon als Opus/RTP | HTTP-Aktion, ffmpeg | Prozess poll() | Request, nicht persistent | audio_streamer | Eingänge fehlen | test_audio_streamer | AUDIO_STREAMER | Start ersetzt laufende Session, keine Recovery, Linux-Backends |
| Live-Audio Receiver | Opus/RTP zu Lautsprechern/virtuellem Mikrofon | HTTP-Aktion, ffmpeg/paplay | Prozess poll() | Request, nicht persistent | audio_streamer | pactl sinks | test_audio_streamer | AUDIO_STREAMER | Ausgabe ohne Gerätesuche nicht klar, keine persistente Auswahl/Recovery |

## Zugehörige Quellen

| Bereich | Dateien/Patterns |
|---|---|
| Kern/Protokolle | simpleoffice_mini_core.py, simpleoffice_mini_runtime.py, simpleoffice_mini_services.py |
| Worker/Lifecycle | tools/mini_services.py, tools/launcher.py, tools/service_control.py |
| Netzboot | simpleoffice_network_boot.py, simpleoffice_network_boot_dhcp.py, app/network_boot*.py |
| Routing | simpleoffice_network_gateway.py, simpleoffice_network_gateway_runtime.py, app/network_gateway*.py, app/network_system_status.py |
| SIP | simpleoffice_sip_runtime.py, app/telephony_admin.py, app/telephony_profiles.py, app/telephony_numbering.py |
| Audio | app/audio_output_{admin,discovery,engine,store}.py, app/audio_streamer{,_admin}.py, app/audio_receiver_{config,store}.py, app/audio_calendar.py |
| Kompatibilität | app/mini_services.py und app/network_*.py Fassaden importieren die vorhandenen Flask-freien Kerne |
| Registrierung | app/runtime_inventory.py → mini_services_admin.bp; app/mini_services_admin.py → Audio/Boot/Telefonie-Blueprints |
| Templates | templates/admin/{mini_services_hub,mini_services,network_boot_federation,telephony,audio_output,audio_streamer}.html |
| JavaScript | static/js/audio_streamer.js, static/js/audio_output_scan.js; weitere Inline-Skripte in den Templates |
| Start/Deployment | start.sh, start.bat, start2.sh, restart.sh, stop.sh, packaging/simpleoffice-mini-services.service, packaging/postinst.sh, deploy/docker/{entrypoint.sh,compose.lan.yaml} |
| Android | android/apk/app/src/main/java/de/simpleoffice4me/android/{MainActivity,AndroidAudioStreamer}.java; embedded Python runtime; kein privilegierter Netzwerkworker |
| Dokumentation | docs/{DEPLOYMENT,TELEPHONY,AUDIO_STREAMER}.md, deploy/{install,vm}/README.md, android/apk/README.md |

### Einstellungen, APIs und Systemgrenzen

- Netzwerk: `SIMPLEOFFICE_MINI_SERVICES_CONFIG`; JSON-Blöcke DHCP/DNS
  werden vollständig durch `validate_config` validiert. Boot- und
  Gateway-Einstellungen besitzen eigene bestehende Validatoren.
- SIP: `SIMPLEOFFICE_SIP_BIND`, `telephony/telephony-profiles.sqlite3` neben der Konfiguration;
  Port, Realm, Registrar-/Proxy-/STUN-/TURN-Angaben über TelephonyProfileStore.
  Secret-Verifier, nicht Klartextpasswörter, gehen an den Registrar.
- TTS: `SIMPLEOFFICE_PIPER_MODEL`; Modelle werden nicht automatisch geladen.
- Federation: `SIMPLEOFFICE_FEDERATION_PEER_ID`; vorhandene Peer-Rollen und
  Authentifizierung bleiben maßgeblich.
- Starter: `PYTHON`, `SIMPLEOFFICE_MINI_SERVICES_PYTHON` (start2-Kompatibilität),
  `SIMPLEOFFICE_NATIVE_PACKAGES`; Web-/Indexer-Schalter bleiben getrennt.
- APIs: `/admin/mini-services` und `/network`, POST `/save`,
  `/blocklists/refresh`, `/dns-log/clear`, `/leases/clear`;
  `/admin/mini-services/audio` (Register/Gruppen/say/sound),
  `/admin/mini-services/audio/streamer` (status/outputs/sender/receiver),
  Telefonie- und Boot-Blueprints. Die genauen Routen sind im jeweiligen
  `@bp`-Decorator definiert; gemeinsame Ergänzungen ersetzen sie nicht.
- Netzwerkprotokolle: DHCP UDP 67, DNS UDP/TCP 53, TFTP UDP 69 plus
  Transferports, SIP UDP 5060, RTP gewöhnlich UDP 5004. Alle bestehenden
  konfigurierbaren Ports bleiben konfigurierbar. DHCP/NAT nie automatisch
  aktivieren oder einen vorhandenen DHCP-/DNS-/Firewall-Dienst abschalten.
- Abhängigkeiten: Protokollkern und Worker nur Python-Standardbibliothek;
  Flask nur HTTP/UI; vorhandene cryptography-Bibliothek für Secrets.
  Linux: ip, nft/sysctl nur Routing/Diagnose, ping optional DHCP;
  ffmpeg/Opus, pactl, paplay für Live-Audio; pw-play/aplay/ffplay alternativ
  für Dateien; Piper plus Modell nur TTS. Windows: vorhandene PowerShell-
  Netzverwaltung. Android: vorhandene MediaCodec-/AudioRecord-/AudioTrack-APIs.
  Keine dieser optionalen Komponenten wird bei dieser Prüfung installiert.

### Angrenzende Dienste und offene Arbeiten

- SFTP (`app/sftp_server.py`, `tools/sftp_setup.py`, start-sftp.sh),
  Preview (`app/preview_service.py`), PrinterShare, Federation Discovery,
  VM/Podman (`app/host_services.py`) und Index-/Datalogger-Worker sind
  angrenzende Subsysteme, nicht zusätzliche Instanzen des Netzwerkworkers.
  Ihre Aufrufstellen und gemeinsamen Lifecycle-Records müssen kompatibel bleiben.
- Issue #285: DLNA/UPnP Audio-/Video-Renderer ist offen, kein vorhandener
  DLNA-Worker auf main. Nicht als funktionierende Discovery ausweisen.
- Issue #286 / offener PR #288: Bildschirmfreigabe separat. Auf main nicht
  vorhanden; WebRTC-/Miracast-/Android-Laufzeitabnahme dort gesondert nötig.
  Insbesondere erfolgreiche Kompilierung allein belegt keine Freigabe im Hintergrund.
- PR #295 betrifft GitHub-Actions-Abhängigkeiten, nicht diesen Umbau.

## Qualitätsmatrix vor Änderungen

Legende: **T** = Teilimplementierung; **V** = vorhandene überprüfbare Stärke
im Quelltext, nicht pauschal hardwaregetestet; **F** = fehlt/Fehler gefunden;
**–** = fachlich nicht anwendbar; **?** = noch nicht ausreichend geprüft.
AS/AR sind Live-Audio Sender/Receiver, AO ist Audio-Ausgabe, HB HTTP/PXE.
Je Zeile gilt der beste passende Bestand als Untergrenze; gibt es keinen
ausreichenden Bestand, gilt der ausdrückliche Nutzerauftrag.

| Eigenschaft | DHCP | DNS | TFTP | Gateway | SIP | HB | AO | AS | AR | Mindeststandard / Beleg |
|---|---|---|---|---|---|---|---|---|---|---|
| Installation | T | T | T | T | T | V | T | T | T | Flask-freier Worker, optionale Werkzeuge nur bei Bedarf |
| Start | F | F | F | T | T | V | F | T | T | SIP-Fehlerisolation auf alle Worker-Dienste |
| Stop | T | T | F | T | T | V | F | V | V | Audio-Cleanup schließt Ressourcen |
| Restart | F | F | F | T | F | T | F | T | T | idempotent, keine zweite Instanz |
| Status | F | F | F | T | T | T | T | T | T | SIP-Details + Audio poll; strukturiert |
| Autostart | T | T | T | T | T | V | F | F | F | ein Launcher, sicherheitskritische Dienste opt-in |
| Abhängigkeiten | V | V | V | T | V | V | T | T | T | dependency-free Kern; explizite requires/provides |
| Hardwareerkennung | – | – | – | T | T | – | F | F | V | begrenzte pactl-Ausgabesuche mit Default |
| Netzwerkdiensterkennung | F | F | T | T | T | T | F | F | F | vorhandene Interfaces/Peer-Rollen, keine fremden Probes blind |
| Konfiguration | V | V | V | V | V | V | T | F | F | bestehende persistente Validatoren erhalten |
| Standardwerte | T | V | V | V | V | V | T | T | T | Loopback/opt-in; kein geratener DHCP-Pool aktivieren |
| Validierung | V | V | V | V | V | V | V | V | V | vor Stop/Änderung vollständig validieren |
| UI | T | T | T | T | V | T | T | T | T | eindeutige Zustände, gleiche Aktionen |
| CLI | T | T | T | T | T | T | F | F | F | start.sh mit status/stop/restart |
| API | T | T | T | T | T | V | V | V | V | bestehende Admin-/CSRF-Grenzen |
| Fehlerbehandlung | F | F | F | T | V | T | T | V | V | Audio-API ohne rohe Exceptions; SIP-Isolation |
| Logging | T | V | T | T | T | T | T | T | T | begrenzte DNS-Logs; Secret-freie strukturierte Events |
| Diagnose | T | T | T | V | V | T | F | T | T | Routing-Istzustand und konkrete nächste Aktion |
| Healthcheck | F | F | F | F | T | T | F | T | T | nicht Objektpräsenz, sondern Socket/Thread/Protokoll |
| Recovery | F | T | T | F | F | T | F | F | F | DNS-Upstreams + TFTP-Retries; Backoff und Stop |
| Berechtigungen | V | V | V | V | V | V | V | V | V | separater Worker mit gezielten Capabilities |
| Security | T | T | T | T | V | V | T | T | T | Auth/CSRF, LAN-Bind, Bounds, Dateirechte |
| Dokumentation | T | T | T | T | V | T | F | V | V | TELEPHONY/AUDIO_STREAMER plus gleiche Kapitel |
| Inline-Hilfe | T | T | T | T | V | T | T | T | T | Telefonie-Hilfe; komplexe Optionen erklären |
| Beispiele | T | T | T | T | V | T | T | V | V | lauffähige sichere Beispiele |
| Tests | T | T | T | T | V | T | T | V | V | Fehler/Cleanup/Protokolltests auf alle übertragen |
| Plattformangaben | T | T | T | V | T | V | T | V | V | ehrlich capability-basiert, nicht OS allein |
| Linux | T | T | T | T | T | V | T | T | T | ohne Root im normalen Webprozess |
| Windows | ? | ? | ? | T | ? | V | T | F | F | keine PulseAudio-Unterstützung vortäuschen |
| Android | – | – | – | – | ? | T | T | T | T | native Audio-Bridge; privilegierter Worker nicht starten |
| Performance | ? | F | F | ? | ? | ? | ? | ? | ? | bounded Requests/Transfers; messen statt raten |
| Ressourcenverbrauch | T | F | F | T | T | T | T | T | T | feste Grenzen und Cleanup |
| Startzeit | ? | ? | ? | ? | ? | ? | ? | ? | ? | getrennt messen; keine globale Blocklist-Wartezeit |
| Offline | V | T | V | T | V | V | V | V | V | lokale Funktionen ohne Internet; Fehler transparent |
| Netzwerkwechsel | F | F | F | F | F | T | F | F | F | verlorene Bindings erkennen; begrenzter Wiederanlauf |
| Discovery | T | T | T | V | T | V | F | F | V | Scanzeit/Anzahl/Fehler + persistente Auswahl |
| Service-Interaktion | F | F | F | T | V | T | T | T | T | unabhängige Ausfälle, explizite Abhängigkeiten |
| Monitoring | F | F | F | T | T | T | T | T | T | frischer Heartbeat; stale nicht als running |
| Versionsanzeige | F | F | F | F | F | T | F | F | F | gemeinsame App-Version; Schema separat |
| Bedienbarkeit | T | T | T | T | V | T | T | T | T | Status, Hilfe, Recovery sichtbar |
| Mobile/Touch/Fokus | ? | ? | ? | ? | ? | ? | ? | ? | ? | WCAG 2.2; Zielgröße, Kontrast, Tastatur prüfen |
| Parallelität/Atomizität | F | F | F | T | T | T | V | T | T | SQLite-Transaktionen + private atomare Dateien |
| Datenschutz | T | T | T | T | V | T | T | T | T | keine Secrets in Logs/API; Diagnose separat |

## Abnahmefortschritt

Noch keine Gesamtabnahme. Die Ausgangsmatrix bleibt unverändert als Vergleich
erhalten. Abgeschlossene Pakete werden mit Tests und verbleibenden Abweichungen
unten ergänzt. Ungeprüfte Plattform-/Hardwarepfade bleiben ausdrücklich offen.

### Security-Nachprüfung: Netzwerkboot, Blocklisten und SIP

- Öffentliche HTTP-Bootdateien und iPXE-Skripte sind nur bei explizit aktiviertem
  Netzwerkboot erreichbar. Defekte Einstellungen sperren die Auslieferung.
  Authentifizierter Föderationsspeicher bleibt unabhängig nutzbar.
- DHCP/DNS-, Boot- und Gateway-Schalter akzeptieren ausschließlich JSON-Booleans.
  Der String `"false"` darf niemals einen Netzwerkdienst aktivieren.
- HTTPS-Blocklisten prüfen jeden Redirect vor dem nächsten Request; HTTP-Downgrade
  und URLs mit Zugangsdaten werden abgewiesen. Administrativ konfigurierte interne
  HTTPS-Feeds bleiben erlaubt; dies ist keine allgemeine öffentliche URL-Fetch-API.
- SIP hält höchstens 1.024 Challenges. Verdrängte Challenges verlieren auch ihre
  Replay-Zähler. Replay-Prüfung und Aktualisierung sind gemeinsam gesperrt.
- Boot-URLs akzeptieren keine Zeilenumbrüche oder Zugangsdaten; Profilbeschriftungen
  können keine zusätzliche iPXE-Skriptzeile erzeugen. Fehler beim Öffnen einer
  atomaren Konfigurationsdatei schließen auch den ursprünglichen Dateideskriptor.

Nachweis: `tests.test_mini_security`, `tests.test_mini_services` und
`tests.test_sip_runtime`: 36 Tests erfolgreich. Vor diesem Korrekturpaket lief die
Gesamtsuite mit 1.581 Tests erfolgreich (10 übersprungen). Dies ersetzt weder
Hardwaretests noch die noch offene Gesamt-Abnahmematrix.

### Routing-Istzustand und Stop

Linux ersetzt bzw. entfernt ausschließlich die beiden eigenen nftables-Tabellen
in einer atomaren Transaktion. Fehler beim Lesen oder Ändern gelten nicht mehr als
erfolgreicher Stop. Windows entfernt ausschließlich den konfigurierten NAT-Namen;
fehlende Werkzeuge oder Berechtigungen werden als Fehler behandelt. Ein fehlgeschlagener
Stop behält die Ressourcen-Zuordnung im Worker für einen erneuten Versuch.

Alle 15 Sekunden prüft der laufende Gateway-Dienst eigene Tabellen/NAT und
IPv4-Forwarding. Fehlende Regeln liefern `degraded`, nicht einen vermeintlich
funktionierenden Datenpfad. Unlesbarer Status bleibt unbekannt. Globale
Forwarding-Einstellungen werden beim Stop nicht abgeschaltet, da andere Dienste
sie nutzen können. Internet-Erreichbarkeit und Paketdurchsatz sind kein Bestandteil
dieses lokalen Healthchecks. Nachweis: 39 Tests im Gateway-/Lifecycle-/Security-/API-Paket.

### HTTP/PXE, Dateien und Bedienung

HTTP/PXE ist in der gemeinsamen API und Übersicht registriert. Der bestehende
Webserver bleibt Eigentümer. Start/Stop speichern den vorhandenen Boot-Schalter;
Neustart lädt Einstellungen neu, ohne andere Webfunktionen zu unterbrechen.
Fehlende Profile oder Dateien liefern `waiting` bzw. `degraded`; das Wiedererscheinen
einer Datei wird beim nächsten Statusabruf erkannt. Keine eigenen erfundenen
HTTP-Prozesslaufzeiten oder Autostart-Schalter.

Dateiscans verwenden Metadaten, maximal 512 Treffer und keine Image-Prüfsummen.
Föderation behält vollständige Hashprüfung. Parallele Uploads verwenden private,
zufällige temporäre Namen und atomaren Ersatz. Scanfehler werden gespeichert.
Der geschützte erweiterte Boot-Editor kann alle vorhandenen Optionen bearbeiten
und auf deaktivierte Standardwerte zurücksetzen. Ungültige Eingaben bleiben erhalten.
Widersprüchliche alte „bereit“-Statusanzeigen wurden aus der Übersicht entfernt.

Dokumentation: [Netzwerkboot](NETWORK_BOOT.md). Nachweis: 47 Tests im
HTTP/PXE-/Security-/Netzwerk-/API-Paket. Der geführte Profileditor und reale
PXE-/Windows-/Android-Gerätetests sind weiterhin offen.

### Gemeinsame Netzwerk-Discovery und Wiederanlauf

Oberfläche und Worker verwenden die bestehende plattformübergreifende
Interface-Erkennung des Routing-Kerns. Linux- und Windows-Aufrufe sind jeweils
auf drei Sekunden begrenzt; Windows liefert jetzt auch Präfixlänge und Linkstatus.
Nicht lesbare OS-Ausgaben gelten als unbekannt, niemals als Beweis für entfernte
Hardware. Die Standardbibliothek liefert weiterhin Interface-Namen als UI-Fallback.

Der Worker prüft beim Start und alle 30 Sekunden neu. Verschwindet eine bekannte
konfigurierte IPv4-Adresse/Schnittstelle, beendet er den betroffenen Listener und
meldet `waiting`. Bei Rückkehr startet genau ein Listener; ein manueller Stop
bleibt erhalten. Effektive SIP- und automatische Gateway-Bindings werden erneut
ausgewertet. Unveränderte Dienste werden nicht neu gestartet. IPv6-Linkverlust
wird vom derzeitigen IPv4-Inventar nicht behauptet und bleibt eine dokumentierte
Grenze. DHCP-Netz und Pool werden bei einem Netzwerkwechsel nicht eigenmächtig
auf ein anderes Segment umgestellt.

Nachweis: 40 Tests im Netzwerk-Recovery-/UI-/Lifecycle-/Gateway-/API-Paket.

### Fachkonfiguration und Betriebshandbücher

Die bestehende Netzwerkseite enthält nun einen Gateway-Editor mit validierten
Adressen, Interface-Auswahl, Autodetection, Betriebsart, Regeln und Reset. Ohne
aktiven DHCP-Dienst respektiert Gateway sein eigenes internes Netz. DHCP und DNS
können separat auf deaktivierte Standardwerte zurückgesetzt werden. Mutationen
benötigen Admin/CSRF; fehlerhafte Gateway-Eingaben bleiben zur Korrektur sichtbar.

[Gemeinsamer Betrieb](MINI_SERVICES.md), [DHCP](MINI_DHCP.md), [DNS](MINI_DNS.md),
[Gateway](MINI_GATEWAY.md), [SIP](TELEPHONY.md) und [Netzwerkboot](NETWORK_BOOT.md)
verwenden dieselben Kapitel für Zweck, Voraussetzungen, Standardbetrieb,
Konfiguration, Discovery, Ports, Security, Diagnose, API, Plattformen und Grenzen.
Nachweis für die Konfigurationsänderungen: 31 Netzwerk-/Recovery-/UI-/API-Tests
einschließlich der vorhandenen Frontend-Routenprüfung.
