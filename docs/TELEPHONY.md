# SimpleOffice4Me Telefonie

## Ziel

SimpleOffice4Me verwendet fuer lokale Telefonie Standard-SIP. Eine Nebenstelle soll deshalb sowohl mit einem Softphone als auch mit einem normalen SIP-Tischtelefon verwendet werden koennen.

Die privaten SimpleOffice-Nummern sind keine oeffentlichen PSTN- oder E.164-Rufnummern. Eine lokale Nebenstelle wie `101` bleibt lokal; Federation-Rufnummern werden getrennt geroutet.

Die Bedienoberflaeche soll ohne separates Handbuch funktionieren. Telefonie-Felder erhalten deshalb direkt an der Beschriftung eine `?`-Hilfe mit kurzer Erklaerung, typischem Beispiel und Hinweis, wann der Wert normalerweise unveraendert bleiben kann.

## Empfohlener Client: Linphone

Als Standard-Softphone wird **Linphone** empfohlen:

- Android und iOS
- Windows, macOS und GNU/Linux
- Standard-SIP und Konten von Drittanbietern
- Audio- und Videoanrufe
- Open-Source-Client und damit kein Zwang zu einem bestimmten Telefonieanbieter

Download: https://www.linphone.org/en/download/

Offizielle Anleitung fuer Drittanbieter-SIP-Konten:
https://www.linphone.org/en/docs/login-sip-account/

Offizielle Anleitung fuer Provisioning-Link/QR-Code:
https://www.linphone.org/en/docs/login-with-qrcode/

Linphone unterstuetzt Remote-Provisioning per HTTP(S)-URL sowie auf Mobilgeraeten per QR-Code. Die URL verweist auf eine XML-Konfiguration, die Linphone herunterlaedt und anwendet.

## Remote-Provisioning: Sicherheitsmodell

SimpleOffice soll die Remote-Einrichtung so weit wie moeglich automatisieren, ohne SIP-Zugangsdaten ueber einen ungeschuetzten oeffentlichen Endpoint auszuliefern.

Vorgesehener Ablauf:

1. Administrator waehlt eine Nebenstelle aus.
2. SimpleOffice erzeugt einen kurzlebigen Provisioning-Link bzw. QR-Code.
3. Linphone uebernimmt Server, Port, Transport, Realm, STUN und Nebenstelle automatisch.
4. Das SIP-Passwort bleibt getrennt und wird nur einmalig in der geschuetzten SimpleOffice-Oberflaeche angezeigt.
5. Der Provisioning-Link laeuft automatisch ab und kann widerrufen werden.
6. Fuer eine spaetere vollautomatische Uebergabe des Passworts wird nur ein dafuer vorgesehener, getesteter Provisioning-Dienst verwendet; kein allgemeiner oeffentlicher SimpleOffice-Endpunkt.

Das ist absichtlich konservativer als ein Link, der alle Zugangsdaten direkt enthaelt. Die QR-Einrichtung bleibt dadurch einfach, waehrend ein abgefangener Link nicht automatisch das dauerhafte SIP-Passwort preisgibt.

## Einrichtung in SimpleOffice

1. Als Administrator **Mini Services -> IP-Telefonie** oeffnen.
2. Unter **SIP-Serverdaten** einen Namen oder eine IP eintragen, die das Telefon im lokalen Netz erreichen kann, z. B. `pbx.home.arpa` oder `192.168.178.20`.
3. Fuer den ersten lokalen Test `UDP` und Port `5060` verwenden. Spaeter sollte fuer nicht vertrauenswuerdige Netze SIP ueber TLS und verschluesseltes Media verwendet werden.
4. **Telefon hinzufuegen** waehlen.
5. Eine freie Nebenstelle eintragen, z. B. `101`.
6. Namen und Geraetetyp auswaehlen.
7. Das erzeugte Passwort sofort in das Telefon uebernehmen. Es wird verschluesselt gespeichert und in der Oberflaeche nicht dauerhaft im Klartext angezeigt.
8. Falls ein Feld unklar ist, die direkt daneben stehende `?`-Hilfe oeffnen.
9. Falls das Passwort verloren geht, **Passwort neu** verwenden und das neue Passwort im Telefon eintragen.

## Linphone auf Android/iPhone

1. Linphone aus Play Store beziehungsweise App Store installieren.
2. Linphone starten.
3. Fuer die manuelle Einrichtung **Third-Party SIP Account** waehlen.
4. Die Daten aus SimpleOffice uebernehmen:

| Linphone | SimpleOffice |
| --- | --- |
| Username | Nebenstelle, z. B. `101` |
| Auth username / User ID | ebenfalls die Nebenstelle |
| Password | einmalig angezeigtes SIP-Passwort |
| Domain / SIP server | SIP-Server aus Mini Services |
| Transport | UDP/TCP/TLS wie in SimpleOffice |
| Port | SIP-Port aus SimpleOffice |

5. Konto speichern.
6. Nach erfolgreicher Registrierung eine andere lokale Nebenstelle anrufen, z. B. `102`.

Fuer Remote-Provisioning waehlt man in Linphone **Provisioning Link** bzw. **Scan QR Code**. SimpleOffice wird dafuer nur kurzlebige, widerrufbare Links verwenden.

## Linphone auf Windows/Linux/macOS

