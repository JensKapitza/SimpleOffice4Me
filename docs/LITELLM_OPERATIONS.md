# Optionales LiteLLM-Gateway (#586)

SimpleOffice verwendet LiteLLM als OpenAI-kompatible Gateway-Schicht. Die
bestehende MCP-Integration bleibt separat: [MCP-Betrieb](CHATGPT_MCP.md).
LiteLLM erhält weder automatisch MCP-Tokens noch Benutzer-/Objektrechte.
Standardzustand: deaktiviert. Der normale Web-/Dokumentenbetrieb benötigt
weder Docker noch das LiteLLM-Pythonpaket. Keine zusätzliche Pflichtabhängigkeit.

## Zentrale Administration

**Administration → Mini Services → LiteLLM konfigurieren**
(`/admin/mini-services/litellm`). Nur Administratoren können Einstellungen,
Verbindungstest, Installation, Start/Stop/Restart, Backup und Restore bedienen.
Session, CSRF und Security-Audit werden aus den bestehenden Pfaden übernommen.
MCP-Zugänge und der aktuelle MCP-Aktivierungszustand sind dort verlinkt/angezeigt.

Gateway-URL, Standardmodell/Alias, Gateway-Key, Timeout (1–30 Sekunden),
Status-Retries (0–2), Betriebsart und explizite interne Netzwerkfreigaben sind
zentral konfigurierbar. Ein leeres Key-Feld behält den bestehenden Key.
Keys werden zweckgebunden mit dem Anwendungsschlüssel verschlüsselt. Weder
Einstellungs-API noch HTML oder Mini-Service-Diagnose geben Keys aus.
Konfiguration: neben `mini-services.json` in `litellm-service.json`; die bestehende
Variable `SIMPLEOFFICE_MINI_SERVICES_CONFIG` bestimmt auch diesen Speicherort.
Schreibvorgänge sind atomar, lokale Aktionen durch den bestehenden OS-Lock serialisiert.

## Externes Gateway (alle Serverpfade, Android/Termux)

1. LiteLLM beim Betreiber installieren und TLS mit gültigem Zertifikat einrichten.
2. Extern auswählen, URL wie `https://gateway.example.org/v1`, Modellalias und
   einen möglichst auf dieses Modell begrenzten Gateway-Key eintragen.
3. Aktivieren und speichern, dann **Verbindung testen**.

Die URL ist die Gateway-Wurzel, optional mit `/v1`; freie Pfade, Credentials,
Query-Parameter und Fragmente sind nicht erlaubt. TLS-Prüfung bleibt aktiv.
Interne Adressen brauchen eine explizite CIDR-Freigabe. Loopback, Link-Local,
Multicast, reservierte Adressen und gemischte öffentliche/private DNS-Antworten
werden verworfen. Alle DNS-Antworten werden geprüft; der Transport verbindet
mit einer geprüften IP und verifiziert TLS gegen den ursprünglichen Hostnamen.
Redirects werden nicht verfolgt. HTTP-Proxys aus Umgebungsvariablen werden beim
Gateway-Zugriff nicht verwendet. DNS-Auflösung und Datenübertragung sind zeitlich
begrenzt, die Anzahl parallel wartender DNS-Auflösungen ist begrenzt.

Der Verbindungstest prüft `/health/liveliness`, `/health/readiness` sowie
`/v1/models` mit Bearer-Key und kontrolliert den gespeicherten Modellalias.
Er löst keinen kostenpflichtigen Modellaufruf aus. Der Hub zeigt das letzte
Prüfergebnis bis zu 60 Sekunden; danach ist ein neuer Test erforderlich.
Das ist kein dauerhafter Monitoring-Worker und kein Durchsatznachweis.

## Lokaler Mini-Service (Linux, Windows/macOS mit Docker Desktop)

Voraussetzungen: lokale Docker Engine ab 24, Compose v2 ab 2.24, Zugriff auf
den lokalen Docker-Daemon, ausreichend Speicher für das Image. Docker-Rechte
sind weitreichende Hostrechte und müssen beim Betreiber eingerichtet sein;
SimpleOffice erhöht keine Rechte. Remote-Docker-Kontexte sind nicht unterstützt.
Im SimpleOffice-Container ist der **externe Betrieb** mit separatem Gateway
zu verwenden; kein Docker-Socket muss in die Webanwendung eingebunden werden.

1. Lokal auswählen, freien Port ab 1024 und Modellalias eintragen.
2. Provider-Modell gemäß LiteLLM, z. B. `openai/dein-modell`, und den Provider-Key
   eintragen. Einen separaten Gateway-Master-Key mit `sk-`-Präfix erzeugen und
   im Gateway-Key-Feld speichern; keine tatsächlichen Keys ins Repository legen.
