# GitHub-Fehlerberichte

SimpleOffice4Me kann unbehandelte Anwendungsfehler datensparsam melden und dedupliziert als GitHub-Issues ablegen. Das Fehlerreporting ist **kein Bestandteil der Federation**: Eine Installation kann Fehler melden, ohne einen Federation-Peer zu kennen oder Federation aktiviert zu haben.

## Architektur

Empfohlen ist der zentrale Relay-Modus:

```text
SimpleOffice A ─┐
SimpleOffice B ─┼── HTTPS ──> SimpleOffice error-relay ──> GitHub Issues
Android/APK  ───┘                    │
                               GitHub-Token nur hier
```

Normale Installationen besitzen **keinen GitHub-Token**. Sie kennen lediglich die HTTPS-URL des Relay. Der Relay ist Bestandteil desselben SimpleOffice4Me-Codes und wird aus demselben Dockerfile gebaut. Es muss daher kein zweites Serverprojekt gepflegt werden.

Der Docker-Role `error-relay` beschränkt die öffentlich erreichbare Flask-Anwendung auf `/api/error-reports/v1/`; normale SimpleOffice-Routen werden in diesem Role mit 404 beantwortet.

## Datenschutzprinzip

Zwischen Installation, Relay und GitHub gilt dieselbe feste Allowlist. Übertragen werden nur:

- Request-ID
- stabiler Fehler-Fingerprint
- Exception-Typ, jedoch **nicht die Exception-Nachricht**
- Flask-Endpoint und HTTP-Methode
- optional die App-Version
- höchstens zwölf bereinigte Stack-Koordinaten: Dateiname, Zeile und Funktionsname beziehungsweise Template, Zeile und Variablenname

Nicht übertragen werden Request-Body, Query-Parameter, Header, Cookies, Sessiondaten, Benutzerkennung, Kundendaten, Datenbankinhalte, Umgebungsvariablen, Logdateien oder Anhänge.

Der Relay akzeptiert maximal 16 KiB pro Bericht, lehnt unbekannte JSON-Felder ab und besitzt ein lokales sowie globales Rate-Limit. Client-IP-Adressen werden nicht persistiert; für das kurzlebige In-Memory-Rate-Limit wird nur ein pro Prozess gesalzener Hash verwendet.

Technische Fehlerdetails werden auf der lokalen 500-Seite nur einem angemeldeten SimpleOffice-Administrator angezeigt.

## Normalen SimpleOffice-Server anbinden

Eine normale Installation benötigt nur die öffentliche HTTPS-Adresse des Relay:

```bash
export SIMPLEOFFICE_ERROR_REPORT_URL='https://errors.example.org/api/error-reports/v1/reports'
```

Bei Docker Compose kann derselbe Wert in `deploy/docker/.env` gesetzt werden. `deploy/docker/compose.yaml` reicht ihn an den Container weiter.

Ohne `SIMPLEOFFICE_ERROR_REPORT_URL` und ohne explizit aktivierten direkten GitHub-Modus bleibt die automatische externe Übertragung deaktiviert. Die lokale Fehlerprotokollierung funktioniert weiterhin.

## Zentralen Relay mit demselben Docker-Image starten

Der fertige Compose-Stack liegt unter `deploy/docker/compose.error-relay.yaml`.

Zuerst lokal ein Secret-Verzeichnis und die Token-Datei anlegen:

```bash
mkdir -p deploy/docker/secrets
chmod 700 deploy/docker/secrets
$EDITOR deploy/docker/secrets/github-error-token
chmod 600 deploy/docker/secrets/github-error-token
```

`deploy/docker/secrets/` ist in `.gitignore` eingetragen. Der echte Token darf niemals committed, in ein Docker-Image eingebaut oder in eine APK gepackt werden.

Danach den Relay bauen und starten:

```bash
docker compose -f deploy/docker/compose.error-relay.yaml up -d --build
```

Standardmäßig lauscht der Relay nur auf `127.0.0.1:8090`. Davor gehört ein TLS-Reverse-Proxy wie Caddy, nginx oder Traefik. Erst die öffentliche `https://.../api/error-reports/v1/reports`-Adresse wird an andere SimpleOffice-Installationen verteilt.

Der Health-Endpunkt lautet:

```text
/api/error-reports/v1/health
```

Er liefert nur dann HTTP 200, wenn der Relay aktiviert und ein GitHub-Zugang konfiguriert ist. Der Docker-Healthcheck wählt diesen Endpunkt automatisch, wenn `SIMPLEOFFICE_CONTAINER_ROLE=error-relay` gesetzt ist.

Wenn genau ein vertrauenswürdiger Reverse-Proxy direkt vor dem Relay steht, kann `SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1` gesetzt werden. Standard ist absichtlich `0`.

