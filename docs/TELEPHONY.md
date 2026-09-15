# SimpleOffice4Me Telefonie

## Zweck

Lokaler SIP-Registrar und Redirect-Dienst für konfigurierte Nebenstellen.
Der bestehende Mini-Services-Worker bleibt Eigentümer; RTP-Medien fließen direkt
zwischen Endgeräten. Einzelheiten zum unterstützten Protokoll folgen unten.

## Voraussetzungen

Python-Standardbibliothek für den Registrar, vorhandene SimpleOffice-Telefonie-
Datenbank und ein SIP-Endgerät bzw. kompatibler Client. Die Webverwaltung nutzt
die bestehende Projekt-Kryptografie für gespeicherte Passwörter. Es wird keine
neue SIP-Bibliothek benötigt oder automatisch installiert.

## Standardbetrieb

`./start.sh` startet den Worker und den lokalen SIP-Dienst automatisch, sofern
Aktiviert/Autostart nicht abgeschaltet wurden. Die gemeinsame Dienstübersicht
bietet Start, Stop, Restart und Status. Nebenstellen und Zugangsdaten werden
weiterhin unter **Mini Services → SIP / Telefonie** eingerichtet.
[Gemeinsamer Betrieb, CLI und Zustände](MINI_SERVICES.md).

## Konfiguration

`telephony/telephony-profiles.sqlite3` liegt neben der Mini-Services-Konfiguration.
Die Telefonie-Verwaltung persistiert dieselben Werte, die der Worker verwendet.

| Option | Standard / Wirkung |
|---|---|
| `registrar_host` | leer; automatisch ermittelte Adresse wird angezeigt, manueller Host für abweichende/externe Infrastruktur |
| `registrar_port` | `5060`; gültiger Port 1–65535 |
| `transport` | `udp`; der eingebaute Mini-Registrar verwendet UDP, andere Transportoptionen betreffen externe SIP-Infrastruktur |
| `realm` | `simpleoffice.local`; Bestandteil des Digest-Verifiers, bei vorhandenen Profilen nicht beliebig änderbar |
| `stun_server` | leer; optionale Client-/externe Infrastruktur, der Mini-Registrar betreibt selbst keinen STUN-Server |
| `SIMPLEOFFICE_SIP_BIND` | nicht gesetzt; manuell nur Loopback oder private IPv4 zulässig |
| Aktiviert / Autostart | gemeinsame Worker-Präferenzen, getrennt von Nebenstellen-Konfiguration |

## Discovery

Der Dienst ermittelt eine private lokale IPv4-Adresse, sonst Loopback. Bei
Netzwerkwechsel bewertet der Worker die Auswahl alle 30 Sekunden neu.
Scan zeigt tatsächlich registrierte lokale Telefone. Er sucht nicht nach fremden
SIP-Providern und erzeugt keine Nebenstellen automatisch. Ein fest konfiguriertes
verlorenes Binding führt zu `waiting`; Rückkehr kann ohne manuellen Restart erfolgen.

## Ports

UDP 5060 beziehungsweise konfigurierter Port. Medienports werden zwischen den
Endgeräten ausgehandelt und gehören nicht zum Registrar. Keine SIP-WebSocket-/TLS-
Listener im eingebauten Mini-Dienst.

## Security

Siehe „Sicherheit“ und „Zugangsdaten“ unten. Zusätzlich sind offene Challenges auf
1.024 begrenzt. Verdrängte Challenges verlieren ihre Replay-Zähler. Nonce-Counter-
Prüfung und Aktualisierung sind gemeinsam gesperrt. Normale Mini-Service-Logs
enthalten keine SIP-Pakete oder Digest-Zugangsdaten. API-Steuerung erfordert Admin/CSRF.

## Fehlerdiagnose

„Wartet auf Mini-Services-Worker“: `./start.sh status` bzw.
`./start.sh mini-services status` prüfen. Portkonflikte betreffen nur SIP;
die übrigen Worker-Dienste bleiben unabhängig. Eine Loopback-Adresse ist nicht
vom Telefon im LAN erreichbar. Fehlende Registrierungen: Nebenstelle aktiviert,
Passwort/Realm/Serveradresse passend und LAN-/Firewallzugriff möglich?

Health prüft gebundenen Socket und aktiven Listener; Registrierungen zeigen echte
Client-Aktivität. Startfehler verwenden begrenzten Backoff, bekannte fehlende
Netzwerk-Bindings werden langsam erneut geprüft. Stop beendet automatischen
Wiederanlauf. Registrierungen sind flüchtig; nach Restart registrieren sich
Telefone erneut. Lokaler Betrieb benötigt keinen Internetzugang.

## API

