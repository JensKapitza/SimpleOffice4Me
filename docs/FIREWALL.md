# Mini Services: Linux-Firewall

## Zweck

Unter **Administration → Mini Services → Firewall** liest SimpleOffice auf Linux
die vorhandene Host-Firewall aus. Unterstützte schreibende Manager sind UFW und
firewalld. nftables wird als vorhandenes Werkzeug angezeigt, aber nicht parallel
zu UFW/firewalld als zweiter Regelmanager verändert.

Die Funktion installiert und aktiviert keine Firewall automatisch. Ist kein
unterstützter Manager aktiv, bleibt die Ansicht lesend und Schreibaktionen sind
gesperrt. Sind UFW und firewalld gleichzeitig aktiv, gilt der Zustand als
Konflikt und Änderungen sind ebenfalls gesperrt.

## Rechtearchitektur

Der Flask-Webprozess bleibt unprivilegiert. Er führt weder `sudo`,
`ufw`, `firewall-cmd` noch frei formulierte Systembefehle aus.

Schreibaktionen laufen in vier Schritten:

1. Die Admin-API validiert ausschließlich strukturierte Regelparameter.
2. Der vorhandene Mini-Services-Worker übernimmt den Auftrag aus seiner privaten
   SQLite-Steuerung.
3. Der Worker verbindet sich über
   `/run/simpleoffice4me/firewall.sock` mit dem root-seitigen Firewall-Agenten.
4. Der Agent akzeptiert nur die fest implementierten Aktionen
   `snapshot`, `test`, `confirm` und `rollback`.

Nur der Mini-Services-systemd-Dienst erhält die zusätzliche Gruppe
`simpleoffice-firewall`. Der Webdienst läuft zwar ebenfalls als Benutzer
`simpleoffice`, erhält diese Zusatzgruppe aber nicht.

## 20-Sekunden-Test und Rollback

Jede Regeländerung ist zunächst ein Test. Vor der ersten Änderung schreibt der
Agent einen privaten Rollback-Plan und startet einen unabhängigen Watchdog.

Ablauf:

1. aktuellen Firewallzustand lesen,
2. Rollback-Plan schreiben,
3. Watchdog scharf schalten,
4. Testregel anwenden,
5. 20 Sekunden auf Bestätigung warten,
6. bei Bestätigung dauerhaft übernehmen,
7. ohne Bestätigung automatisch zurückrollen.

Unter systemd wird bevorzugt ein transienter Timer verwendet. Falls dieser Pfad
nicht verfügbar ist, läuft ein vom Agenten abgekoppelter Root-Prozess als
Watchdog. Ein Neustart von Flask oder des Mini-Services-Workers beendet den
Rollback deshalb nicht. Beim Start des Agenten werden offene Pläne erneut
geprüft. Vor einer Paketentfernung werden alle noch offenen Tests sofort
zurückgerollt.

### UFW

UFW besitzt keinen getrennten Runtime-/Permanent-Modus. Testregeln werden deshalb
mit `simpleoffice-test-<id>` markiert und an Position 1 eingefügt, damit der Test
gegenüber einer bereits vorhandenen Gegenregel eindeutig wirkt. Beim Rollback
wird die aktuelle nummerierte Regelliste erneut gelesen. Eine Regelnummer wird
nur dann gelöscht, wenn dieselbe aktuelle Zeile exakt die erwartete Testkennung
trägt. Fremde Regeln werden nicht anhand einer gespeicherten Nummer entfernt.

Nach Bestätigung wird eine separat markierte dauerhafte Regel eingefügt und die
Testregel entfernt.

### firewalld

firewalld-Testregeln werden nur in der Runtime-Konfiguration angelegt. Erst die
Bestätigung erzeugt die entsprechende Permanent-Regel. Bei mehreren aktiven
Zonen muss die Zielzone ausdrücklich gewählt werden.

Freigaben verwenden normale Portregeln. Testweise Sperren verwenden eine
eindeutige Rich Rule mit negativer Priorität, damit eine vorhandene Freigabe
nicht versehentlich Vorrang erhält.

## Dienst- und Portdiagnose

Die Ansicht erzeugt ihre Portmatrix aus den tatsächlich gespeicherten
Konfigurationen:

- DHCP: konfigurierter UDP-Port,
- DNS: konfigurierter UDP- und TCP-Port,
- TFTP/PXE: konfigurierter UDP-Listener,
- SIP: Registrar-Port und Transport,
- RTP/RTCP-Receiver: gespeichertes Portpaar,
- Connectivity Relay: TURN, optional TURN/TLS und Relay-Portbereich,
- DLNA/Media Renderer: konfigurierter TCP-Port,
- SimpleOffice Web / HTTP-PXE: tatsächlicher Web-Port.

Zusätzlich wird `ss -H -lntu` verwendet, sofern verfügbar. Dadurch werden zwei
verschiedene Fragen getrennt dargestellt:

- **lauscht der Dienst tatsächlich?**
- **würde die Host-Firewall diesen Port nach der lesbaren Regelkonfiguration
  zulassen oder sperren?**

Ein offener Firewall-Port beweist nicht, dass ein Dienst lauscht. Umgekehrt kann
ein Listener durch die Firewall blockiert sein.

Nur an Loopback gebundene Dienste werden als **local-only** markiert und erhalten
keine unnötige LAN-Freigabe.

## Aussperrschutz

Der aktuelle SimpleOffice-Webport und der als kritisch markierte
Web-/HTTP-PXE-Port können aus der Remote-Weboberfläche nicht mit einer Deny-Regel
gesperrt werden. Dafür ist eine lokale Konsole erforderlich.

Der 20-Sekunden-Rollback schützt andere Firewalländerungen zusätzlich. Er ersetzt
keine externe Erreichbarkeitsprüfung: Router, NAT, VLANs, Cloud-Firewalls und
weitere ACLs liegen außerhalb der Host-Firewall und können weiterhin blockieren.

## API

Alle Endpunkte benötigen Anmeldung und Administratorrechte. Schreibende
Browserzugriffe unterliegen zusätzlich dem vorhandenen CSRF-Schutz.

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/mini-services/firewall` | Cache, Backend, Regeln, Listener und Dienste |
| POST | `/api/mini-services/firewall/refresh` | privilegierten Snapshot über Worker anfordern |
| GET | `/api/mini-services/firewall/operations/<id>` | Status eines Firewall-Auftrags |
| POST | `/api/mini-services/firewall/test` | 20-Sekunden-Test starten |
| POST | `/api/mini-services/firewall/test/<id>/confirm` | Test dauerhaft bestätigen |
| POST | `/api/mini-services/firewall/test/<id>/rollback` | sofort zurückrollen |

## Praktische Abnahme

Unit-Tests können Parsing, Validierung, Priorität, Konfliktbehandlung,
Watchdog-Reihenfolge und Packaging absichern. Vor einer Produktionsfreigabe
bleiben reale Tests auf mindestens je einem UFW- und firewalld-System sinnvoll,
einschließlich:

- Öffnen und Sperren eines unkritischen TCP-/UDP-Testports,
- Ablauf ohne Bestätigung,
- Bestätigung vor Ablauf,
- Neustart von Web und Worker während des Tests,
- Agent-Neustart mit offenem Testplan,
- Paketentfernung mit offenem Test,
- mehreren firewalld-Zonen,
- vorhandenen fremden Regeln vor und hinter der SimpleOffice-Regel.
