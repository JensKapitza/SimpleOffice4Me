# Produktionsbetrieb mit Waitress

## Zweck und Nutzen

`start.sh`, `start.command` und `start.bat` starten SimpleOffice4Me mit
**Waitress** als produktionsgeeignetem WSGI-Server. Flask bleibt das
Webframework, aber sein nur für Entwicklung vorgesehener Server wird nicht
mehr verwendet. Damit entfällt die Meldung
`This is a development server. Do not use it in a production deployment.`
nicht durch Ausblenden, sondern durch den tatsächlichen Serverwechsel.

Waitress ist ein reiner Python-WSGI-Server, läuft auch unter Windows und
benötigt weder Compiler noch einen zusätzlichen Systemdienst. Die Umsetzung
folgt der [offiziellen Flask-Empfehlung für Waitress](https://flask.palletsprojects.com/en/stable/deploying/waitress/)
und der Python-WSGI-Spezifikation [PEP 3333](https://peps.python.org/pep-3333/).

## Installation und Bedienung

Ein normaler Start genügt:

```bash
./start.sh
```

Der Starter aktualisiert die lokale `.venv`, installiert dadurch auch
Waitress und startet anschließend den Einrichtungsassistenten beziehungsweise
die vorhandene Installation. Standardmäßig bleibt der Server ausschließlich
unter `127.0.0.1` erreichbar.

Beispiel für einen Rechner im internen Netz:

```bash
./start.sh --host 0.0.0.0 --port 8080 --threads 8
```

`0.0.0.0` ist nur eine Bind-Adresse. Im Browser wird die tatsächliche IP oder
der DNS-Name des Rechners verwendet. Ein öffentlicher Betrieb benötigt
weiterhin einen HTTPS-Reverse-Proxy.

## Konfiguration

| Startoption | Umgebungsvariable | Standard | Zulässiger Bereich |
|---|---|---:|---:|
| `--host ADRESSE` | `SIMPLEOFFICE_HOST` | Wert der Ersteinrichtung, gewöhnlich `127.0.0.1` | nicht leer, keine Leerzeichen |
| `--port PORT` | `SIMPLEOFFICE_PORT` | Wert der Ersteinrichtung, gewöhnlich `8080` | 1–65535 |
| `--threads ANZAHL` | `SIMPLEOFFICE_WSGI_THREADS` | 4 | 1–64 |
| `--channel-timeout SEKUNDEN` | `SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT` | 120 | 10–3600 |

Die Thread- und Timeout-Werte entsprechen den dokumentierten
[Waitress-Serverargumenten](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html#arguments-to-waitress-serve).
Ungültige Werte beenden den Start mit einer verständlichen Fehlermeldung,
anstatt unbemerkt einen anderen Wert zu verwenden.

Die maximale von Waitress angenommene HTTP-Anfrage wird automatisch an
`SIMPLEOFFICE_MAX_UPLOAD_MIB` beziehungsweise Flasks `MAX_CONTENT_LENGTH`
angeglichen. Damit widersprechen sich Proxy-, WSGI- und Anwendungsgrenze nicht
innerhalb von SimpleOffice4Me.

## Voraussetzungen

- Python 3.10 oder neuer,
- ein freier TCP-Port,
- Schreibzugriff auf Projekt-, Instanz- und Dokumentordner,
- für öffentliche Erreichbarkeit ein vorgeschalteter TLS-Proxy wie Caddy,
  nginx oder Traefik.

Waitress wird über `pyproject.toml` installiert. Zugangsdaten oder Zertifikate
werden nicht in das Repository geschrieben.

## Sicherheit, Datenschutz und Rechte

- Der sichere Standard bleibt `127.0.0.1`; keine Netzfreigabe erfolgt
  automatisch.
- Flask-Debugmodus und Browser-Tracebacks bleiben deaktiviert.
- Ohne konfigurierte Proxy-Kette entfernt Waitress nicht vertrauenswürdige
  `X-Forwarded-*`-Header.
- Bei `SIMPLEOFFICE_TRUSTED_PROXY_HOPS` bleiben die Header bis zur bestehenden
  `ProxyFix`-Prüfung erhalten. Dann muss der Anwendungsport per Firewall oder
  lokaler Bind-Adresse ausschließlich für den Proxy erreichbar sein.
- Waitress stellt selbst kein TLS bereit. Passwörter, CardDAV und Freigabelinks
  dürfen über ein Netzwerk nur per HTTPS-Proxy angeboten werden.
- Dokument-, Kalender-, Kontakt- und Benutzerrechte werden nicht verändert.
  Der WSGI-Server entscheidet nicht über fachliche Berechtigungen.

Der Prozess sollte nicht als `root` laufen. Für Port 80 oder 443 übernimmt der
Reverse-Proxy die privilegierte Bindung und leitet intern beispielsweise an
`127.0.0.1:8080` weiter.

## Protokoll- und Formatkompatibilität

Flask stellt weiterhin dieselbe WSGI-Anwendung nach PEP 3333 bereit. HTTP,
WebDAV, CardDAV, CalDAV, ICS, Uploads und Downloads behalten ihre URLs und
Formate. Der Wechsel erfordert keine Änderung in Thunderbird, LibreOffice oder
im Browser.

Waitress arbeitet mit einem Prozess und mehreren Threads. Eingehende
Request-Daten werden gepuffert; Request-Streaming wird laut Flask-Dokumentation
nicht unterstützt. Große Uploads können deshalb temporären Speicher belegen.

## Fehler- und Ausfallverhalten

- Ist der Port belegt oder die Bind-Adresse ungültig, stoppt Waitress sichtbar.
- Fehlt eine Abhängigkeit, beendet bereits die Installation im Starter den
  Startvorgang.
- Ungültige Port-, Thread-, Timeout- oder Proxywerte stoppen vor dem Binden.
- Ein erschöpfter Thread-Pool verzögert weitere Anfragen, beschädigt aber keine
  Dokumente. Für parallele OCR- oder große Upload-Aufgaben kann die Threadzahl
  bewusst erhöht werden.
- Der Initialscan läuft wie bisher in einem Hintergrundthread und schreibt
  seinen Status. Ein Serverabbruch ändert dessen Aufbewahrungsregeln nicht.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenmigration. Vorhandene Einrichtung, Port, Dokumente,
Sitzungsschlüssel, Datenbanken und App-Passwörter bleiben gültig. Beim nächsten
Start wird nur die neue Python-Abhängigkeit installiert. Bestehende
Kommandozeilen ohne neue Optionen funktionieren unverändert.

## Tests

Automatisiert geprüft werden:

- sichere lokale Standardwerte,
- Port-, Thread-, Timeout- und Uploadgrenzen,
- explizite Umgebungsvariablen,
- Proxy-Kompatibilitätsmodus,
- verständliches Ablehnen ungültiger Werte,
- Linux-Hilfe und Optionsvalidierung,
- Optionsgleichheit des Windows-Starters,
- vollständige bestehende Anwendungstests.

## Bekannte Grenzen

- Waitress skaliert innerhalb eines einzelnen Prozesses. Mehrere Prozesse,
  Hochverfügbarkeit und Neustartüberwachung benötigen systemd, einen Container
  oder einen vergleichbaren Prozessmanager.
- TLS, HTTP/2, Rate-Limits und Komprimierung gehören weiterhin in den
  Reverse-Proxy.
- Ein laufender Prozess übernimmt geänderte Optionen erst nach einem Neustart.

## Deaktivierung oder Rückkehr

Ein Code-Rollback entfernt die Waitress-Abhängigkeit; Daten bleiben
unverändert. Für eine kurzfristige lokale Fehlersuche kann Flask weiterhin
manuell mit `python -m flask --app app run` gestartet werden. Dieser Weg ist
nur für Entwicklung vorgesehen und zeigt deshalb wieder die Warnung an.