ID `sip` in der [gemeinsamen Mini-Service-API](MINI_SERVICES.md).
Die vorhandene Telefonie-Verwaltung bleibt für Profile, generierte Passwörter
und Clientdaten zuständig. SIP selbst ist ein separates UDP-Protokoll.

## Plattformen

Python-Registrar auf Linux und Windows; konkrete Firewall-/Endgerätetests bleiben
erforderlich. Android nutzt die vorhandene native Übergabe an einen installierten
SIP-Handler und kann über WebView administrieren. Ein interner vollständiger
Android-SIP-User-Agent ist dadurch nicht implementiert.

## Einschränkungen

Kein Internet-Proxy, PSTN-Trunk, Medienrelay oder vollständiger PBX-Ersatz. Der
eingebaute Registrar bleibt UDP/IPv4. Automatische Discovery bedeutet lokale
Adressauswahl und registrierte Telefone, keine vollständige Inventur aller SIP-
Geräte. Die nächste Ausbaustufe am Ende dieses Dokuments bleibt gesonderte Arbeit.

## Tests

`test_sip_runtime`, `test_mini_lifecycle`, `test_mini_security`,
`test_mini_network_recovery`, `test_mini_control_api` sowie bestehende
`test_telephony_*`: Registrierungen, Digest/Replay, lokale Zieladressen,
Challenge-Grenzen, Start/Stop/Restart, Fehlerisolation, Bindings und geschützte API.

## Ziel

SimpleOffice4Me verwendet fuer lokale Telefonie Standard-SIP. Der normale Betrieb soll ohne Serverkonfiguration funktionieren: Der Mini-Services-Worker startet automatisch einen lokalen Registrar auf einer privaten LAN-Adresse mit Port `5060/UDP`.

Eine Nebenstelle kann sowohl mit einem Softphone als auch mit einem normalen SIP-Tischtelefon verwendet werden. Die privaten SimpleOffice-Nummern sind keine oeffentlichen PSTN- oder E.164-Rufnummern.

## Automatikbetrieb

Im Standardfall ist keine SIP-Serveradresse einzutragen:

1. Der Mini-Services-Worker sucht eine private RFC1918-LAN-Adresse der Installation.
2. Der SIP-Dienst bindet ausschliesslich diese Adresse, niemals automatisch `0.0.0.0`.
3. Falls kein privates LAN vorhanden ist, bleibt der Dienst auf Loopback und wird nicht als erreichbarer Chat-Registrar angeboten.
4. Standardport ist `5060/UDP`, Realm `simpleoffice.local`.
5. Einstellungen werden bei Aenderung der Telefonie-Daten automatisch vom Worker neu geladen.
6. Ist Port 5060 bereits durch eine vorhandene PBX belegt, bleiben DHCP, DNS, TFTP und Routing trotzdem aktiv; nur der eingebaute SIP-Dienst meldet einen Startfehler.

Eine manuelle Serveradresse ist damit nur noch eine Expertenoption fuer eine bereits vorhandene PBX oder einen bewusst abweichenden DNS-Namen.

## Eingebauter Registrar

Der Mini SIP Runtime-Baustein implementiert aktuell:

- `REGISTER` fuer lokale Nebenstellen;
- SIP Digest `qop=auth` mit kurzlebigen, an die Quelladresse gebundenen Nonces;
- Replay-Schutz ueber den Digest-Nonce-Counter;
- kurze In-Memory-Registrierungen mit begrenzter Laufzeit;
- `OPTIONS` fuer Erreichbarkeitspruefungen;
- authentifizierte lokale `INVITE`-Anfragen;
- Weiterleitung eines internen Anrufs per SIP-Redirect auf die tatsaechlich registrierte LAN-Adresse des Zielgeraets.

Der Dienst ist absichtlich **kein** offener Internet-SIP-Proxy und stellt keinen PSTN-Trunk bereit. Registrierungen werden nur aus Loopback bzw. privaten RFC1918-Netzen angenommen. Ein vom Telefon behaupteter Contact-Host wird nicht ungeprueft als Ziel uebernommen; fuer die Registrierung verwendet SimpleOffice die tatsaechliche LAN-Quelladresse des Pakets.

Audio und Video laufen nach der SIP-Aushandlung direkt zwischen den Endgeraeten. Der Registrar muss daher keine RTP-Medien weiterleiten.

## Zugangsdaten

Beim Anlegen einer Nebenstelle erzeugt SimpleOffice ein zufaelliges SIP-Passwort. Das Passwort wird einmalig angezeigt und weiterhin verschluesselt gespeichert.

