# Mini Services: Abnahmestand nach den Änderungen

Stand: 24.09.2026. Die historische Matrix basiert auf #296/#298 und wurde gegen den aktuellen main-Stand sowie die inzwischen gemergten Folge-PRs abgeglichen.
Die [Ausgangsmatrix](MINI_SERVICES_REVIEW.md) bleibt als Vergleich erhalten.
Diese erneute Bewertung ist **keine Gesamtabnahme**: offene Implementierungen
und ungeprüfte Plattformen sind ausdrücklich markiert. Tests eines Teilpakets
werden nicht als Nachweis für das gesamte Produkt gewertet.


## Abgleich mit aktuellem main vom 21.09.2026

Seit dem letzten Matrixstand wurden weitere bereits bekannte Lücken geschlossen:

- #338 ist gemergt: Blocklisten-Diagnose speichert nur noch den redigierten
  HTTPS-Origin; Pfad, Query und Fragment erreichen die Diagnose nicht.
- #341 ist gemergt: zugängliche Inline-Hilfen für Netzwerk, RTP/RTCP und
  Networkboot sind wieder auf aktuellem main vorhanden.
- #333 sowie #346–#349 sind gemergt: der DLNA/UPnP-MediaRenderer besitzt
  Konfiguration, MediaRenderer-Protokollkern, gebundenen SSDP/HTTP-Dienst,
  sicheren Media-Fetch/Playback sowie Mini-Services-Worker/Admin-Integration.
  #285 bleibt für praktische Audio-/Video-/Controller-Hardwareabnahme offen;
  die Softwarekette wird nicht erneut implementiert.
- Die vorherigen Screen-, CLI-, Baseline-, Gateway- und Diagnosereparaturen
  bleiben Bestandteil von main; historische Draft-/Ersatz-PRs sind kein
  zusätzlicher offener Implementierungsstrang.

Die verbleibenden Punkte aus #330 werden ab jetzt in drei Klassen geführt:

| Klasse | Offene Punkte | Bedeutung |
|---|---|---|
| **Implementierungslücke** | Keine aktuell bekannte unklassifizierte Kernlücke aus der #330-Checkliste. Der Log-/Diagnosepfad besitzt jetzt einen CI-Guard und die dienstweise Negativfall-Matrix ist dokumentiert. Neue Befunde werden wieder als Implementierungslücke geführt. | Repositoryseitige Softwarepunkte gelten nur mit CI-Nachweis als erledigt; reale Fähigkeiten bleiben davon getrennt. |
| **Prüfnachweis** | saubere Installation und Lifecycle auf realem Linux/Windows; reale LAN-/IPv6-Linkwechsel; echter Gateway-Paketfluss; reale RTP-/DLNA-/PXE-Geräte; Android-Hintergrundbetrieb/Capture/Audio; visuelle WCAG-/Touch-/Tastaturprüfung; Gesamt-RSS, Last, Durchsatz und Langzeitleak-Messungen | CI-/Loopback-Nachweise existieren, ersetzen aber die praktische Abnahme nicht. Das konkrete Protokoll steht in `MINI_SERVICES_EXTERNAL_ACCEPTANCE.md`. |
| **Technische Grenze** | virtuelles Windows-Mikrofon ohne freigegebene Systemkomponente; fremde RTP-Player ohne Discovery-Profil; DHCP/Gateway weiterhin IPv4-only; Windows-Ausgabe derzeit nur Systemstandard, solange keine verlässliche gezielte Gerätewahl nachgewiesen ist | Kein stilles „erledigt“; Grenze bleibt sichtbar dokumentiert und darf nicht als Fähigkeit dargestellt werden. |

### Status der Issue-Checkliste

