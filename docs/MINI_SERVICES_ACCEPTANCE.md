# Mini Services: Abnahmestand nach den Änderungen

Stand: 15.09.2026, aufbauend auf PR #296 und #298 einschließlich RTP-Discovery.
Die [Ausgangsmatrix](MINI_SERVICES_REVIEW.md) bleibt als Vergleich erhalten.
Diese erneute Bewertung ist **keine Gesamtabnahme**: offene Implementierungen
und ungeprüfte Plattformen sind ausdrücklich markiert. Tests eines Teilpakets
werden nicht als Nachweis für das gesamte Produkt gewertet.

## Gleiche Kriterien, erneute Bewertung

Legende wie in der Ausgangsmatrix: V = überprüfbare vorhandene Stärke im Code
und/oder den genannten Tests, T = Teilimplementierung, F = fehlt/Fehler,
? = nicht ausreichend geprüft, – = fachlich nicht anwendbar.
V bedeutet keine pauschale Hardware-, Plattform- oder Produktionsfreigabe.
HB = HTTP/PXE, AO = Audio-Ausgabe, AS/AR = Live-Audio Sender/Receiver.

| Eigenschaft | DHCP | DNS | TFTP | Gateway | SIP | HB | AO | AS | AR | Nachweis / verbleibende Grenze |
|---|---|---|---|---|---|---|---|---|---|---|
| Installation | T | T | T | T | T | V | T | T | T | Gemeinsamer Launcher; saubere Neuinstallation auf allen Plattformen offen |
| Start | V | V | V | V | V | V | V | V | V | Idempotenz, Ausfallisolation und Preflight getestet; Systemgrenzen teils gemockt |
| Stop | V | V | V | V | V | V | V | V | V | Socket-/Prozess-Cleanup und wiederholter Stop getestet |
| Restart | V | V | V | V | V | V | V | V | V | Vorhandener Eigentümer bleibt; keine zweite Startarchitektur |
| Status | V | V | V | T | V | V | T | T | V | Strukturierte Zustände; physische Audioausgabe und Gateway-Datenpfad nicht bestätigt |
| Autostart | V | V | V | V | V | V | V | V | V | Persistente Präferenzen; DHCP/Gateway und Mikrofonfreigabe bewusst aktivieren |
| Abhängigkeiten | V | V | V | T | V | V | T | T | T | Worker/Web-Eigentümer explizit; Systemwerkzeuge nicht vollständig im Status modelliert |
| Hardwareerkennung | – | – | – | V | V | – | T | T | T | Interfaces, Pulse, ALSA- und DirectShow-Mikrofone; Windows-Ausgänge fehlen |
| Netzwerkdiensterkennung | T | T | T | V | T | T | F | T | T | Bestehende LAN-Profile für aktive Desktop-RTP-Empfänger; fremde Player nicht entdeckt |
| Konfiguration | V | V | V | V | V | V | V | V | V | Bestehende JSON-/SQLite-Speicher; Audio jetzt persistent |
| Standardwerte | V | V | V | V | V | V | V | V | V | Sichere Bindings/Opt-in; DHCP-Netz nicht automatisch erraten |
| Validierung | V | V | V | V | V | V | V | V | V | Vor Start/Änderung; Audio-Portpaar und boolesche Schalter geprüft |
| UI | T | T | T | T | T | T | T | T | T | Gemeinsame Aktionen und geführte Bootprofile vorhanden; vollständige visuelle Prüfung fehlt |
| CLI | V | V | V | V | V | T | T | T | T | start.sh steuert Eigentümer; kein gleichwertiger Fach-CLI für alle Webdienste |
| API | V | V | V | V | V | V | V | V | V | Gemeinsame Admin-/CSRF-API plus bestehende Fachrouten |
| Fehlerbehandlung | V | V | V | T | V | V | V | V | V | Verständliche Antworten, erhaltene Formulare, isolierte Speicherfehler |
| Logging | T | T | T | T | T | T | T | T | T | Strukturierte gemeinsame Fehler; noch kein vollständiger Audit aller alten Logpfade |
| Diagnose | V | V | V | T | V | V | T | T | T | Status, Fehler, Scan und Handlungshinweise; Hardware-Diagnose bleibt begrenzt |
| Healthcheck | V | V | V | T | V | V | T | T | V | Socket/Thread, Bootprofile/Dateien, PCM-Empfang; kein End-to-End-Nachweis überall |
| Recovery | V | V | V | T | V | V | V | V | V | Backoff, Netzwerk-Rückkehr, Dateien und Prozessfehler; stille Treiber nicht vollständig erkannt |
| Berechtigungen | V | V | V | V | V | V | V | V | V | Admin/CSRF; privilegierter Netzwerkworker bleibt getrennt |
| Security | T | T | T | T | T | T | T | T | T | Gezielte Regressionstests; kein vollständiger Security-Audit behauptet |
| Dokumentation | V | V | V | V | V | V | V | V | V | Gleiche Betriebskapitel, Optionen und technische Grenzen dokumentiert |
| Inline-Hilfe | T | T | T | T | V | T | T | T | T | Erweiterte Optionen noch nicht überall gleich gut erklärt |
| Beispiele | V | V | V | V | V | V | V | V | V | Handbücher enthalten Betriebsabläufe und Konfigurationsbeispiele |
| Tests | T | T | T | T | T | T | T | T | T | Umfang erweitert; gesamte verlangte Fehlermatrix nicht pro Plattform nachgewiesen |
| Plattformangaben | V | V | V | V | V | V | V | V | V | Unterstützung und Grenzen ausdrücklich benannt |
| Linux | T | T | T | T | T | V | T | T | T | Loopback/Protokolltests; echte LAN-/Audio-/Gateway-Hardwareabnahme fehlt |
| Windows | ? | ? | ? | T | ? | ? | T | T | F | Gateway und DirectShow-Sender gemockt; nativer Windows-Receiver fehlt |
| Android | – | – | – | – | ? | T | T | T | T | Native Bridge beibehalten; SDK/Gradle und Geräteabnahme fehlen |
| Performance | T | T | T | ? | T | ? | ? | ? | ? | Lifecycle-Mikrobenchmark; kein Last-/Durchsatzvergleich aller Dienste |
| Ressourcenverbrauch | V | V | V | T | V | T | T | T | T | Begrenzte Tasks/Queues/Cache; kein vollständiges RAM-/CPU-Profil |
| Startzeit | T | T | T | ? | T | ? | ? | ? | ? | Messwerte unten; keine Kaltstartmessung des ganzen Systems |
| Offline | V | T | V | T | V | V | V | V | V | Lokale Funktionen; DNS-Upstreams/externe Bootziele benötigen ihr Netz |
| Netzwerkwechsel | V | V | V | T | V | T | T | T | T | IPv4-Binding-Recovery; IPv6 und stille Audiounterbrechung offen |
| Discovery | V | V | V | V | T | V | T | T | T | Scan, Zeit, Treffer und Fehler; kein universelles Geräteprotokoll |
| Service-Interaktion | V | V | V | V | V | V | V | V | V | Ausfallisolation und gemeinsamer Netzwerk-/Audio-Prüfstand |
| Monitoring | V | V | V | T | V | V | T | T | V | Frischer Heartbeat; reale PCM-Daten statt Prozessbehauptung |
| Versionsanzeige | V | V | V | V | V | V | V | V | V | Gemeinsame Projektversion im Status |
| Bedienbarkeit | T | T | T | T | T | T | T | T | T | Besseres Feedback; vollständige Bedienabnahme fehlt |
| Mobile/Touch/Fokus | ? | ? | ? | ? | ? | ? | ? | ? | ? | Codeanpassungen vorhanden, visuelle Abnahme blockiert |
| Parallelität/Atomizität | V | V | V | T | V | V | V | V | V | Locks, atomare Dateien und Queue-Claims; Gateway-Reload nicht insgesamt atomar |
| Datenschutz | T | T | T | T | T | T | T | T | T | Kein automatischer Mikrofonstart; vollständige Logprüfung noch offen |

