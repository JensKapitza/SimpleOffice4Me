# ChatGPT und MCP

SimpleOffice4Me stellt normale Benutzerfunktionen über einen eigenen Streamable-HTTP-MCP-Endpunkt bereit. ChatGPT kann damit als Bedienoberfläche dienen, ohne dass SimpleOffice einen OpenAI-API-Schlüssel benötigt.

## Einrichtung

1. Unter **Einstellungen → ChatGPT und MCP** einen Zugang erzeugen. Er ist standardmäßig nur lesend und 30 Tage gültig; das Geheimnis erscheint genau einmal.
2. SimpleOffice per HTTPS veröffentlichen. Hinter einem Reverse Proxy muss `SIMPLEOFFICE_TRUSTED_PROXY_HOPS` stimmen. HTTP wird nur auf Loopback und in Tests akzeptiert.
3. In ChatGPT den Entwicklermodus aktivieren und unter **Plugins** eine Verbindung zu `https://SERVER/mcp` hinzufügen. Für private lokale Server eignet sich der offizielle Secure MCP Tunnel.
4. Für Clients mit Dateikonfiguration `simpleoffice.mcp.json.example` kopieren und `SIMPLEOFFICE_MCP_TOKEN` nur in einer geschützten Laufzeitumgebung setzen.

