# Federation Discovery und Vertrauen

SimpleOffice4Me trennt vier Dinge strikt voneinander:

1. **Discovery**: Ein Peer wurde gefunden.
2. **Prüfung**: Die Identität/Fingerprint wurde geprüft.
3. **Vertrauen**: Diese Instanz entscheidet lokal, ob sie dem Peer vertraut.
4. **Berechtigungen**: Dokumente, Kontakte, Kalender, Aufgaben usw. bleiben in der bestehenden Peer-Policy separat freizugeben.

Ein gefundener Peer wird deshalb standardmäßig als **bekannt, aber nicht geprüft** gespeichert und nicht automatisch aktiviert.

## Discovery-Wege

- **WLAN/LAN**: Benutzerstart über „Netzwerk scannen“. Ohne Vorgabe werden private RFC1918-IPv4-Netze der lokalen Instanz als `/24` geprüft. Für Docker/Podman kann im UI ausdrücklich ein vom Container erreichbares RFC1918- oder RFC6598-CIDR angegeben werden. Ein Scan ist auf maximal vier Netze, Präfix `/22` oder kleiner und insgesamt 1024 Hosts begrenzt. Standardport ist 8080. Abgerufen wird nur der Federation-Well-Known-Endpunkt. Gefundene Peers werden deaktiviert als `KNOWN_UNVERIFIED` gespeichert.
- **Land**: Abfrage konfigurierter Bootstrap-/Directory-Peers mit ISO-Ländercode, z. B. `DE`.
- **IP / Hostname / URL**: Direkter Abruf des Federation-Well-Known-Endpunkts. Host/IP mit Port, HTTP(S)-Basis-URL und ein sicherer Reverse-Proxy-Basispfad sind zulässig. Fehlt das Schema, wird zunächst HTTPS und bei einem Transportfehler anschließend HTTP geprüft. Explizites HTTPS wird nicht herabgestuft; die TLS-Zertifikatsprüfung bleibt aktiv. Query, Fragment und eingebettete Zugangsdaten sind unzulässig. Explizit eingegebene RFC1918- und RFC6598-Adressen (z. B. `100.110.89.7:8765`) sind für den manuellen lokalen/VPN-Weg zulässig.
- **E-Mail**: Die normalisierte E-Mail wird lokal mit SHA-256 gehasht. Nur der Hash wird als Rendezvous-Lookup übertragen.
- **QR-Code**: Das öffentliche Peer-Profil wird als `sofp://peer/...` ausgetauscht. QR-Import bedeutet zunächst nur `KNOWN_UNVERIFIED`.

Es findet bewusst **kein flächendeckender Internet-Portscan** statt. Automatische LAN-Discovery bleibt auf lokal erkannte `/24`-Netze begrenzt; zusätzliche Netze müssen ein Administrator ausdrücklich als RFC1918/RFC6598-CIDR angeben und sie bleiben mengenmäßig begrenzt. Land-Discovery läuft über Bootstrap-/Directory-Knoten.

## Protokoll- und Feature-Kompatibilität

Das öffentliche Discovery-Profil enthält zusätzlich zur Identität eine versionierte Kompatibilitätsbeschreibung:

- `application.name` und `application.version` dienen nur der Diagnose;
- `federation.name = simpleoffice-federation`;
- `federation.min_version` und `federation.max_version` beschreiben den unterstützten Protokollbereich;
- `features` versioniert Chat, Dokumente, Kontakte, Kalender und Aufgaben einzeln.

Die aktuelle Mindestvoraussetzung ist **Federation-Protokoll v1**. Eine Funktion gilt nur dann als bestätigt kompatibel, wenn der Federation-Protokollbereich kompatibel ist und die Gegenstelle eine unterstützte Feature-Version meldet. Eine alte Gegenstelle ohne diese Metadaten wird als **Kompatibilität unbekannt** behandelt, nicht automatisch als kompatibel oder inkompatibel.

Das Ergebnis wird beim Discovery-Lauf lokal als letzter Kompatibilitätsstand gespeichert und in der Peer-Tabelle angezeigt. Es ändert weder `KNOWN_UNVERIFIED`, Trust noch Datenrechte. Chat oder Datenaustausch werden dadurch also nicht automatisch freigeschaltet.

## Verifikationszustände

- `KNOWN_UNVERIFIED`
- `VERIFIED_ONE_WAY`
- `VERIFIED_MUTUAL`
- `VERIFIED_IN_PERSON`
- `VERIFIED_ADMIN`

Eine persönliche QR-Prüfung kann `VERIFIED_IN_PERSON` setzen, ohne Vertrauen zu vergeben. Dadurch ist „ich kenne dich“ nicht gleichbedeutend mit „ich vertraue dir“.

## Vertrauen