## Reproduzierbare Messung

Aus dem Repository: `python -m tools.mini_services_benchmark --iterations 5`.
Nur Standardbibliothek und vorhandene Protokollkerne; temporärer Speicher,
Loopback und vom OS zugewiesene Ports. Keine DHCP-Pakete ins LAN, keine externen
DNS-Anfragen, keine Änderung von Routing oder Firewall. DNS-TCP und -UDP erhalten
hier getrennte freie Ports: gemessen wird der Lifecycle, nicht DNS-Verkehr.

Messumgebung: Python 3.12.14, Linux 6.18.44, x86_64, glibc 2.39.
Fünf Starts/Stops je Dienst auf derselben Instanz; Konstruktor und Python-Import
sind nicht in der Startzeit enthalten. CPU ist Prozess-CPU je Start/Stop-Zyklus.

| Dienst | Start Median / Max ms | Doppelstart Median ms | Stop Median / Max ms | CPU Median ms |
|---|---:|---:|---:|---:|
| DHCP | 0,235 / 0,251 | 0,003 | 1001,263 / 1001,287 | 0,338 |
| DNS | 0,355 / 0,605 | 0,003 | 1001,247 / 1001,301 | 0,577 |
| TFTP | 0,183 / 0,225 | 0,003 | 1001,227 / 1001,278 | 0,326 |
| SIP | 0,187 / 1,682 | 0,003 | 1001,202 / 1001,235 | 0,300 |

Keine zusätzlichen Threads nach Abschluss. Der Stop wartet auf den bestehenden
Socket-Timeout von einer Sekunde. Das ist eine begrenzte Wartezeit, kein hoher
CPU-Verbrauch. Diese Messung ersetzt weder Langzeittest noch RAM-, Last- oder
Hardwaremessung. Es wird kein plattformübergreifendes Leistungsversprechen abgeleitet.

## Abweichungen und konkrete Restarbeit