- **Aktueller Stand/CI/PR-Abgleich:** auf 24.09.2026 aktualisiert. DHCP-
  Fremdservererkennung (#418) und sichere UFW/firewalld-Verwaltung (#424) sind
  Bestandteil des Softwarestands und werden nicht mehr als Implementierungslücke
  geführt. Laufende fachfremde PRs werden nicht als Mini-Services-Nachweis gewertet.
- **Inline-Hilfe:** durch #341 softwareseitig erledigt; visuelle Bedienabnahme
  bleibt als Prüfnachweis offen.
- **Diagnose-URL-Datenschutz:** durch #338 erledigt.
- **DLNA (#285):** Softwareimplementierung vorhanden; praktische Hardware- und
  Controller-Abnahme offen.
- **Security-/Logprüfung:** bekannte rohe Exception-Ausgaben in Screen- und
  Audio-Admin-Pfaden sind redigiert. `tools/mini_services_log_audit.py` prüft
  die Mini-Services-/Netzwerk-/Audio-/Screen-Runtimequellen künftig in CI gegen
  direktes Durchreichen von Exception-Text an UI-/JSON-/Log-Sinks.
- **Negativfall-Matrix:** `MINI_SERVICES_FAILURE_MATRIX.md` ordnet Doppelstart,
  Stop/Restart, Port-/Rechte-/Netz-/Gerätefehler, Recovery und externe
  Hardwarefälle pro Dienst einem CI- oder externen Nachweis zu.
- **Messungen/Gesamtabnahme:** Der vorhandene Lifecycle-Mikrobenchmark misst
  Konstruktion, Python-Heap-Peak, Start, Doppelstart, Stop und Prozess-CPU auf
  Loopback und läuft als eigener Extended-Quality-Smoke. Gesamt-RSS,
  Protokolllast, Durchsatz, reale Hardware und Langzeitverhalten bleiben externe
  Abnahme nach `MINI_SERVICES_EXTERNAL_ACCEPTANCE.md`.

Diese Klassifizierung ersetzt keine Matrixzelle durch ein pauschales „V“. Eine
Zelle wird erst hochgestuft, wenn der konkrete Nachweis für den jeweiligen
Dienst vorliegt.

## Reproduzierbares Software-Gate

Die Extended-Quality-Pipeline führt den dependency-freien
`tools.mini_services_benchmark` mit einer kurzen Loopback-Stichprobe aus. Das
Gate verlangt DHCP, DNS, TFTP und SIP, gültige nichtnegative Messwerte für
Konstruktion, Python-Heap-Peak, Start, Doppelstart, Stop und CPU sowie null
zurückbleibende Listener-Threads.

Die vollständige Python-CI prüft zusätzlich die vorhandenen Lifecycle-,
Netzwerk-Recovery-, Gateway-, Security-, Audio-, Boot- und API-Regressionen.
Damit ist die repositoryseitige Software-Basis reproduzierbar. Sie ist bewusst
nicht gleichbedeutend mit realem Paketfluss, Audio-Wiedergabe, PXE-Boot,
Android-Hintergrundbetrieb oder formaler Accessibility-Abnahme.

Alle nicht in CI belegbaren Schritte sind nun in
`docs/MINI_SERVICES_EXTERNAL_ACCEPTANCE.md` mit Eingaben, erwartetem Ergebnis
und Abschlussregel konkretisiert.

Die dienstweise Software-Fehlermatrix steht ergänzend in
`docs/MINI_SERVICES_FAILURE_MATRIX.md`. Sie ist die verbindliche Zuordnung,
welche Negativfälle im Repository regressionsgetestet sind und welche nur in
einer realen Umgebung abgenommen werden können.

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
| Abhängigkeiten | V | V | V | T | V | V | T | T | T | Worker/Web-Eigentümer explizit; Audio-Programme nach erforderlichen/bedingten Funktionen erkannt; übrige Systemwerkzeuge noch nicht vollständig modelliert |
| Hardwareerkennung | – | – | – | V | V | – | T | T | T | Interfaces, Pulse, ALSA- und DirectShow-Mikrofone; Windows-Systemstandard verfügbar; keine Windows-Ausgangserkennung |
| Netzwerkdiensterkennung | T | T | T | V | T | T | T | T | T | Bestehende LAN-Profile für aktive Desktop-RTP-Empfänger; fremde Player nicht entdeckt |
| Konfiguration | V | V | V | V | V | V | V | V | V | Bestehende JSON-/SQLite-Speicher; Audio jetzt persistent |
| Standardwerte | V | V | V | V | V | V | V | V | V | Sichere Bindings/Opt-in; DHCP-Netz nicht automatisch erraten |
| Validierung | V | V | V | V | V | V | V | V | V | Vor Start/Änderung; Audio-Portpaar und boolesche Schalter geprüft |
| UI | T | T | T | T | T | T | T | T | T | Gemeinsame Aktionen und geführte Bootprofile vorhanden; vollständige visuelle Prüfung fehlt |
| CLI | V | V | V | V | V | V | V | V | V | Einzeldienst-Lifecycle/Status über Mailbox bzw. authentifizierte bestehende Web-API; Scan über Web-API; Windows-Terminalabnahme offen |
| API | V | V | V | V | V | V | V | V | V | Gemeinsame Admin-/CSRF-API plus bestehende Fachrouten |
| Fehlerbehandlung | V | V | V | T | V | V | V | V | V | Verständliche Antworten, erhaltene Formulare, isolierte Speicherfehler |
| Logging | T | T | T | T | T | T | T | T | T | Strukturierte gemeinsame Fehler; noch kein vollständiger Audit aller alten Logpfade |
| Diagnose | V | V | V | T | V | V | T | T | T | Status, Fehler, Scan und Handlungshinweise; Hardware-Diagnose bleibt begrenzt |
| Healthcheck | V | V | V | T | V | V | T | T | V | Socket/Thread, Bootprofile/Dateien, PCM-Empfang; kein End-to-End-Nachweis überall |
| Recovery | V | V | V | T | V | V | V | V | V | Backoff, Netzwerk-Rückkehr, Dateien und Prozessfehler; stille Treiber nicht vollständig erkannt |
| Berechtigungen | V | V | V | V | V | V | V | V | V | Admin/CSRF; privilegierter Netzwerkworker bleibt getrennt |
| Security | T | T | T | T | T | T | T | T | T | Gezielte Regressionstests; kein vollständiger Security-Audit behauptet |
| Dokumentation | V | V | V | V | V | V | V | V | V | Gleiche Betriebskapitel, Optionen und technische Grenzen dokumentiert |
| Inline-Hilfe | T | T | T | T | V | T | T | T | T | Aktiviert/Autostart im Hub erklärt und Fachseiten direkt verlinkt; erweiterte Optionen noch uneinheitlich |
| Beispiele | V | V | V | V | V | V | V | V | V | Handbücher enthalten Betriebsabläufe und Konfigurationsbeispiele |
| Tests | T | T | T | T | T | T | T | T | T | Umfang erweitert; gesamte verlangte Fehlermatrix nicht pro Plattform nachgewiesen |
| Plattformangaben | V | V | V | V | V | V | V | V | V | Unterstützung und Grenzen ausdrücklich benannt |
| Linux | T | T | T | T | T | V | T | T | T | Loopback/Protokolltests; echte LAN-/Audio-/Gateway-Hardwareabnahme fehlt |
| Windows | ? | ? | ? | T | ? | ? | T | T | T | Windows-Audio über DirectShow/FFplay; reale Geräteabnahme fehlt |
| Android | – | – | – | – | ? | T | T | T | T | Native Bridge beibehalten; Android-CI-Build erfolgreich, reale Geräteabnahme fehlt |
| Performance | T | T | T | ? | T | ? | ? | ? | ? | Lifecycle-Mikrobenchmark; kein Last-/Durchsatzvergleich aller Dienste |
| Ressourcenverbrauch | V | V | V | T | V | T | T | T | T | Begrenzte Tasks/Queues/Cache; kein vollständiges RAM-/CPU-Profil |
| Startzeit | T | T | T | ? | T | ? | ? | ? | ? | Messwerte unten; keine Kaltstartmessung des ganzen Systems |
| Offline | V | T | V | T | V | V | V | V | V | Lokale Funktionen; DNS-Upstreams/externe Bootziele benötigen ihr Netz |
| Netzwerkwechsel | V | V | V | T | V | T | T | T | T | IPv4-/IPv6-Binding-Recovery; reale Linkwechsel und stille Audiounterbrechung offen |
| Discovery | V | V | V | V | T | V | T | T | T | Scan, Zeit, Treffer und Fehler; kein universelles Geräteprotokoll |
| Service-Interaktion | V | V | V | V | V | V | V | V | V | Ausfallisolation und gemeinsamer Netzwerk-/Audio-Prüfstand |
| Monitoring | V | V | V | T | V | V | T | T | V | Frischer Heartbeat; reale PCM-Daten statt Prozessbehauptung |
| Versionsanzeige | V | V | V | V | V | V | V | V | V | Gemeinsame Projektversion im Status |
| Bedienbarkeit | T | T | T | T | T | T | T | T | T | Besseres Feedback; vollständige Bedienabnahme fehlt |
| Mobile/Touch/Fokus | ? | ? | ? | ? | ? | ? | ? | ? | ? | Codeanpassungen vorhanden, visuelle Abnahme blockiert |
| Parallelität/Atomizität | V | V | V | T | V | V | V | V | V | Locks, atomare Dateien und Queue-Claims; Aktiver Linux-Gateway-Reload nutzt nft-Transaktion; Windows und Recovery nicht insgesamt atomar |
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
| AS/AR | Windows | DirectShow-Sender und FFplay-Receiver implementiert; nur Systemstandard-Ausgabe | Gezielte Windows-Geräteauswahl und virtuelle Mikrofone fehlen; Hardwareabnahme offen | Windows-Hardwareabnahme; gezielte Ausgänge gesondert prüfen |
| AS/AR | Android | CI-Build erfolgreich; reale Hintergrund-/Geräteprüfung offen | Kein Android-Gerät/ADB in dieser Umgebung | Auf echtem Gerät Hintergrundbetrieb, Capture und Stop prüfen |
| AO | Remote-Ausgabe | Explizite RTP/Opus-Bindung, Suche und Versand implementiert; Empfang unbestätigt | Bestehendes RTP-Protokoll bietet weder Verschlüsselung noch Wiedergabebestätigung; nur vertrauenswürdiges privates IPv4-Netz | Reale Empfänger-/Lautsprecherabnahme; DLNA bleibt #285 |
| AS | Discovery | Fremde RTP-Player werden nicht erkannt | Sie veröffentlichen kein SimpleOffice-Profil | Nur tatsächlich verfügbare Protokolle ergänzen; manuelle Ziele bleiben |
| AR | ALSA-Ausgänge | Receiver benötigt PulseAudio/PipeWire-Pulse | Vorhandener PCM-Verteiler und virtuelle Mikrofone nutzen diesen Backend | Separaten ALSA-Ausgabepfad nur mit vollständigem Cleanup/Health ergänzen |
| Gateway | Health/Atomizität | Linux-Regelstruktur/-inhalt geprüft, aktiver Reload über nft-Transaktion; realer Datenpfad und Windows-Atomizität offen | Gemeinsames Forwarding ist nicht Teil der Transaktion; nft fehlt in der Testumgebung | Reale nft-Versionen/Kernel-Paketfluss prüfen und Windows-Reload verbessern |
| DHCP/Gateway | Automatische Konfiguration | Kein eigenmächtig gewähltes neues DHCP-Netz | Fremde DHCP-Server und vorhandene Netzverwaltung dürfen nicht gestört werden | Konflikterkennung und geführte Auswahl ohne automatische Aktivierung |
| Netzwerkdienste | IPv6-Netzwechsel | Linux-/Windows-Inventar und Binding-Recovery implementiert; reale LAN-/Windows-Abnahme offen | OS-Grenzen im Test gemockt; DHCP/Gateway bleiben IPv4-only | Reale Linkwechsel prüfen; DNS-IPv6-Loopback separat getestet |
| HTTP/TFTP | Bedienung | Geführter Profileditor umgesetzt; tatsächlicher Booterfolg ungeprüft | Bootdateien und Kernel-Parameter hängen vom Client ab | Reale PXE-Clients mit den angelegten Profilen prüfen |
| Alle | Mobile/Accessibility | Kein visueller Konformitätsnachweis | Browser blockiert lokale Testseite mit ERR_BLOCKED_BY_CLIENT | Desktop/Tablet/Smartphone/WebView samt Fokus/Kontrast prüfen |
| Alle | Logs/Tests | Noch nicht jeder relevante Aspekt gleichwertig | Historische Fachpfade und unvollständige Negativfallabdeckung | Verbleibende T/F/?-Zeilen gezielt abarbeiten |

Diese Punkte sind keine pauschalen Ausnahmen vom Auftrag. Fehlende Implementierung
bleibt offen; Umgebungsgrenzen werden getrennt davon benannt. Die PRs bleiben Draft,
bis die anwendbaren Kriterien erfüllt oder konkrete technische Abweichungen
vollständig bewertet sind. GitHub-Abgleich vom 16.09.2026: PR #288 (Bildschirm) wurde am 14.09.2026
gemergt. Alle acht Workflows seines Heads 16cc006a sind erfolgreich, einschließlich
Android- und Desktop-Build. Weitere Signaling-/Stop-Korrekturen stehen separat
in Draft-PR #301 auf main. #285 ist ein offenes DLNA-Issue, kein PR.
Diese Angaben ersetzen keine reale Geräteabnahme.

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

### Windows-Receiver

Vorhandene PCM-Verteilung nutzt unter Windows FFplay statt paplay.
Scan/API kennzeichnen den Systemstandard ausdrücklich als nicht hardwaregeprüft;
kein künstlicher Pulse-Sink wird im Ausgaberegister angelegt. Windows-Defaults
und Reset wählen Systemstandard ohne virtuelles Mikrofon. Start/Stop,
Teilstartfehler, Persistenz/Reset und Rechte sind getestet; echter FFplay-Aufruf
mit synthetischem PCM und SDL-Dummytreiber ergänzt die gemockten OS-Grenzen.
Gezielte Geräteauswahl und Windows-Hardwareabnahme bleiben offen. Virtuelle Windows-Mikrofone bleiben eine dokumentierte Plattformgrenze; VB-CABLE und SysVAD sind ausgeschlossen.

Audio-Neustarts prüfen Startargumente und Programmverfügbarkeit vor dem Stop.
Abgewiesene Starts überschreiben keine gespeicherten Einstellungen.
Fehler beim Speichern lassen laufende Streams bestehen. Ein expliziter Restart
ersetzt auch bei gleicher Konfiguration den Prozess. Zwischen Vorprüfung und
tatsächlichem Prozessstart sind weiterhin Betriebssystemfehler möglich; dann
greift die begrenzte Recovery. Regressionstests decken diese Vorprüfungsfälle ab.

## Vollständiger Unittest-Lauf am 17.09.2026

Gemeinsamer Audio-/Gateway-Stand: 1.695 Unittests in 155,932 Sekunden,
10 übersprungen, keine Fehler. Gateway-Recovery, aktiver Linux-Reload und
Regelinhalt sowie die jüngsten Audio-Cleanup-/Scan-/Programmprüfungen sind
enthalten. Der währenddessen ergänzte Paketierungstest wurde anschließend
separat erfolgreich ausgeführt (beide Paketdefinitionen, isolierte Imports).
Python-Compileall für app/tools und neue Gateway-Module, JavaScript-Syntax
des Mini-Service-Hubs und Diff-Prüfung bestanden. Dies ist ein Unittest-Lauf,
keine Aussage über separat definierte pytest-Funktionen oder Plattformabnahme.

## Frühere vollständige Testläufe

Audio-Branch auf Commit 67a4d8f: 1.662 Tests, 10 übersprungen, keine Fehler
(166,718 Sekunden). 74 gezielte Tests schließen Restart-Vorprüfung ein.
Separater main-/Screen-Stand: 1.559 Tests, 10 übersprungen, keine Fehler
(160,145 Sekunden); danach ergänzte Poll-Heartbeat-Prüfung im gezielten Lauf.
PR #301 hat zusätzlich drei Node-Runtimetests; Policy-, Größen-, Syntax- und
CRA-Checks bestanden. pip-audit fehlt in der lokalen Testumgebung; nicht installiert.

Die Anbindung eines virtuellen Windows-Mikrofons benötigt eine zusätzliche
Systemkomponente. Im Projekt ist kein entsprechender Treiber vorhanden.
VB-CABLE und SysVAD wurden vom Auftraggeber ausdrücklich ausgeschlossen.
Der virtuelle Mikrofoneingang unter Windows bleibt daher eine dokumentierte
Plattformgrenze; beide Komponenten werden nicht integriert. Keine neue Bibliothek oder Systemkomponente
wurde im Rahmen dieser Änderungen installiert. Übrige Matrixlücken bleiben offen.

Audio-Ausgabe: Fehler beim Beenden einzelner Player blockieren die Bereinigung
der übrigen Prozesse und temporären Lautstärkedateien nicht mehr. Unbestätigt
beendete Player bleiben referenziert; neue Wiedergabe ist gesperrt, bis ein
erneuter Stop die Bereinigung abschließt. Diagnose enthält keine Exception-Nutzdaten.

Der Mini-Service-Hub unterscheidet nun Scan-Fortschritt, Scan-Fehler, leere
Ergebnisse und Treffer mit Suchbereich. Fehler erscheinen als Text mit
Handlungshinweis, Statusänderungen über aria-live. Audio-Scans speichern den
Zustand scanning vor der Discovery; Speicherfehler beim Registrieren von
Ausgängen werden als failed/HTTP 503 erfasst. Abhängigkeiten, Eigentümer und
Version sind in der gemeinsamen Diagnose sichtbar. Das macht die vorhandenen
Metadaten sichtbar, ersetzt aber nicht die noch unvollständige Erkennung aller
Systemvoraussetzungen. Node-Runtimetests prüfen Anzeige und Fehleraktualisierung.

Audio-Programmprüfung ergänzt: erforderliche Stream-Programme und bedingte
Ansage-/Discovery-Funktionen sind maschinenlesbar und in der gemeinsamen Diagnose
sichtbar. Fehlendes paplay wird auch beim reinen virtuellen Mikrofon vor dem
Sessionwechsel erkannt. Diese PATH-Prüfung ersetzt keine Hardware-, Codec- oder
Plattformabnahme; die offenen Punkte der Qualitätsmatrix bleiben bestehen.

### IPv6-Integration und reale Loopback-Sockets

Linux-/Windows-Inventar und IPv6-Bindingprüfung aus #296 in den gemeinsamen
Audio-Stand übernommen. 55 relevante Netzwerk-/API-/Audio-Tests bestanden.
`test_dns_ipv6_loopback` prüft echte AF_INET6-Sockets auf ::1: DNS-AAAA über UDP
und TCP, Start-Idempotenz, Stop und erneuten Start samt geschlossenen Sockets und
beendeten Listener-Threads. Eine Upstream-Anfrage würde den Test scheitern lassen.
Freie Ports werden vom OS vergeben (UDP/TCP jeweils eigener Port); keine LAN-
Pakete oder Systemkonfigurationsänderungen. In dieser Umgebung ohne Skip bestanden.
Reale Interface-Linkwechsel, identischer Produktionsport und Windows-/Android-
Geräteabnahme sind damit nicht nachgewiesen. Python-Syntax und Diff geprüft.

### Lokaler Integrationsstand: Windows-NAT und Linux-Inventar

Windows-NAT-Restart bei gleichem Namen/Netz/Modus in den Audio-Stand integriert.
Zusätzlich erkennt das Linux-Inventar fehlerhafte Adressobjekte als unbekannten
Scan statt als fehlende Hardware. Ungültige Routing-JSON-Strukturen verwerfen
nicht das gültige Adressinventar. Ein erfolgreich gelesenes leeres Inventar
bestätigt weiterhin Adressverlust. Keine neuen Dependencies.

Die Veröffentlichung war zunächst durch fehlendes Workspace-Guthaben in der
automatischen Freigabeprüfung blockiert. Nach erneutem Push-Auftrag wurde die
Veröffentlichung wieder aufgenommen. Die fachlichen Abnahmegrenzen bleiben bestehen.

Prüfung des lokalen Integrationsstands: 53 relevante Tests bestanden, darunter
echter IPv6-DNS-Loopback; Python-Syntax und Diff-Prüfung bestanden.

## Audio-Konfigurationsvalidierung (17.09.2026)

Ausgangsregistrierung prüft Kanäle, Lautstärke, Online-Boolean und Gerätekennung
vor dem Schreiben. Ungültige Updates bewahren die bestehende Konfiguration;
ungültige Prioritäten werden vor dem Einreihen abgewiesen. API liefert HTTP 400
mit Handlungshinweis statt stiller Werteänderung. 55 relevante Audio-Tests
bestanden, einschließlich persistenter Grenzwerte, unveränderter Datensätze bei
Fehlern sowie Admin-/CSRF-Schutz. Python-Syntax und Diff geprüft. Kein neuer
Gesamtlauf und kein Remote-Transportnachweis; externe Audio-Nodes bleiben offen.

## Entfernte Durchsagen über bestehenden RTP-Empfänger

Ausgaberegister um explizite RTP/Opus-Bindung erweitert, vorhandene SQLite-Daten
werden migriert. Ungebundene Alt-Nodes bleiben unverändert ohne Transport.
Durchsagen-Worker verwendet bestehendes FFmpeg, Lautstärkeanpassung, begrenzte
Prozesslaufzeit, Stop/Cleanup und Nichtwiederholung teilweise gesendeter Aufträge.
Doppelte RTP-Empfänger innerhalb einer Gruppe werden vor Prozessstart abgewiesen.
Admin-/CSRF-API, bestehende LAN-Suche, manuelle Zielwahl und persistente Speicherung
sind in der Audio-Seite verfügbar. Versandstatus `sent-unconfirmed` behauptet
keine physische Wiedergabe. Private IPv4-Adressen/Loopback sind zulässig; RTP
bleibt unverschlüsselt und ohne Peer-Authentifizierung, daher nur vertraute Netze.
Keine neue Dependency, kein zusätzlicher Listener oder Service-Manager.

85 relevante Tests bestanden, einschließlich echtem RTP/Opus-Langstream und
kurzem Gong über Loopback in den bestehenden Decoder. Anschließend neun
Transport-Tests bestanden, darin zwei Node-Runtimefälle für Suche/Auswahl,
CSRF-geschützte Registrierung und Fehlerfeedback. Tests prüfen Migration,
Validierung, gemischte Gruppen, Stop, fehlendes FFmpeg, Prozessfehler und
Nichtwiederholung. Python-/JavaScript-Syntax und Diff geprüft. Reale Hardware,
Ende-zu-Ende-Bestätigung, Audio über IPv6 und visuelle Abnahme bleiben offen.

### Gemeinsame Mikrofon-Discovery

Die Hub-Suche verwendet jetzt das gespeicherte Capture-Backend (PulseAudio,
ALSA oder DirectShow) statt unabhängig davon automatisch ein anderes Backend
auszuwählen. Ungültige Einstellungen erzeugen einen gespeicherten fehlgeschlagenen
Scan mit sicherer Diagnose. 42 API-/Discovery-/Lifecycle-Tests bestanden.
Der Gesamtlauf fand zunächst eine fehlende HTTP-Methode am neuen RTP-Formular;
diese ist korrigiert. Anschließend bestanden 47 gezielte Accessibility-,
Routen-, Transport- und API-Tests.

CI-Abgleich: Auf Audio-Commit fd52a988 sind Android-APK, Desktop, Docker,
Tests/Dependency-Audit und Security-Quick-Wins erfolgreich. Auf Screen-Commit
34bd7f9d sind alle acht gemeldeten Workflows erfolgreich, einschließlich Android,
Desktop, Tests/Audit und CodeQL. Diese Nachweise beziehen sich auf die genannten
Commits, nicht auf spätere Änderungen oder reale Hardware.

### Gesamtlauf nach RTP- und Discovery-Korrekturen

1.726 Unittests in 160,102 Sekunden erfolgreich, 10 übersprungen. Enthalten sind
RTP-Transport, echter kurzer Gong/Opus-Loopback, Migration, API/Berechtigungen,
Frontend-Runtimefälle, konfigurierte Mikrofon-Discovery und Formsemantik.
Python-/JavaScript-Syntax sowie Diff geprüft. Kein Nachweis für separat definierte
pytest-Funktionen oder reale Hardware-/Mobile-Abnahme. Auf dem RTP-Commit
f5e791a9 sind zusätzlich Android-APK, Desktop, Docker und Security-Quick-Wins
in CI erfolgreich; Tests/Audit waren beim Abruf noch in Arbeit.

### CLI-Erweiterung vom 18.09.2026 (PR #307)

Audio-Sender, Audio-Receiver, Audio-Ausgabe und HTTP-Boot unterstützen jetzt
Start/Stop/Restart/Status/Scan über `start.sh mini-services --service …`.
Netzwerk-Scans verwenden ebenfalls die bestehende Web-API. Die Dienstverantwortung
bleibt im bisherigen Prozess; der CLI-Client importiert keine Flask-App.

32 gezielte CLI-/API-Tests bestanden, darunter echte HTTP-Anmeldung mit Cookie-
und CSRF-Rotation, Rollenprüfung, abgewiesenes altes Token, bestehende Audio-
Manager, HTTP-Boot-Dateispeicher, DNS-Scan, Timeout ohne Wiederholung, begrenzte
Antwortgröße und abgewiesene Weiterleitungen. Keine neue Dependency.

Technische Grenze: Webaktionen benötigen einen laufenden Webprozess und ein
lokales Admin-Passwort; Konfiguration erfolgt weiterhin über UI/API. HTTP ist auf
explizite Loopback-Adressen beschränkt (auch IPv6), sonst HTTPS. Diese CLI-Prüfung
bestätigt keine IPv6-Unterstützung des zugrunde liegenden RTP-Audiotransports und
keine physische Audioausgabe. Reale Windows-/Android-Terminaltests bleiben offen.

### Diagnose-/Datenschutzprüfung vom 19.09.2026

HTTP-Boot verwendet für Konfigurations-, Profil- und Föderationsfehler das
vorhandene strukturierte Dienstlog mit Service, Event, Severity, Timestamp,
Request-ID, Fehlertyp und begrenzten Stackpositionen. Exception-Texte, lokale
Variablen, Quelltext, Request-Header und Bodies werden nicht übernommen.
Dateisystemfehler erhalten HTTP 503, einen Handlungshinweis und `no-store`;
fehlende Dateien bleiben HTTP 404. Föderationsauthentifizierung bleibt unverändert.

DHCP-Fehlercallbacks übertragen keine Exception-Nutzdaten mehr. TFTP sendet bei
Berechtigungsfehlern eine feste Protokollmeldung statt lokaler Dateipfade.
Gateway-Fehler beim Prozessaufruf enthalten nur den Fehlertyp, nicht den Command-
oder Exception-Text. Gezielte Tests prüfen die tatsächlichen Fehlerpfade mit
sensiblen Sentinel-Werten sowie erhaltene Statuscodes und Zugriffsprüfungen.
Dies schließt die gefundenen Lecks; eine pauschale Prüfung aller Projektlogs
oder ein vollständiger Security-Audit wird daraus nicht abgeleitet.

PR-Abgleich: #301, #307 und #309 sind gemergt; für die beiden letzten Heads
sind alle acht CI-Workflows erfolgreich. Echte Hardware-/Mobilabnahmen bleiben
offen. Der kombinierte CLI-/Basisfix-Stand bestand 1.828 Tests, 10 übersprungen.

UI-Prüfversuch am 19.09.2026: Der verfügbare Cloud-Browser blockiert auch die
neue lokale Vorschauverbindung mit `net::ERR_BLOCKED_BY_CLIENT`. Der nur für den
Verbindungstest gestartete lokale Server wurde wieder beendet. Keine Umgehung,
kein neuer Browser und keine neue Dependency installiert. Sichtprüfung bleibt
ungeprüft; bestehende DOM-/Frontend-Tests werden nicht als Ersatz ausgewiesen.