3. Aktivieren, speichern und **Gepinntes Image installieren / aktualisieren**.
4. **Starten**, Initialisierung abwarten und **Verbindung testen**.

Default ist `docker.litellm.ai/berriai/litellm:v1.100.1` mit dem am 05.10.2026
aus der offiziellen Registry geprüften Digest
`sha256:a3715fa7ad8387941ab697259bd2881d68931657247a41984f90fae6d11c62bf`.
Pull und Compose verwenden ausschließlich `Version@sha256:Digest`, damit ein
nachträglich veränderter Tag keinen anderen Code mit den Provider-Keys ausführt.
Erlaubte Versionslinie ab 1.98.0. Bei Versionswechsel den zugehörigen Digest
aus der vertrauenswürdigen Registry prüfen und gemeinsam speichern; ohne Digest
ist lokaler Betrieb gesperrt. Kein `latest`, keine unbemerkten Upgrades. Die Python-
Abhängigkeiten von LiteLLM laufen im eigenen Image, nicht in der SimpleOffice-venv.
Die offiziell gepinnte Version definiert ihre Abhängigkeiten und Python-Version
selbst. Installation lädt nur das gewählte Image; Start verwendet `--pull never`.

Der generierte Compose-Stack liegt unter `instance/litellm/` (bei abweichender
Mini-Service-Konfiguration neben dieser Datei). Das JSON-Dokument `config.yaml`
ist gültiges YAML und enthält nur Secret-Referenzen. Diese nicht geheime Datei
wird mit Modus 0644 für den Container ohne DAC-Override-Capability geschrieben;
verschlüsselte Einstellungen bleiben 0600. Keys werden dem
Docker-Prozess serverseitig per Umgebung übergeben; berechtigte Docker-/Host-
Administratoren können Container-Umgebungen einsehen. Veröffentlichung nur auf
`127.0.0.1:<Port>`, keine Internetfreigabe. Capabilities entfernt,
`no-new-privileges`, PID-/RAM-Limits. Provider-/Prompt-Logs werden für den lokalen
Container nicht in das gemeinsame Journal übernommen (`logging: none`).

Ein Datenbankfreier Konfigurationsbetrieb ist bewusst der lokale Standard.
Virtuelle Keys, Spend-Tracking und Gateway-Admin-UI mit Persistenz gehören zum
extern verwalteten Gateway. Hierfür vorhandenes PostgreSQL prüfen und eine
isolierte Datenbank/Rolle im vorhandenen Dienst verwenden; keine ungeprüfte zweite
PostgreSQL-Installation. Lokale Provider-URL- und Mehrmodellverwaltung bleiben
in dieser Variante ausgeschlossen; hierfür die externe Betriebsart verwenden.

Autostart greift über den bestehenden Launcher beim nächsten SimpleOffice-Start.
Die Containerregel begrenzt Fehlerneustarts auf drei; kein unbegrenzter Retry-Loop.
Ein gewöhnlicher Webserverstart ohne Launcher startet keine Container implizit.

## Fehler und Netzwerkzugriff

`app.litellm_gateway.completion(messages, max_tokens=...)` stellt serverseitig den
begrenzt validierten OpenAI-Chat-Zugang bereit. Kein öffentlicher Proxy-Endpunkt
und keine automatische Weitergabe von MCP-Tokens/Tools. Aufrufende Fachfunktionen
müssen ihre vorhandenen Benutzer-/Objektberechtigungen vor dem Aufruf prüfen.
Nur konfigurierte Modelle und vorgegebene HTTP-Pfade sind erreichbar. Request
und Response maximal 1 MiB, 100 Nachrichten und 4096 Ausgabe-Tokens.

Nur idempotente Statusprüfungen werden bei vorübergehenden Netzwerkfehlern,
429 oder 5xx begrenzt wiederholt. Das Timeout gilt für die gesamte Operation
inklusive Retries, DNS und Übertragung; der Verbindungstest teilt sein Budget
auf alle drei Prüfungen. Modell-POSTs werden nie automatisch wiederholt, weil
Kosten oder bereits ausgeführte Aktionen sonst doppelt auftreten könnten.
Bei Verbindungsfehlern vor der HTTP-Anfrage werden weitere bereits geprüfte
DNS-Adressen innerhalb derselben Deadline versucht. Nach Übertragung der Anfrage
gibt es keinen Adresswechsel für Modell-POSTs; TLS-Zertifikatsfehler brechen ab.
TLS benötigt mindestens Version 1.2.
Keine automatischen Provider-Fallbacks mit veränderten Datenfreigaben.