Die Daten sind identisch mit der mobilen Einrichtung:

1. Linphone installieren.
2. **Third-Party SIP Account** auswaehlen oder spaeter den Provisioning-Link verwenden.
3. Nebenstelle als Username/Auth-ID verwenden.
4. SIP-Server, Port und Transport aus SimpleOffice uebernehmen.
5. SIP-Passwort eintragen.
6. Registrierung pruefen und einen internen Testanruf durchfuehren.

Damit muessen fuer PC und Handy keine unterschiedlichen Clients dokumentiert oder gepflegt werden.

## Normales IP-Tischtelefon

Bei Yealink, Snom, Grandstream und anderen SIP-Telefonen unterscheiden sich nur die Feldnamen. Typische Zuordnung:

| Telefonfeld | Wert |
| --- | --- |
| Account / Line | beliebiger Anzeigename |
| SIP User / Extension | SimpleOffice-Nebenstelle |
| Authentication ID | SimpleOffice-Nebenstelle |
| Password | erzeugtes SIP-Passwort |
| Registrar / SIP Server / Domain | SimpleOffice-SIP-Server |
| Registrar Port | konfigurierter SIP-Port |
| Transport | UDP, TCP oder TLS |
| Outbound Proxy | leer, solange SimpleOffice keinen separaten Proxy vorgibt |
| STUN | nur falls in SimpleOffice angegeben/benoetigt |

## Nummernplan

Beispiel:

- `101` - Jens PC
- `102` - Jens Handy
- `110` - Buero-Tischtelefon
- `120` - Tuertelefon

Eine Person kann mehrere Nebenstellen besitzen. Spaeter kann eine Rufgruppe wie `100` mehrere Geraete gleichzeitig klingeln lassen.

Die Federation arbeitet zusaetzlich mit einer Server-Basisnummer und einer Master-Vorwahl. Beispiel:

- lokale Nebenstelle: `101`
- Server-Basisnummer: `100`
- private Master-Vorwahl: `49`
- private Federation-Darstellung: `+49 100 101`

Diese Darstellung ist absichtlich eine private Federation-Adresse und keine Behauptung, dass `+49 100 101` eine oeffentliche Telefonnummer ist.

## UI-Regel fuer Formularfelder

Neue oder ueberarbeitete SimpleOffice-Formulare sollen folgende Regel einhalten:

- jedes Eingabefeld hat eine sichtbare, eindeutige Beschriftung;
- direkt an der Beschriftung steht eine kurze `?`-Hilfe;
- die Hilfe erklaert **was** der Wert ist, **wann** er benoetigt wird und nennt wenn sinnvoll ein Beispiel;
- Pflichtfelder sind als solche erkennbar;
- technische Begriffe wie Realm, STUN, Transport, Port oder Federation werden nicht ohne Erklaerung gezeigt;
- sensible Werte werden in Hilfetexten nie wiederholt;
- die normale Bedienung darf kein separates Handbuch voraussetzen.

Die Telefonie-Seite setzt dieses Muster bereits um. Bestehende Formulare sollen schrittweise auf dieselbe Konvention umgestellt werden, wobei gemeinsame Komponenten statt einzelner Sonderloesungen bevorzugt werden.

## Sicherheit

- SIP-Passwoerter werden nicht im Klartext in der SimpleOffice-Datenbank abgelegt.
- Ein Passwort wird beim Anlegen oder Rotieren einmalig angezeigt.
- Passwoerter niemals in Audit-Logs schreiben.
- Administration bleibt CSRF- und Login-geschuetzt.
- SIP ueber das Internet nicht einfach per offenem UDP-Port veroeffentlichen.
- Remote-Provisioning-Links sind kurzlebig, widerrufbar und duerfen keine dauerhaft wiederverwendbaren Zugangsdaten in der URL enthalten.
- Fuer externe Nutzung sind TLS, SRTP, Rate-Limits und ein sauber konfigurierter STUN/TURN-Pfad vorgesehen.

## Aktueller Implementierungsstand

Der Einrichtungsassistent verwaltet bereits:

- SIP-Serverdaten
- Nebenstellen
- Geraetetypen
- zufaellige starke SIP-Zugangsdaten
- verschluesselte Ablage der Zugangsdaten
- Passwortrotation
- Linphone- und generische Telefonkonfiguration
- direkte `?`-Erklaerungen fuer die Telefonie-Felder

Der eigentliche interne SIP-Registrar/Proxy ist ein separater Runtime-Baustein. Solange dieser Dienst nicht aktiv ist, zeigt die Oberflaeche deutlich an, dass die Konten vorbereitet sind, aber sich noch nicht registrieren koennen.

## Geplanter Ausbau

1. SIP-Registrar fuer `REGISTER`
2. Digest-Authentifizierung
3. interne Anrufe zwischen Nebenstellen
4. Registrierungsstatus / letzte Registrierung / User-Agent in der UI
5. SIP-DNS-SRV und DHCP Option 120
6. TLS und SRTP
7. Rufgruppen und mehrere Endgeraete pro Benutzer
8. STUN/TURN fuer entfernte Clients
9. Federation-Routing zwischen SimpleOffice-Installationen
10. sicherer Linphone-Provisioning-Dienst fuer Link/QR-Code
11. gemeinsame Formular-Hilfe-Komponente fuer die restlichen Anwendungsbereiche
