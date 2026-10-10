# Webseitenexport (Issue #606)

Der erste Ausbau exportiert angemeldete SimpleOffice-Seiten als PDF oder PNG mit dem vorhandenen Playwright/Chromium-Stack. Die Aktion sitzt im gemeinsamen `templates/layout.html`.

## Sicherheitsvertrag

Der Renderer akzeptiert keine beliebigen URLs. `SIMPLEOFFICE_SERVER_PUBLIC_URL` legt im normalen Serverbetrieb die einzige zulässige Origin fest; Requests zu anderen Origins werden im Browser abgebrochen. Bei lokalem Zugriff über `localhost`, `127.0.0.0/8` oder `::1` wird ohne gesetzte Variable ausschließlich die aktuelle Loopback-Origin verwendet. Das Export-Token wird nur über `X-SimpleOffice-Export-Token` übertragen, gehasht in SQLite gespeichert und niemals in URL oder Kommandozeile geschrieben.

Der Token-Austausch erzeugt eine Sitzung des tatsächlichen Benutzers mit dessen `auth_version`. Gesperrte Konten, widerrufene/abgelaufene Tokens und geänderte `auth_version` werden abgewiesen. Der Nutzungszähler wird unter `BEGIN IMMEDIATE` atomar verbraucht. Die Renderer-Sitzung blockiert alle mutierenden HTTP-Methoden.

Administratoren können unter `/web-export/settings` Gültigkeit (mindestens 1 Minute) und Anzahl Exporte pro Token (mindestens 1) setzen. `POST /web-export/revoke` widerruft aktive Export-Tokens des Benutzers.

## Betrieb

Playwright ist optional. Für den Export müssen das Python-Paket und Chromium installiert sein. Ein lokal gestartetes SimpleOffice auf `localhost`/Loopback benötigt keine zusätzliche Public-URL-Konfiguration; bei Zugriff über LAN, DNS-Namen oder Reverse Proxy bleibt `SIMPLEOFFICE_SERVER_PUBLIC_URL` erforderlich. Der normale SimpleOffice-Betrieb startet auch ohne Playwright. Chromium wird mit seiner Sandbox gestartet; `--no-sandbox` wird nicht automatisch gesetzt.

PDF verwendet `page.pdf(print_background=True)` und enthält Browser-Text als durchsuchbaren PDF-Text. PNG verwendet einen Full-Page-Screenshot. Druck- und Bildschirmansicht werden über `page.emulate_media()` gewählt.

Nicht Bestandteil dieses PR: Ablage als Dokument, LibreOffice-Konvertierung, OCR und Export fremder Webseiten oder unvertrauenswürdiger HTML-Dateien.

### Standalone-Start

Bei `./start.sh --standalone` werden Playwright (`playwright==1.55.0`) und die dazu passende Chromium-Binary in der isolierten `.venv-standalone` installiert, falls sie fehlen. Bereits vorhandene Komponenten werden beim nächsten Start wiederverwendet. Dafür ist beim ersten Start Netzwerkzugang erforderlich. Fehlende Linux-Systembibliotheken lassen sich bei Bedarf mit `.venv-standalone/bin/python -m playwright install-deps chromium` ergänzen; dieser Schritt benötigt in der Regel administrative Rechte und wird deshalb nicht ungefragt vom Startskript ausgeführt.
