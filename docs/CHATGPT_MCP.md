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

## Protokollierung

Jeder Werkzeugaufruf wird in `mcp_operation` mit Request-ID, Zeitpunkt, Benutzer, Token-ID, Werkzeug, sicherem Zielbezeichner, Ergebnis und Fehlerklasse protokolliert. Zusätzlich entsteht ein Security-Audit-Ereignis. Tokens, Passwörter, vollständige Argumente und Dateiinhalte gelangen nicht ins Log. Die Request-ID verbindet den Vorgang mit dem allgemeinen Anwendungsfehlerprotokoll.

Tokens bestehen aus 40 zufälligen URL-sicheren Bytes und werden nur als SHA-256-Prüfwert gespeichert. Sie sind einzeln widerrufbar, maximal 365 Tage gültig und bei Kontosperrung sofort unbrauchbar. `SIMPLEOFFICE_MCP=0` deaktiviert die Schnittstelle ohne Datenänderung. Die Datenbankmigration ist rein additiv.

## Grenzen

Der Server ist zustandslos und benötigt keine Server-Sent Events. Die Beispieldatei gilt für dateibasierte MCP-Clients; ChatGPT richtet die Verbindung über die Plugin-Oberfläche ein. OAuth-Discovery und ein eingebettetes Widget sind noch nicht enthalten. Destruktive Werkzeuge bleiben absichtlich ausgeschlossen.