Trust ist **gerichtet**. `A -> B` erzeugt niemals automatisch `B -> A`.

Trust-Level:

- `NONE`
- `LOW`
- `NORMAL`
- `HIGH`

Weitergabe pro Beziehung:

- `DIRECT_ONLY`: Standard. Die Beziehung wird nicht exportiert.
- `RECOMMENDATION_ONLY`: Andere Peers dürfen die Beziehung als Hinweis sehen, aber daraus entsteht kein automatisches Vertrauen.
- `TRANSITIVE`: Darf als Web-of-Trust-Hinweis verwendet werden. `max_hops` ist auf 0–2 begrenzt.

Importierte Trust-Hinweise bleiben Empfehlungen. Nur eine lokale Entscheidung erzeugt lokalen Trust.

## Directory-Privacy

Ein Directory veröffentlicht nur Peers, die sich **explizit registriert** haben. Peers, die lokal per WLAN, QR, direkter URL oder fremdem Directory gefunden wurden, werden nicht automatisch weiterveröffentlicht.

## NAT und Vermittlung

Für NAT-Situationen gibt es einen kurzlebigen Rendezvous-Signalkanal. Er kann Verbindungsangebote bzw. erreichbare Endpunkte zwischen zwei Peers vermitteln. Er ist kein dauerhafter Datei-Relay.

TURN ist nicht Voraussetzung. TURN ist erst sinnvoll, wenn eine spätere WebRTC-/UDP-Verbindung echte NAT-Traversal-Unterstützung benötigt. Für normale SOFP-Verbindungen bleiben HTTPS/WebSocket-Rendezvous oder ein gezielter Relay-Dienst einfacher.

## Wichtige Umgebungsvariablen

- `SIMPLEOFFICE_FEDERATION_TOKEN` – bestehende Federation-Authentifizierung.
- `SIMPLEOFFICE_FEDERATION_PEER_ID` – stabile lokale Peer-ID.
- `SIMPLEOFFICE_FEDERATION_PUBLIC_URL` – von anderen Peers erreichbare URL für veröffentlichte Profile/QR. Ein sicherer Reverse-Proxy-Basispfad ist zulässig, z. B. `https://office.example/simpleoffice`. Für einen direkten LAN-Well-Known-Aufruf wird die tatsächlich angesprochene lokale Adresse verwendet, wenn keine Public-URL gesetzt ist.
- `SIMPLEOFFICE_FEDERATION_LABEL` – Anzeigename.
- `SIMPLEOFFICE_FEDERATION_COUNTRY` – ISO-3166 Alpha-2 Land.
- `SIMPLEOFFICE_FEDERATION_FINGERPRINT` – öffentlicher Fingerprint der Instanz/Identität.
- `SIMPLEOFFICE_FEDERATION_BOOTSTRAP_URLS` – kommaseparierte Directory-/Bootstrap-URLs.
- `SIMPLEOFFICE_FEDERATION_PUBLIC_DIRECTORY=1` – erlaubt öffentliche Leseabfragen des Directorys. Registrierung/Rendezvous bleiben authentifiziert.
- `SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS=1` – erlaubt bei Hostnamen/DNS bewusst private Ziele für LAN/VPN. Explizit eingegebene RFC1918-/RFC6598-IPv4-Literale benötigen dieses globale Opt-in nicht; Link-Local und reservierte Ziele bleiben gesperrt.
- `SIMPLEOFFICE_FEDERATION_ALLOW_LOOPBACK=1` – erlaubt Loopback-Ziele für lokale Entwicklung/Integrationstests. Standardmäßig ist Loopback gesperrt.
- `SIMPLEOFFICE_FEDERATION_LAN_ADDRESS` – optional eine oder mehrere kommaseparierte lokale IPv4-Adressen, falls die aktive Host-/LAN-Adresse nicht automatisch erkannt wird. In Containern ist das nicht automatisch die Docker-Bridge-Adresse, die andere Geräte erreichen können.
- `SIMPLEOFFICE_FEDERATION_LAN_PORTS` – optionale zusätzliche lokale SimpleOffice-Ports; maximal vier Ports werden geprüft.
- Die **Scan-Basis / CIDR** wird im Admin-UI pro Scan angegeben, wenn die automatische Netzerkennung wegen Docker/Podman, Routing oder VPN nicht zum gewünschten Netz führt.
- `SIMPLEOFFICE_FEDERATION_AUTOSCAN_COUNTRY=DE` – aktiviert automatisches Land-Discovery.
- `SIMPLEOFFICE_FEDERATION_AUTOSCAN_SECONDS` – Intervall, mindestens eine Stunde; Standard 21600 Sekunden.

## HTTP-Endpunkte

