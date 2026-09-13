# SimpleOffice4Me Telefonie

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
