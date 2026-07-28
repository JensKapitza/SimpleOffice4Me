# Google-Anmeldung einrichten

1. In der [Google Cloud Console](https://console.cloud.google.com/apis/credentials) ein OAuth-Client vom Typ **Webanwendung** anlegen.
2. Als autorisierte Weiterleitungs-URL eintragen:

   `https://DEIN-HOST/auth/google/callback`

3. Die heruntergeladene JSON-Datei mit Client-ID und Client-Secret außerhalb des Repositories speichern, zum Beispiel `/etc/simpleoffice/google-oauth.json`, und nur für den Dienstbenutzer lesbar machen:

```bash
sudo install -m 600 -o simpleoffice -g simpleoffice google-client-secret.json /etc/simpleoffice/google-oauth.json
```

4. Vor dem Start von SimpleOffice mindestens diese Variablen setzen:

```bash
export SIMPLEOFFICE_SECRET_KEY='langer-zufaelliger-serverwert'
export SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE='/etc/simpleoffice/google-oauth.json'
```

Die JSON-Datei muss den Google-Block `web` enthalten. `SIMPLEOFFICE_GOOGLE_CLIENT_ID` und `SIMPLEOFFICE_GOOGLE_CLIENT_SECRET` bleiben als explizite Alternative verfügbar und haben Vorrang gegenüber den Werten aus der Datei. Eine URI aus `web.redirect_uris` wird automatisch benutzt, wenn sie mit `/auth/google/callback` endet. `SIMPLEOFFICE_GOOGLE_REDIRECT_URI` ist nur nötig, wenn du eine URI ausdrücklich überschreiben willst.

Der Server muss über HTTPS erreichbar sein. Für einen Reverse Proxy ist bereits `SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1` vorgesehen. Nach dem Neustart erscheint die Schaltfläche **Mit Google anmelden** bei Anmeldung und Registrierung. Beim ersten Google-Login wird ein lokales SimpleOffice-Konto angelegt und dauerhaft über die Google-Subject-ID zugeordnet.

## Mit `start.sh` starten

Für einen Linux-Start ohne manuelles Setzen von Umgebungsvariablen kann die Konfiguration direkt als Optionen übergeben werden:

```bash
./start.sh --google-json /etc/simpleoffice/google-oauth.json \
  --public-url https://office.example.de \
  --trusted-proxy-hops 1 \
  --secret-key-file /etc/simpleoffice/session-secret
```

`--public-url` überschreibt bei Bedarf die Callback-URL mit `https://office.example.de/auth/google/callback`. Normalerweise verwendet die Anwendung den passenden Eintrag aus der Google-JSON. Sichere Cookies werden automatisch nur für HTTPS-Anfragen gesetzt. Bei einem Reverse Proxy ist weiterhin `--trusted-proxy-hops 1` erforderlich, damit das Schema nicht gefälscht werden kann. Mit `./start.sh --help` werden alle Optionen angezeigt.

Für Windows gelten dieselben Optionen:

```bat
start.bat --google-json C:\simpleoffice\google-oauth.json --trusted-proxy-hops 1 --secret-key-file C:\simpleoffice\session-secret.txt
```

`start.command` auf macOS ruft `start.sh` auf und unterstützt damit ebenfalls dieselben Optionen.