- `GET /.well-known/simpleoffice-federation`
- `GET /federation/v1/discovery/peers?country=DE`
- `POST /federation/v1/discovery/register`
- `GET /federation/v1/discovery/resolve?lookup=<sha256>`
- `POST /federation/v1/discovery/signal`
- `GET /federation/v1/discovery/signal?peer_id=<peer>`
- `GET /federation/v1/discovery/trust-claims`

## Admin

`/admin/federation/peer-discovery`

Dort gibt es als ersten Schnellweg **„Netzwerk scannen“**. Außerdem können Peers nach Land, URL/IP oder E-Mail gesucht, QR-Codes ausgetauscht, persönliche Prüfungen bestätigt, Trust-Level gesetzt und die Weitergabe jeder Trust-Beziehung einzeln festgelegt werden.

## Netzwerk-Scan im Detail

Der Scan ist ein lokaler Komfortmechanismus für LAN/WLAN, Container-Netze und ausdrücklich angegebene lokale/VPN-Netze:

1. Ohne Eingabe lokale private IPv4-Adresse bestimmen und das zugehörige `/24` bilden, z. B. `192.168.178.0/24`.
2. Bei Container-/VPN-Betrieb optional eine explizite Scan-Basis angeben, z. B. `192.168.178.0/24` oder `100.110.89.0/24`.
3. Nur den bzw. die konfigurierten SimpleOffice-Ports prüfen.
4. Nur den festen Federation-Well-Known-Pfad unter der jeweiligen Basis-URL abrufen.
5. Antwortprofil, Federation-Protokollbereich und Feature-Versionen validieren.
6. Ergebnis der Kompatibilitätsprüfung lokal speichern; der Peer bleibt deaktiviert, unverified und ohne Datenrechte.

Damit wird kein fremdes Internetnetz durchsucht und Link-Local/Cloud-Metadata-Ziele bleiben gesperrt.

## Resource Commander

Der zweigeteilte **Resource Commander** unter `/resource-commander` übernimmt das klassische Commander-Prinzip für lokale und föderierte Ressourcen:

- links und rechts kann jeweils ein Provider gewählt werden;
- vorhandene aktive Federation-Peers werden als eigene Provider angeboten;
- wenn mindestens ein Federation-Peer verfügbar ist, wird er beim Öffnen bevorzugt auf der rechten Seite ausgewählt;
- Verifikationsstatus und lokales Trust-Level werden direkt am Federation-Pane angezeigt;
- die Aktionsleiste berücksichtigt die effektiven Provider-Capabilities. Lesen, Schreiben, Ordneranlage und Löschen werden nicht nur optisch, sondern weiterhin serverseitig geprüft;
- providerübergreifendes „Verschieben“ bleibt absichtlich eine sichere Kopie. Die Quelle wird dabei nicht automatisch gelöscht;
- Federation-Rechte ergeben sich weiterhin aus der lokalen Dokument-Policy. Der angezeigte Trust-Status ersetzt keine Berechtigung;
- Remote-Zugriff auf die Commander-API verwendet weiterhin den separaten `SIMPLEOFFICE_RESOURCE_COMMANDER_TOKEN` bzw. peer-spezifische Commander-Credentials und nicht den allgemeinen Federation-Transport-Token.

Damit bleibt Discovery/Trust von konkreten Datei-Rechten getrennt, während der Bedienweg für lokale und föderierte Dateien einheitlich ist.

## Sicherheitsregeln

- Discovery vergibt keine Datenrechte.
- Neue Discovery-Peers sind deaktiviert.
- LAN-Discovery ist benutzerinitiiert. Automatisch werden nur lokale RFC1918-`/24` gescannt; ausdrücklich angegebene RFC1918/RFC6598-Netze sind auf vier Netze, mindestens `/22` und 1024 Hosts begrenzt.
- Direkte Discovery rekonstruiert eine kanonische Server-Basis-URL mit optionalem sicheren Reverse-Proxy-Basispfad und ruft ausschließlich den festen Well-Known-Pfad darunter ab.
- Loopback, Link-Local, Multicast, unspezifizierte und reservierte Netzwerkziele sind standardmäßig gesperrt; Link-Local-Ziele wie Cloud-Metadata-Endpunkte bleiben auch beim WLAN-Scan gesperrt.
- Private RFC1918-/ULA-Ziele sind für manuelle direkte Discovery nur nach explizitem serverseitigem Opt-in zugelassen.
- `DIRECT_ONLY` wird nie als Trust-Claim exportiert.
- E-Mail-Lookups übertragen keine Klartext-E-Mail.
- Rendezvous-Nachrichten sind kurzlebig und werden nur einmal ausgeliefert.
- Trust-Hinweise anderer Peers führen niemals automatisch zu lokalem Trust.
- Fingerprints müssen bei persönlicher Prüfung außerhalb des automatischen Discovery-Pfads verglichen werden.