Zusaetzlich wird fuer den eingebauten Registrar der SIP-Digest-Verifier `HA1 = MD5(username:realm:password)` abgelegt. Das ist der fuer SIP Digest erforderliche Verifier; der Mini-Services-Worker muss das Klartextpasswort dadurch nicht entschluesseln oder kennen.

Aeltere Profile ohne Verifier werden beim Aufruf der geschuetzten Telefonie-Verwaltung migriert, soweit ihr verschluesseltes Passwort noch mit dem Installationsschluessel entschluesselt werden kann.

Der Realm kann bei vorhandenen Nebenstellen nicht unbemerkt geaendert werden, weil er Bestandteil des Digest-Verifiers ist.

## Empfohlener externer Client: Linphone

Als Standard-Softphone fuer externe SIP-Nutzung wird **Linphone** empfohlen:

- Android und iOS
- Windows, macOS und GNU/Linux
- Standard-SIP und Konten von Drittanbietern
- Audio- und Videoanrufe
- Open Source

In SimpleOffice unter **Mini Services -> IP-Telefonie** reicht normalerweise:

1. **Telefon hinzufuegen** waehlen.
2. Eine freie Nebenstelle wie `101` eingeben.
3. Namen und Geraetetyp auswaehlen.
4. Das einmalig angezeigte Passwort in das Telefon uebernehmen.
5. Server und Port aus der automatisch angezeigten SimpleOffice-Konfiguration uebernehmen.

Typische Zuordnung:

| SIP-Client | SimpleOffice |
| --- | --- |
| Username / Auth ID | Nebenstelle, z. B. `101` |
| Password | einmalig angezeigtes SIP-Passwort |
| Domain / Registrar | automatisch angezeigter SimpleOffice-Host |
| Port | `5060` |
| Transport | `UDP` |
| Realm | `simpleoffice.local` |

## SimpleOffice-Chat

Die Chat-Oberflaeche verwendet dieselben Automatikwerte und soll keine Eingabe von Port, Transport, Realm oder STUN verlangen. Audio- und Videoaktionen werden direkt beim Teilnehmer angeboten.

Die vorhandene Android-App kann gueltige SIP-Ziele kontrolliert an einen installierten SIP-Handler uebergeben. Die naechste Ausbaustufe fuer vollstaendig interne SimpleOffice-zu-SimpleOffice-Anrufe ist ein integrierter User-Agent bzw. eine sichere automatische Anrufsignalisierung; der jetzt implementierte Registrar ist dafuer die lokale SIP-Infrastruktur und macht bereits normale SIP-Telefone ohne externe PBX nutzbar.

## Normales IP-Tischtelefon

Bei Yealink, Snom, Grandstream und anderen SIP-Telefonen unterscheiden sich nur die Feldnamen. Typische Zuordnung:

| Telefonfeld | Wert |
| --- | --- |
| Account / Line | beliebiger Anzeigename |
| SIP User / Extension | SimpleOffice-Nebenstelle |
| Authentication ID | SimpleOffice-Nebenstelle |
| Password | erzeugtes SIP-Passwort |
| Registrar / SIP Server / Domain | automatisch angezeigter SimpleOffice-SIP-Host |
| Registrar Port | `5060` |
| Transport | `UDP` |
| Outbound Proxy | leer |
| STUN | im lokalen Netz leer |

## Sicherheit

- kein automatisches Bind auf `0.0.0.0`;
- nur Loopback und RFC1918-Quellen werden verarbeitet;
- keine PSTN- oder Internet-Weiterleitung;
- SIP Digest mit kurzlebigen Nonces und Replay-Schutz;
- keine Klartextpasswoerter in der Runtime-Konfiguration;
- Passwoerter niemals in Audit-Logs schreiben;
- Administration bleibt CSRF- und Login-geschuetzt;
- SIP ueber das Internet nicht einfach per offenem UDP-Port veroeffentlichen;
- fuer spaetere externe Nutzung sind TLS, SRTP und ein kontrollierter STUN/TURN-Pfad getrennt zu implementieren.

## Naechster Ausbau

1. Registrierungsstatus mit letzter Registrierung und User-Agent in der UI
2. automatische Zuordnung von SimpleOffice-Benutzern zu internen Anrufidentitaeten
3. komplett integrierter SimpleOffice-User-Agent auf Android/Desktop, damit keine externe SIP-App benoetigt wird
4. sichere Federation-Aushandlung, welcher Peer bei einem Chat-Anruf Signalisierung bzw. Relay uebernimmt
5. SIP-DNS-SRV und optional DHCP Option 120
6. TLS/SRTP fuer Netze ausserhalb des vertrauenswuerdigen LAN
7. Rufgruppen und mehrere Endgeraete pro Benutzer
8. sicherer Linphone-Provisioning-Link/QR-Code