Primärquellen: [Build an MCP server](https://developers.openai.com/plugins/build/mcp-server) und [Connect from ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt). Implementiert sind JSON-RPC 2.0, Streamable HTTP, `initialize`, `ping`, `tools/list`, `tools/call`, explizite Schemas und Sicherheitskennzeichnungen für MCP `2025-06-18`.

## Rechte und Sicherheitsgrenzen

Jeder Aufruf prüft Kontoaktivität, Funktionsfreigabe, das Lese-/Schreibrecht des Tokens und bei Dokumenten zusätzlich die geerbten Ordnerrechte des virtuellen Dateisystems. Kalender- und Kontaktfreigaben werden von den vorhandenen Stores geprüft. Administrations-, Benutzerrechte-, Geheimnis- und beliebige URL-Werkzeuge werden bewusst nicht angeboten.

MCP kann keine Datei und keinen Datensatz löschen. Allgemeine Dokument-Tags dürfen ergänzt werden. Ein Löschvorschlag erzeugt ausschließlich das Tag `ai-delete-candidate` und eine unveränderliche Notiz `KI-Löschvorschlag (keine Löschung ausgeführt): …`. Ein Mensch kann Tag, Datei, Herkunft und Begründung anschließend prüfen.

## Argumente und Fehlerverhalten

`tools/list` veröffentlicht für jedes Werkzeug das verbindliche `inputSchema`.
Vor der Ausführung prüft der Server Pflichtfelder, JSON-Typen, Längen,
Wertebereiche, Auswahllisten, zusätzliche Felder und verschachtelte Werte.
Ein boolescher Wert ist keine Ganzzahl; Zahlen als Zeichenketten werden nicht
automatisch konvertiert. Beispielsweise erlaubt `create_project` Titel mit
1 bis 300 Zeichen, `list_projects` einen optionalen ganzzahligen `limit` von
1 bis 100 und `upsert_contact` ausschließlich Zeichenketten als Feldwerte.

Ungültige Werkzeugargumente ergeben ein MCP-Werkzeugergebnis mit `isError: true`
und der Meldung `Tool request rejected`. Es werden dabei keine fachlichen Daten
angelegt oder geändert; die Ablehnung wird im bestehenden Journal protokolliert.
Ein falsch aufgebautes `tools/call`, etwa mit einem Array statt eines
Parameterobjekts oder ohne Werkzeugnamen als Zeichenkette, ergibt den
JSON-RPC-Fehler `-32602` mit der ursprünglichen Request-ID.

## Protokollierung

Jeder Werkzeugaufruf wird in `mcp_operation` mit Request-ID, Zeitpunkt, Benutzer, Token-ID, Werkzeug, sicherem Zielbezeichner, Ergebnis und Fehlerklasse protokolliert. Zusätzlich entsteht ein Security-Audit-Ereignis. Tokens, Passwörter, vollständige Argumente und Dateiinhalte gelangen nicht ins Log. Die Request-ID verbindet den Vorgang mit dem allgemeinen Anwendungsfehlerprotokoll.

Bei abgelehnten Aufrufen bleibt der Zielbezeichner leer: Ein übermitteltes
ID-Feld kann ungültig sein oder fachliche Inhalte statt einer ID enthalten.
Werkzeug, Ergebnis und Fehlerklasse bleiben zur Diagnose sichtbar.

Tokens bestehen aus 40 zufälligen URL-sicheren Bytes und werden nur als SHA-256-Prüfwert gespeichert. Sie sind einzeln widerrufbar, maximal 365 Tage gültig und bei Kontosperrung sofort unbrauchbar. `SIMPLEOFFICE_MCP=0` deaktiviert die Schnittstelle ohne Datenänderung. Die Datenbankmigration ist rein additiv.

Ein Widerruf wirkt ab dem nächsten Aufruf, auch bei weiterhin angemeldetem
Browser. Andere Benutzer können einen fremden Token nicht widerrufen; separat
ausgestellte Tokens bleiben nutzbar. Die Einstellungsseite zeigt das vollständige
Geheimnis nur unmittelbar nach seiner Erzeugung an.

## Öffentliche API-Vertragstests

```bash
python -m unittest discover -s tests -p test_mcp_api_black_box.py -v
```

Die Tests registrieren Benutzer, melden sie an und erzeugen sowie widerrufen
Tokens über die öffentlichen HTTP-Oberflächen mit aktivem CSRF-Schutz.
Werkzeugaufrufe und Kontrollen ihrer Seiteneffekte erfolgen über `/mcp`;
das öffentliche Journal wird über `/settings/mcp` gelesen. Fest vorgegebene
Erwartungen sichern Lese-/Schreibrechte, Schemagrenzen, abgelehnte Eingaben ohne
fachliche Änderungen, JSON-RPC-Fehler, Widerruf, Benutzertrennung und den Schutz
von Tokens und fachlichen Argumenten im Journal ab. Produktive Validatoren,
Tokenfunktionen oder Datenbankabfragen berechnen keine erwarteten Testergebnisse.

## Browser-Bedienprüfung

Der bestehende Chromium-Lauf in
`tests/browser/manual_screenshot_smoke.py` prüft ergänzend fünf sichtbare
Bedienaktionen auf Desktop (1440 px) und in einer schmalen Ansicht (390 px):

1. **Mehr → MCP** öffnet die Seite **ChatGPT- und MCP-Zugang**.
2. **Zugang einmalig erzeugen** legt einen Lesezugang an; er erscheint in der Übersicht.
3. **Widerrufen** kennzeichnet diesen Zugang sichtbar als widerrufen.
4. Mit **Schreibende Werkzeuge erlauben** kann ein weiterer Zugang angelegt werden.
5. Das **Verarbeitungsjournal** ist auf derselben Seite erreichbar.

Dieselbe Liste eignet sich zur manuellen Browser-Prüfung mit einer isolierten
Testinstanz und Testkonto. Bei schmalen Ansichten zuerst **Navigation öffnen**
verwenden; die Zugangstabelle kann horizontal gescrollt werden.

Der Ablauf nutzt echte Klicks und Formulare. Er wiederholt keine
API-Schematests und prüft keine Datenbank-, CSS- oder internen Methodenwerte.
Ergebnisse stehen unter `mcp_ui.checks` im bestehenden Browserbericht;
Screenshots heißen `mcp-1440-*.png` beziehungsweise `mcp-390-*.png`.
Die Erzeugungsantwort mit dem einmalig sichtbaren Geheimnis wird nicht
aufgezeichnet: Vor jeder Aufnahme wird die Seite per GET neu geladen.
Im Browser-CI ist MCP auf der primären Testinstanz aktiviert.

## Grenzen

Der Server ist zustandslos und benötigt keine Server-Sent Events. Die Beispieldatei gilt für dateibasierte MCP-Clients; ChatGPT richtet die Verbindung über die Plugin-Oberfläche ein. OAuth-Discovery und ein eingebettetes Widget sind noch nicht enthalten. Destruktive Werkzeuge bleiben absichtlich ausgeschlossen.