| Service | Eigenschaft | Abweichung | Technischer Grund | Nächste Verbesserung |
|---|---|---|---|---|
| AS/AR | Windows | DirectShow-Sender implementiert; reale Abnahme und nativer Receiver fehlen | Systemgrenzen nur gemockt, Receiver nutzt Pulse/ALSA-Infrastruktur | Windows-Hardwareabnahme; Receiver separat ergänzen |
| AS/AR | Android | Build und reale Hintergrund-/Geräteprüfung offen | Gradle/SDK hier nicht vorhanden; keine Installation freigegeben | In vorhandener Android-Buildumgebung bauen, anschließend Gerätetest |
| AO | Remote-Ausgabe | Definitionen ohne Transport sind nicht abspielbar | Register ist kein Audio-Transport | Echten unterstützten Transport anbinden; DLNA bleibt #285 |
| AS | Discovery | Fremde RTP-Player werden nicht erkannt | Sie veröffentlichen kein SimpleOffice-Profil | Nur tatsächlich verfügbare Protokolle ergänzen; manuelle Ziele bleiben |
| AR | ALSA-Ausgänge | Receiver benötigt PulseAudio/PipeWire-Pulse | Vorhandener PCM-Verteiler und virtuelle Mikrofone nutzen diesen Backend | Separaten ALSA-Ausgabepfad nur mit vollständigem Cleanup/Health ergänzen |
| Gateway | Health/Atomizität | Tabellen/NAT werden geprüft, nicht kompletter Datenpfad; Reload nicht durchgehend atomar | Bestehende Stop/Apply-Grenze und OS-Regelverwaltung | Regelinhalte prüfen, Reload-Transaktion und Pakettests ergänzen |
| DHCP/Gateway | Automatische Konfiguration | Kein eigenmächtig gewähltes neues DHCP-Netz | Fremde DHCP-Server und vorhandene Netzverwaltung dürfen nicht gestört werden | Konflikterkennung und geführte Auswahl ohne automatische Aktivierung |
| Netzwerkdienste | IPv6-Netzwechsel | Automatische Bindingprüfung bisher IPv4 | Gemeinsames Inventar liefert IPv4-Adressen | IPv6-Inventar und Linkverlusttests ergänzen |
| HTTP/TFTP | Bedienung | Geführter Profileditor umgesetzt; tatsächlicher Booterfolg ungeprüft | Bootdateien und Kernel-Parameter hängen vom Client ab | Reale PXE-Clients mit den angelegten Profilen prüfen |
| Alle | Mobile/Accessibility | Kein visueller Konformitätsnachweis | Browser blockiert lokale Testseite mit ERR_BLOCKED_BY_CLIENT | Desktop/Tablet/Smartphone/WebView samt Fokus/Kontrast prüfen |
| Alle | CLI/Logs/Tests | Noch nicht jeder relevante Aspekt gleichwertig | Historische Fachpfade und unvollständige Negativfallabdeckung | Verbleibende T/F/?-Zeilen gezielt abarbeiten |

Diese Punkte sind keine pauschalen Ausnahmen vom Auftrag. Fehlende Implementierung
bleibt offen; Umgebungsgrenzen werden getrennt davon benannt. Die PRs bleiben Draft,
bis die anwendbaren Kriterien erfüllt oder konkrete technische Abweichungen
vollständig bewertet sind. PR #288 (Bildschirm) wird durch diese Prüfung nicht
als fertiggestellt behandelt; #285 (DLNA) bleibt ein eigener Arbeitsbereich.

## Testnachweise

- 16.09.2026: Geführter Boot-Profileditor in den gemeinsamen Stand übernommen.
  61 Boot-/Security-/API-/Frontend-Tests bestanden, einschließlich Anlegen,
  Bearbeiten, Standardauswahl, Entfernen, Schreibfehler und HTML-Escaping.

- Gemeinsamer Netzwerk-/Audio-Prüfstand vor den jüngsten Audioergänzungen:
  191 Tests bestanden; kein erneuter vollständiger Gesamtlauf daraus abgeleitet.
- Nach Lautstärke-/Portkorrekturen: 76 relevante Tests bestanden.
- Nach RTP-Zielerkennung: 103 Audio-/Federation-/Discovery-/API-/Routentests
  einschließlich synthetischem Opus/RTP-Loopback bestanden.
- Frühere Gesamtsuite: 1.581 Tests bestanden, 10 übersprungen; älterer Stand.
- Relevante Tests: `test_mini_lifecycle`, `test_mini_network_recovery`,
  `test_gateway_lifecycle`, `test_network_boot_lifecycle`, `test_mini_security`,
  `test_mini_control_api`, `test_audio_lifecycle`, `test_audio_announcements`,
  `test_audio_target_discovery`, `test_audio_rtp_loopback`, `test_frontend_route_contract`.
- Keine neue Drittanbieterbibliothek oder Systemabhängigkeit installiert.

### Windows-Sender

DirectShow-Gerätesuche und Capture verwenden vorhandenes FFmpeg, ohne neue
Dependency. Tests prüfen Unicode/stabile Kennungen, doppelte Namen, ungültige
Eingänge, fehlendes Backend, Timeout, API-Rechte, Persistenz und gemeinsamen
Lifecycle samt Recovery. Eine echte Windows-Geräteabnahme ist weiterhin offen.
