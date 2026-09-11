# GitHub-Fehlerberichte

SimpleOffice4Me kann unbehandelte Anwendungsfehler datensparsam melden und dedupliziert als GitHub-Issues ablegen. Der Fehlertransport benötigt **keine aktive Federation-Verbindung und keinen Federation-Token**. Er verwendet jedoch standardmäßig dieselbe globale Master-Adresse, die bereits beim Build als `LICENSE_MASTER_URL` hinterlegt wird.

## Zero-Config-Architektur

Für reguläre Client-Builds gilt ohne zusätzliche Konfiguration:

```text
SimpleOffice A ─┐
SimpleOffice B ─┼── HTTPS ──> globale Master-Federation ──> GitHub Issues
Android/APK  ───┤                       │
Desktop-App  ───┘                  GitHub-Token nur hier
```

Ist in `app/build_master.py` eine `LICENSE_MASTER_URL` eingebrannt und `LICENSE_MASTER_MODE=False`, leitet SimpleOffice daraus automatisch den Fehler-Endpunkt ab:

```text
<LICENSE_MASTER_URL>/api/error-reports/v1/reports
```

Es muss dafür **keine** `SIMPLEOFFICE_ERROR_REPORT_URL` gesetzt werden.

Ein Build mit `LICENSE_MASTER_MODE=True` aktiviert den Error-Collector auf derselben SimpleOffice-Instanz automatisch. Der Master sendet seine eigenen Fehler nicht an sich selbst; dadurch entsteht keine Schleife.

Die Fehler-API ist technisch getrennt von der authentifizierten Federation. Eine fremde Installation muss weder Federation zu deinem Server aktiviert haben noch einen Federation- oder GitHub-Token besitzen. Sie braucht lediglich HTTPS-Zugriff auf die globale Master-Adresse.

## Priorität der Konfiguration

Die Auswahl des Meldewegs erfolgt in dieser Reihenfolge:

1. `SIMPLEOFFICE_ERROR_REPORT_URL` – optionaler expliziter/custom Relay-Override.
2. Expliziter direkter GitHub-Modus mit `SIMPLEOFFICE_GITHUB_ERROR_REPORTING=1` – für kontrollierte Einzelserver.
3. Ohne beides automatisch die buildfeste globale `LICENSE_MASTER_URL`.

Mit

```bash
SIMPLEOFFICE_ERROR_REPORTING=0
```

kann die ausgehende automatische Fehlermeldung vollständig deaktiviert werden.

Auf dem Master kann der automatisch aktivierte Collector bei Bedarf explizit abgeschaltet werden:

```bash
SIMPLEOFFICE_ERROR_RELAY_ENABLED=0
```

Der separate Docker-Role `error-relay` bleibt als optionale Alternative erhalten. Er kann auf einem beliebigen Build mit `SIMPLEOFFICE_ERROR_RELAY_ENABLED=1` betrieben werden.

## Datenschutzprinzip

Zwischen Installation, Master/Relay und GitHub gilt dieselbe feste Allowlist. Übertragen werden nur:

- Request-ID
- stabiler Fehler-Fingerprint
- Exception-Typ, jedoch **nicht die Exception-Nachricht**
- Flask-Endpoint und HTTP-Methode
- optional die App-Version
- höchstens zwölf bereinigte Stack-Koordinaten: Dateiname, Zeile und Funktionsname beziehungsweise Template, Zeile und Variablenname

Nicht übertragen werden Request-Body, Query-Parameter, Header, Cookies, Sessiondaten, Benutzerkennung, Kundendaten, Datenbankinhalte, Umgebungsvariablen, Logdateien oder Anhänge.

Der Collector akzeptiert maximal 16 KiB pro Bericht, lehnt unbekannte JSON-Felder ab und besitzt lokale und globale Rate-Limits. Client-IP-Adressen werden nicht persistiert; für das kurzlebige In-Memory-Rate-Limit wird nur ein pro Prozess gesalzener Hash verwendet. Stack-Felder werden auf technische Zeichenmengen begrenzt, damit ein anonymer Caller keinen Markdown-Inhalt in erzeugte GitHub-Issues einschleusen kann.

Technische Fehlerdetails werden auf der lokalen 500-Seite nur einem angemeldeten SimpleOffice-Administrator angezeigt.

## GitHub-Zugang nur auf Master/Relay

Der GitHub-Zugang gehört ausschließlich auf die Instanz, die den Collector bereitstellt. Für `JensKapitza/SimpleOffice4Me` reicht ein Fine-Grained Token mit `Issues: Read and write`. Contents-, Administration- oder Secrets-Schreibrechte sind für den Reporter nicht erforderlich.

Bevorzugt wird eine private Token-Datei:

```bash
SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE=/etc/simpleoffice/secrets/github-error-token
```

Die Datei muss regulär sein, darf kein Symlink sein und keine Gruppen- oder Fremdrechte besitzen, beispielsweise Modus `0600`.

