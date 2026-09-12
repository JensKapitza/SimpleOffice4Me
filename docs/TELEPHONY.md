# SimpleOffice4Me Telefonie

## Ziel

SimpleOffice4Me verwendet fuer lokale Telefonie Standard-SIP. Eine Nebenstelle soll deshalb sowohl mit einem Softphone als auch mit einem normalen SIP-Tischtelefon verwendet werden koennen.

Die privaten SimpleOffice-Nummern sind keine oeffentlichen PSTN- oder E.164-Rufnummern. Eine lokale Nebenstelle wie `101` bleibt lokal; Federation-Rufnummern werden getrennt geroutet.

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

Linphone unterstuetzt auch Provisioning-Links und QR-Code-Einrichtung. SimpleOffice stellt erst dann einen Linphone-spezifischen QR-Code bereit, wenn das Provisioning-Format serverseitig vollstaendig implementiert und getestet ist. Bis dahin werden die wenigen Standard-SIP-Felder direkt angezeigt.

## Einrichtung in SimpleOffice

1. Als Administrator **Mini Services -> IP-Telefonie** oeffnen.
2. Unter **SIP-Serverdaten** einen Namen oder eine IP eintragen, die das Telefon im lokalen Netz erreichen kann, z. B. `pbx.home.arpa` oder `192.168.178.20`.
3. Fuer den ersten lokalen Test `UDP` und Port `5060` verwenden. Spaeter sollte fuer nicht vertrauenswuerdige Netze SIP ueber TLS und verschluesseltes Media verwendet werden.
4. **Telefon hinzufuegen** waehlen.
5. Eine freie Nebenstelle eintragen, z. B. `101`.
6. Namen und Geraetetyp auswaehlen.
7. Das erzeugte Passwort sofort in das Telefon uebernehmen. Es wird verschluesselt gespeichert und in der Oberflaeche nicht dauerhaft im Klartext angezeigt.
8. Falls das Passwort verloren geht, **Passwort neu** verwenden und das neue Passwort im Telefon eintragen.

## Linphone auf Android/iPhone

1. Linphone aus Play Store beziehungsweise App Store installieren.
2. Linphone starten.
3. **Third-Party SIP Account** waehlen.
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

## Linphone auf Windows/Linux/macOS

Die Daten sind identisch mit der mobilen Einrichtung:

1. Linphone installieren.
2. **Third-Party SIP Account** auswaehlen.
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

## Sicherheit

- SIP-Passwoerter werden nicht im Klartext in der SimpleOffice-Datenbank abgelegt.
- Ein Passwort wird beim Anlegen oder Rotieren einmalig angezeigt.
- Passwoerter niemals in Audit-Logs schreiben.
- Administration bleibt CSRF- und Login-geschuetzt.
- SIP ueber das Internet nicht einfach per offenem UDP-Port veroeffentlichen.
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
10. getestetes Linphone-Provisioning per Link/QR-Code
