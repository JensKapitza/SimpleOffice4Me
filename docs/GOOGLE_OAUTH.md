# Google-Anmeldung einrichten

1. In der [Google Cloud Console](https://console.cloud.google.com/apis/credentials) ein OAuth-Client vom Typ **Webanwendung** anlegen.
2. Als autorisierte Weiterleitungs-URL eintragen:

   `https://DEIN-HOST/auth/google/callback`

3. Vor dem Start von SimpleOffice diese Variablen setzen:

```bash
export SIMPLEOFFICE_SECRET_KEY='langer-zufaelliger-serverwert'
export SIMPLEOFFICE_GOOGLE_CLIENT_ID='...apps.googleusercontent.com'
export SIMPLEOFFICE_GOOGLE_CLIENT_SECRET='...'
export SIMPLEOFFICE_GOOGLE_REDIRECT_URI='https://DEIN-HOST/auth/google/callback'
```

Der Server muss über HTTPS erreichbar sein. Für einen Reverse Proxy ist bereits `SIMPLEOFFICE_TRUSTED_PROXY_HOPS=1` vorgesehen. Nach dem Neustart erscheint die Schaltfläche **Mit Google anmelden** bei Anmeldung und Registrierung. Beim ersten Google-Login wird ein lokales SimpleOffice-Konto angelegt und dauerhaft über die Google-Subject-ID zugeordnet.