## GitHub-Zugang nur auf dem Relay

Für `JensKapitza/SimpleOffice4Me` reicht ein Fine-Grained Token mit `Issues: Read and write`. Contents-, Administration- oder Secrets-Schreibrechte sind für den Reporter nicht erforderlich.

Der Compose-Relay mountet die Token-Datei nach `/run/secrets/github-error-token`. Der Entry-Point kopiert sie vor dem Privilege-Drop mit Modus `0600` nach `/tmp/simpleoffice-github-error-token`; `/tmp` ist im mitgelieferten Compose-Stack ein `tmpfs`. Der Token landet deshalb weder im persistenten SimpleOffice-Volume noch im Image und verschwindet mit dem Container. Die Anwendung erhält ausschließlich den Dateipfad.

Langfristig kann an derselben Stelle statt eines PAT eine dedizierte GitHub App verwendet werden; die Client-/Relay-Schnittstelle muss dafür nicht geändert werden.

## Direkter GitHub-Modus für einzelne eigene Server

Für eine eigene kontrollierte Installation bleibt der direkte Modus möglich:

```bash
export SIMPLEOFFICE_GITHUB_ERROR_REPORTING=1
export SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY=JensKapitza/SimpleOffice4Me
export SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE=/etc/simpleoffice/secrets/github-error-token
```

Die Token-Datei muss eine reguläre Datei sein und darf keine Gruppen- oder Fremdrechte besitzen, beispielsweise Modus `0600`.

Ein Environment-Token über `SIMPLEOFFICE_GITHUB_ERROR_TOKEN` wird aus Kompatibilitätsgründen unterstützt, eine geschützte Datei ist für Serverbetrieb vorzuziehen.

## Android/APK automatisch an den Relay anbinden

Die öffentliche Relay-URL darf in eine APK eingebaut werden, weil sie **kein Secret** ist. Der Android-Build liest dafür `SIMPLEOFFICE_ERROR_REPORT_URL` und schreibt ausschließlich diese HTTPS-URL in `BuildConfig.ERROR_REPORT_URL`. Beim Start übergibt die Android-Hülle sie an den lokalen Python-Server, der sie als `SIMPLEOFFICE_ERROR_REPORT_URL` setzt.

Der GitHub-Actions-Workflow liest denselben Wert aus der Repository-Variable `SIMPLEOFFICE_ERROR_REPORT_URL`. Nach Bereitstellung des öffentlichen Relay wird diese Variable einmalig beispielsweise auf

```text
https://errors.example.org/api/error-reports/v1/reports
```

gesetzt. **Kein GitHub-Token darf als Android-Buildvariable gesetzt werden.** Ist die Repository-Variable leer, wird die APK weiterhin gebaut; automatische externe Meldungen bleiben dann deaktiviert und der manuelle Fallback bleibt verfügbar.

## Manuelle Meldung über GitHub-App oder Browser

Kann ein Fehler nicht automatisch übertragen werden, zeigt die lokale 500-Seite einen Link **„Fehler auf GitHub melden“**. Er öffnet die normale GitHub-Seite zum Erstellen eines Issues und enthält lediglich die Request-ID sowie einen kurzen Hinweistext.

Die Android-Hülle von SimpleOffice4Me gibt externe HTTPS-Links über Android `ACTION_VIEW` an das Betriebssystem weiter. Ist die GitHub-App als Handler eingerichtet, kann sie den Link übernehmen; andernfalls wird der Browser verwendet. Ein GitHub-Token wird dafür ebenfalls nicht in SimpleOffice gespeichert.

Hat der automatische Relay bereits erfolgreich ein Issue angelegt, wird stattdessen der vorhandene Issue-Link angezeigt, damit der Benutzer nicht versehentlich ein zweites Issue erzeugt.

## Verhalten bei Fehlern

Bei einem unbehandelten Fehler speichert SimpleOffice weiterhin seinen lokalen strukturierten Fehlerdatensatz. Danach wird die externe Meldung versucht.

Im Relay-Modus sendet die Installation die feste Allowlist an den zentralen SimpleOffice-Relay. Der Relay prüft Schema, Größe und Rate-Limits und sucht über GitHub nach einem offenen Issue mit demselben Fingerprint. Existiert bereits eines, wird dessen Nummer zurückgegeben. Andernfalls wird ein neues Issue angelegt.

Ist der Relay oder GitHub nicht erreichbar, ist der Token ungültig oder ist die Funktion deaktiviert, bleibt die normale lokale Fehlerbehandlung aktiv. Ein Fehler des Reporters darf keinen weiteren Anwendungsfehler auslösen.