Ein Environment-Token über `SIMPLEOFFICE_GITHUB_ERROR_TOKEN` wird aus Kompatibilitätsgründen unterstützt. Der Token darf niemals committed, in ein Docker-Image, eine APK oder ein Desktop-Paket eingebaut werden.

Langfristig kann an derselben Stelle eine dedizierte GitHub App verwendet werden; die Client-/Collector-Schnittstelle muss dafür nicht geändert werden.

## Master als Collector

Ein korrekt gebauter Master (`LICENSE_MASTER_MODE=True`) stellt automatisch bereit:

```text
POST /api/error-reports/v1/reports
GET  /api/error-reports/v1/health
```

Die normale SimpleOffice-Weboberfläche und die bestehenden Federation-Endpunkte bleiben auf dem Master weiterhin verfügbar. Nur der optionale dedizierte Docker-Role `error-relay` beschränkt die sichtbare Oberfläche auf die Fehler-API.

Der Health-Endpunkt liefert HTTP 200, wenn der Collector aktiv und ein GitHub-Zugang konfiguriert ist, ansonsten 503 beziehungsweise 404 bei deaktiviertem Collector.

## Separaten Relay mit demselben Docker-Image starten

Falls der globale Master später nicht selbst öffentlich als Collector dienen soll, bleibt der fertige Compose-Stack unter `deploy/docker/compose.error-relay.yaml` verfügbar.

```bash
mkdir -p deploy/docker/secrets
chmod 700 deploy/docker/secrets
$EDITOR deploy/docker/secrets/github-error-token
chmod 600 deploy/docker/secrets/github-error-token
docker compose -f deploy/docker/compose.error-relay.yaml up -d --build
```

Der Compose-Stack bindet standardmäßig nur an `127.0.0.1:8090`. Der Token wird mit Modus `0600` nach `/tmp/simpleoffice-github-error-token` kopiert; `/tmp` ist dort `tmpfs`. Er landet weder im persistenten Volume noch im Image.

## Eigener Collector als Override

Ein Betreiber kann die eingebaute Master-Adresse bewusst überschreiben:

```bash
SIMPLEOFFICE_ERROR_REPORT_URL=https://errors.example.org/api/error-reports/v1/reports
```

Das ist nur ein Override und für normale, korrekt gebaute SimpleOffice-Clients nicht erforderlich.

## Direkter GitHub-Modus

Für eine eigene kontrollierte Installation bleibt direkter GitHub-Zugriff möglich:

```bash
export SIMPLEOFFICE_GITHUB_ERROR_REPORTING=1
export SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY=JensKapitza/SimpleOffice4Me
export SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE=/etc/simpleoffice/secrets/github-error-token
```

Dieser Modus hat bewusst Vorrang vor dem automatischen Master-Fallback.

## Android/APK und Desktop

Die Fehlerlogik sitzt in derselben Python-App. Sobald der jeweilige Paket-Build die globale `LICENSE_MASTER_URL` enthält, ist deshalb keine separate Error-Adresse nötig.

Die bereits vorhandene optionale Build-/Runtime-Einstellung `SIMPLEOFFICE_ERROR_REPORT_URL` bleibt für spezielle Builds oder Tests als Override bestehen. **Kein GitHub-Token darf in Android-BuildConfig oder Desktop-Pakete gelangen.**

Die Android-Hülle gibt externe HTTPS-Links über Android `ACTION_VIEW` an das Betriebssystem weiter. Ist die GitHub-App als Handler eingerichtet, kann sie den manuellen Fallback übernehmen; andernfalls wird der Browser verwendet. Die Electron-Hülle öffnet externe HTTP/HTTPS-Links über den Systembrowser.

## Manueller Fallback

Kann ein Fehler nicht automatisch übertragen werden, zeigt die lokale 500-Seite **„Fehler auf GitHub melden“**. Der Link füllt eine datensparsame technische Diagnose vor: Request-ID, Fingerprint, Exception-Typ, Endpoint, Methode, optionale Version und höchstens sechs bereinigte Stack-Koordinaten.

Exception-Nachricht, Request-Inhalte, Kundendaten, Benutzerkennung, Logs und Zugangsdaten werden auch bei diesem Weg nicht übernommen.

Hat der automatische Collector bereits erfolgreich ein Issue angelegt, wird stattdessen der vorhandene Issue-Link angezeigt, damit kein doppeltes Issue entsteht.

## Verhalten bei Ausfällen

Bei einem unbehandelten Fehler speichert SimpleOffice weiterhin seinen lokalen strukturierten Fehlerdatensatz. Danach wird die externe Meldung versucht.

Ist Master/Relay oder GitHub nicht erreichbar, ist der GitHub-Zugang ungültig oder wurde Reporting deaktiviert, bleibt die lokale Fehlerbehandlung aktiv. Ein Fehler des Reporters darf keinen weiteren Anwendungsfehler auslösen.