Fehlercodes: `disabled`, `unauthorized`, `unreachable`, `timeout`,
`temporarily_unavailable`, `endpoint_blocked`, `model_missing`,
`invalid_response`, `request_too_large`, `response_too_large`.
Bei 401/403 Key/Rechte prüfen. Der lokale Datenbankfreie Betrieb liefert
für unbekannte Keys außerdem HTTP 400 mit `no_db_connection`; der Adapter
ordnet dies gezielt als `unauthorized` ein. bei `model_missing` Alias kontrollieren; bei
Timeout/Erreichbarkeit Gateway und Netzwerk prüfen; bei SSRF-Sperre ausschließlich
den tatsächlich benötigten internen CIDR freigeben. Diagnose/Audit enthalten
keine Providerantworten, Prompts oder Exception-Payloads.

## Upgrade / Rollback / Restart

1. Verschlüsselte Konfiguration sichern; Anwendungsschlüssel separat sichern.
2. Lokalen Dienst stoppen. Laufende lokale Konfiguration wird nicht überschrieben.
3. Zielversion und geprüften Image-Digest speichern und **Image installieren / aktualisieren**.
4. Starten und Verbindung testen; Fachanfrage mit unkritischen Daten prüfen.
5. Bei Fehlern stoppen, vorherige Version mit ihrem Digest wieder speichern/installieren/starten.

Es gibt keine lokalen Gateway-DB-Migrationen. Das vorherige Image bleibt erhalten;
kein automatisches Pruning. Neustart ersetzt nur den eigenen Compose-Dienst
(Projektkennung aus dem Konfigurationspfad), keine fremden Container.
Fehlende Docker-Komponenten, Pull-Timeouts, fehlende Images, Portkonflikte,
Konfigurationsfehler und fehlgeschlagene Prüfungen werden kontrolliert gemeldet.
Vor einem Neustart werden Secrets entschlüsselt, Compose-Dateien geschrieben und
das lokal installierte Image geprüft. Scheitert diese Vorprüfung, läuft der
bestehende Container weiter. Gestoppt und startend werden als eigene Zustände
angezeigt. Abgewiesene Einstellungen oder konkurrierende Aktionen verändern
keinen gemessenen Health-Status; ihre Fehler stehen in Rückmeldung und Audit.
Der Hub wartet für synchrone LiteLLM-Aktionen bis zu 90 Sekunden; Docker-Befehle
und Verbindungstests bleiben serverseitig einzeln begrenzt.
Die letzte sichere Dienstdiagnose steht im Hub. Ein Pull-Timeout verlangt einen
erneuten expliziten Versuch; kein Hintergrund-Installationsloop.

## Backup / Restore / Deaktivieren

**Verschlüsselte Konfiguration sichern** exportiert Schema 1, einschließlich
verschlüsselter Gateway-/Provider-Keys. Der ursprüngliche SimpleOffice-
Anwendungsschlüssel ist zwingend erforderlich. Sicherung und Schlüssel getrennt
und zugriffsgeschützt aufbewahren. Der Compose-Stack wird reproduzierbar erzeugt;
kein zusätzlicher lokaler Datenspeicher muss archiviert werden.

Vor Restore deaktivieren. Restore validiert die Sicherung und entschlüsselt Keys
zur Prüfung, schreibt atomar und setzt `enabled=false` und `autostart=false`.
Eine defekte Konfigurationsdatei kann bei gestopptem Dienst wiederhergestellt
werden. Externe PostgreSQL-Daten, Gateway-Keys und Salt-Key bleiben Verantwortung
des externen Gateway-Betreibers; dessen Restore muss zur verwendeten Gateway-
Version passen. Die SimpleOffice-Sicherung ersetzt diese Datenbanksicherung nicht.

Deaktivieren unterbindet neue Gateway-Anfragen und stoppt/entfernt den lokal
verwalteten Container. Nach einer Docker-Vorprüfung wird der deaktivierte Zustand
atomar gespeichert, bevor Stop/Remove beginnt: Scheitert die Speicherung, bleibt
der Container unverändert; ein Absturz nach Stop hinterlässt keinen aktivierten
Autostart. Scheitert Stop/Remove, wird die vorherige Konfiguration wiederhergestellt
und die Aktion als fehlgeschlagen gemeldet. Scheitert auch diese Wiederherstellung,
müssen Konfiguration und Containerzustand vor einem erneuten Start geprüft werden. Auch ein fehlender Docker-Client gilt
als unbekannter Zustand, nicht als erfolgreicher Stop. Einstellungen und verschlüsselte Keys
bleiben erhalten. Bei beschädigter Konfiguration funktioniert die Stop-Aktion
anhand der eigenen Compose-Labels auch ohne Entschlüsselung. Extern wird nur
der SimpleOffice-Zugang deaktiviert, keine fremde Infrastruktur abgeschaltet.
MCP wird davon unabhängig über `MCP_ENABLED` deaktiviert.

