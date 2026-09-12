# Federation Discovery und Vertrauen

SimpleOffice4Me trennt vier Dinge strikt voneinander:

1. **Discovery**: Ein Peer wurde gefunden.
2. **Prüfung**: Die Identität/Fingerprint wurde geprüft.
3. **Vertrauen**: Diese Instanz entscheidet lokal, ob sie dem Peer vertraut.
4. **Berechtigungen**: Dokumente, Kontakte, Kalender, Aufgaben usw. bleiben in der bestehenden Peer-Policy separat freizugeben.

Ein gefundener Peer wird deshalb standardmäßig als **bekannt, aber nicht geprüft** gespeichert und nicht automatisch aktiviert.

## Discovery-Wege

- **WLAN/LAN**: Benutzerstart über „WLAN scannen“. Es werden ausschließlich private RFC1918-IPv4-Adressen im lokalen `/24` geprüft, standardmäßig auf dem SimpleOffice-Port 8080. Abgerufen wird nur `/.well-known/simpleoffice-federation`. Gefundene Peers werden deaktiviert als `KNOWN_UNVERIFIED` gespeichert.
- **Land**: Abfrage konfigurierter Bootstrap-/Directory-Peers mit ISO-Ländercode, z. B. `DE`.
- **IP / Hostname / URL**: Direkter Abruf von `/.well-known/simpleoffice-federation`. Als Eingabe wird nur eine Server-Basis-URL akzeptiert; Pfad, Query, Fragment und eingebettete Zugangsdaten sind unzulässig.
- **E-Mail**: Die normalisierte E-Mail wird lokal mit SHA-256 gehasht. Nur der Hash wird als Rendezvous-Lookup übertragen.
- **QR-Code**: Das öffentliche Peer-Profil wird als `sofp://peer/...` ausgetauscht. QR-Import bedeutet zunächst nur `KNOWN_UNVERIFIED`.

Es findet bewusst **kein flächendeckender Internet-Portscan** statt. Der WLAN-Scan bleibt auf das lokale `/24` begrenzt. Land-Discovery läuft über Bootstrap-/Directory-Knoten.

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
- `SIMPLEOFFICE_FEDERATION_PUBLIC_URL` – von anderen Peers erreichbare URL für veröffentlichte Profile/QR. Für einen direkten WLAN-Well-Known-Aufruf wird die tatsächlich angesprochene lokale Adresse verwendet, wenn keine Public-URL gesetzt ist.
- `SIMPLEOFFICE_FEDERATION_LABEL` – Anzeigename.
- `SIMPLEOFFICE_FEDERATION_COUNTRY` – ISO-3166 Alpha-2 Land.
- `SIMPLEOFFICE_FEDERATION_FINGERPRINT` – öffentlicher Fingerprint der Instanz/Identität.
- `SIMPLEOFFICE_FEDERATION_BOOTSTRAP_URLS` – kommaseparierte Directory-/Bootstrap-URLs.
- `SIMPLEOFFICE_FEDERATION_PUBLIC_DIRECTORY=1` – erlaubt öffentliche Leseabfragen des Directorys. Registrierung/Rendezvous bleiben authentifiziert.
- `SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS=1` – erlaubt bei der manuellen direkten Discovery bewusst RFC1918-/ULA-Ziele für LAN/VPN. Der spezielle WLAN-Scan benötigt dieses globale Opt-in nicht, weil er intern auf RFC1918 + lokales `/24` + festen Well-Known-Pfad begrenzt ist.
- `SIMPLEOFFICE_FEDERATION_ALLOW_LOOPBACK=1` – erlaubt Loopback-Ziele für lokale Entwicklung/Integrationstests. Standardmäßig ist Loopback gesperrt.
- `SIMPLEOFFICE_FEDERATION_LAN_ADDRESS` – optional eine oder mehrere kommaseparierte lokale IPv4-Adressen, falls die aktive WLAN-Adresse nicht automatisch erkannt wird.
- `SIMPLEOFFICE_FEDERATION_LAN_PORTS` – optionale zusätzliche lokale SimpleOffice-Ports; maximal vier Ports werden geprüft.
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

Dort gibt es als ersten Schnellweg **„Geräte im WLAN suchen“**. Außerdem können Peers nach Land, URL/IP oder E-Mail gesucht, QR-Codes ausgetauscht, persönliche Prüfungen bestätigt, Trust-Level gesetzt und die Weitergabe jeder Trust-Beziehung einzeln festgelegt werden.

## WLAN-Scan im Detail

Der Scan ist ausdrücklich ein lokaler Komfortmechanismus für z. B. Handy → Handy im selben WLAN:

1. Lokale private IPv4-Adresse bestimmen.
2. Das zugehörige `/24` bilden, z. B. `192.168.178.0/24`.
3. Nur den bzw. die konfigurierten SimpleOffice-Ports prüfen.
4. Nur `/.well-known/simpleoffice-federation` abrufen.
5. Antwortprofil validieren und unter der tatsächlich erreichbaren LAN-URL speichern.
6. Peer bleibt deaktiviert, unverified und ohne Datenrechte.

Damit wird kein fremdes Internetnetz durchsucht und Link-Local/Cloud-Metadata-Ziele bleiben gesperrt.

## Sicherheitsregeln

- Discovery vergibt keine Datenrechte.
- Neue Discovery-Peers sind deaktiviert.
- WLAN-Discovery ist benutzerinitiiert und auf RFC1918 + lokales `/24` begrenzt.
- Direkte Discovery rekonstruiert eine kanonische Server-Basis-URL und ruft ausschließlich den festen Well-Known-Pfad ab.
- Loopback, Link-Local, Multicast, unspezifizierte und reservierte Netzwerkziele sind standardmäßig gesperrt; Link-Local-Ziele wie Cloud-Metadata-Endpunkte bleiben auch beim WLAN-Scan gesperrt.
- Private RFC1918-/ULA-Ziele sind für manuelle direkte Discovery nur nach explizitem serverseitigem Opt-in zugelassen.
- `DIRECT_ONLY` wird nie als Trust-Claim exportiert.
- E-Mail-Lookups übertragen keine Klartext-E-Mail.
- Rendezvous-Nachrichten sind kurzlebig und werden nur einmal ausgeliefert.
- Trust-Hinweise anderer Peers führen niemals automatisch zu lokalem Trust.
- Fingerprints müssen bei persönlicher Prüfung außerhalb des automatischen Discovery-Pfads verglichen werden.
