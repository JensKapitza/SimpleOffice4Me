# Desktop-LAN-Proxy und Bildschirm-Viewer (Entwurf)

## Startverhalten

Die Desktop-EXE lässt das eingebettete Waitress-Backend auf `127.0.0.1` und einem
dynamischen Port. Beim EXE-Start wird zusätzlich die persistierte Sektion
`server` aus `<Dokumentordner>/.simpleoffice-meta/settings.json` gelesen:

- `host=127.0.0.1` (oder `localhost`, `::1`): kein LAN-Proxy.
- `host=192.168.x.y` oder `0.0.0.0` und `port=8081`: opt-in LAN-Proxy.
- Der Proxy wird nach dem Backend gestartet und beim EXE-Ende geschlossen.
- Ein Änderung von Host/Port erfordert einen vollständigen EXE-Neustart.
- Belegter Proxy-Port oder ungültige Einstellung: Fehler im Electron-Log,
  kein verdeckter Wechsel auf einen anderen Port.

Beispiel: Backend `127.0.0.1:49152`, Proxy `192.168.1.50:8081`.
Alle HTTP-Methoden und Pfade werden an das Backend weitergeleitet.
Die Anmeldung und CSRF-Prüfung des Backends bleiben erforderlich.
Es werden weder HTTP-Upgrades noch WebSocket-Verbindungen weitergeleitet.

## Bildschirm

Beim Start der Bildschirmfreigabe wird zusätzlich ein Zuschauer-Link
`/screen/view/<Verbindungscode>` angeboten. Die Seite wählt die
Empfängerrolle automatisch und nutzt denselben WebRTC-Signalisierungskanal.

- Der lokale Link verwendet den aktuellen Browser-Ursprung.
- Der LAN-Link wird nur angezeigt, wenn die Desktop-Konfiguration einen
  Proxy-Ursprung ermitteln kann.
- **Beide Links erfordern derzeit eine SimpleOffice-Anmeldung.**
- Der Verbindungscode ist nur für die aktuelle Sitzung gültig.
- Nach Ende der Sitzung wird der Link ungültig.
- Mehrere parallele Empfänger sind mit dem derzeitigen WebRTC-Sender nicht
  zuverlässig unterstützt: eigenes Follow-up erforderlich.

## Sicherheit / Einschränkungen

Ein HTTP-LAN-Proxy ist **keine** TLS-Terminierung. Keine Router-Portfreigabe
ins Internet einrichten. Für öffentliche Zugriffe TLS, Zugriffskontrolle und
die vorhandenen TURN-Optionen berücksichtigen. Browser-Bildschirmaufnahme
benötigt i. d. R. HTTPS oder einen lokalen, vertrauenswürdigen Kontext.

Der neue Proxy läuft im Electron-Hauptprozess, **noch nicht** als registrierter
Mini-Services-Worker. Statusanzeige, Start/Stop in Mini-Services,
TLS-Integration, Gastlinks und mehrere Zuschauer sind noch separat umzusetzen.

## Prüfpunkte vor Merge

1. Windows-EXE: Host/Port speichern, EXE komplett beenden und neu starten.
2. Proxy in demselben LAN von einem anderen Gerät aufrufen.
3. Sicherstellen, dass bei Standardwerten kein LAN-Port geöffnet wird.
4. Bildschirmfreigabe auf Desktop, Empfang im zweiten Browser mit Login.
5. Portkonflikt, mehrere Netzwerkschnittstellen, Firewall und Neustart.
6. Node-Tests: `node --test tests/desktop_lan_proxy.test.cjs`.
7. Python- und Browser-Screen-Share-Tests / CI.

## Mini-Services-Status (Desktop-Variante)

Der Electron-Prozess schreibt alle fünf Sekunden einen atomar erneuerten
Laufzeitstatus in `.simpleoffice-meta/desktop-proxy-status.json`.
Die Servereinstellungen und die Mini-Services-Übersicht zeigen diesen an.
Ein Status älter als 20 Sekunden wird als `stale` gekennzeichnet, nicht als
laufender Proxy. Zustände: `running`, `disabled`, `error`, `stopped`,
`stale`, `unknown`.

Der Listener akzeptiert nur Loopback und private LAN-Quelladressen.
Er ersetzt **keine** Verschlüsselung. Die Windows-Firewall muss den
konfigurierten Port freigeben; keine pauschalen Portfreigaben im Router.
Mini-Services bietet derzeit die Statusanzeige und verweist zur
Host-/Port-Konfiguration; Live-Start/-Stop erfolgt ausschließlich über den
EXE-Prozess, um parallele Listener zu vermeiden.

Weitere Prüfung: Desktop-Neustart, Statusalterung nach hartem Prozessabbruch,
Zugriff über einen zweiten Client, ausschließlich lokal gebundene Standardeinstellung
und WebRTC-Browserkompatibilität.