## Automatisierte Abnahme

- `python -m unittest discover -s tests -p 'test_litellm*.py' -v`: Konfiguration,
  Encryption, Rechte/CSRF/Audit, DNS/SSRF, echter HTTP-Transport, Fehlercodes,
  Größenlimits, Deadline, Retry-Verhalten, Lifecycle, Backup/Restore und Deaktivierung.
- Öffentliche Mini-Service-API: Deaktivierungsfehler, Neustart-Vorprüfung,
  Lifecycle-Status, unveränderte Health-Diagnose bei Eingabefehlern, rekursiv
  beschädigte Dateien, Restore-Feedback und unveränderliches Containerimage.
- Echter TLS-Testserver: Mehradress-Fallback vor einem einzelnen Modell-POST
  sowie Ablehnung eines Zertifikats mit falschem Hostnamen.
- Dünner Browserablauf auf Desktop/Mobil: Mini-Service-Link, Speichern,
  deaktivierter Verbindungstest, Backup-Download und sichtbarer Restore-Fehler.
- Bestehende 22 MCP-Tests bleiben eigenständig unverändert.
- CI-Job **LiteLLM optional container operations**: gepinntes reales LiteLLM-
  Image, nicht kostenpflichtiges Mock-Modell, Installation/Readiness, OpenAI-
  Completion, ungültiger Key, Restart, Backup/Restore, Deaktivierung und Reinstall.
  Einstieg: `python tools/litellm_smoke.py` auf einem Docker-Host. Das Skript
  arbeitet in einem temporären Konfigurationspfad und entfernt seinen Container.

Die Containerabnahme ergänzt die bestehenden Python-3.10-/3.14-/3.15-Gates,
Policy, Größenlimits, CRA, SBOM und Dependency-Audit. Reale Providerkosten,
lang laufender Lastbetrieb und externe Gateway-Datenbank-Restore brauchen
separate Betreiber-Nachweise; diese werden nicht durch Mock-Tests behauptet.

Referenzen:
- https://docs.litellm.ai/docs/proxy/docker_quick_start
- https://docs.litellm.ai/docs/proxy/deploy
- https://docs.litellm.ai/docs/proxy/virtual_keys
- https://github.com/BerriAI/litellm/blob/v1.100.1/pyproject.toml

### Ungültige numerische Eingaben und beschädigte Statusdateien

Die Settings-API akzeptiert für `timeout` Zahlen und numerische Formularwerte
von 1 bis 30 Sekunden. Boolesche Werte, `null`, Listen, Objekte und übergroße
Zahlen werden mit HTTP 400 abgelehnt; gespeicherte Einstellungen und die letzte
erfolgreiche Verbindungsdiagnose bleiben erhalten. Unlesbare oder numerisch
ungültige Zeitstempel in der Statusdatei machen den Mini-Service-Katalog nicht
unzugänglich: Der Dienst meldet einen unbekannten Status und verlangt einen
neuen Verbindungstest.

### Zusätzliche Review-Grenzen

Lokaler Docker-Betrieb akzeptiert ausschließlich Unix-Sockets oder die lokale
Windows-Docker-Engine-Pipe. SSH-/TCP-Endpunkte werden auch bei gesetztem
`DOCKER_HOST`, `DOCKER_CONTEXT` oder gespeichertem Remote-Kontext abgewiesen.
Kontextinspektion erhält keine entschlüsselten Gateway-/Provider-Keys; jeder
Lifecycle-Befehl bindet anschließend den geprüften lokalen Endpunkt ausdrücklich.
Remote-Docker wird nicht durch einen stillen Wechsel auf einen anderen Daemon ersetzt.

Versionsstrings sind auf 64 Zeichen begrenzt. Fehlgeschlagene TLS-Zertifikats-/
Handshake-Prüfungen werden auch bei konfigurierten Retries nicht wiederholt;
der bestehende Fehlercode bleibt `unreachable`. CA und Gateway-Hostname prüfen.
Ein fehlgeschlagener Konfigurationsbackup liefert HTTP 503 und einen Audit-Eintrag
mit Ergebnis `failure`; ein erfolgreicher Audit-Eintrag entsteht erst nach der
Serialisierung der Sicherung.
