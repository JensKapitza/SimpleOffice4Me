# GitHub-Fehlerberichte

SimpleOffice kann unbehandelte Anwendungsfehler automatisch als deduplizierte GitHub-Issues melden. Endbenutzer benoetigen dafuer keinen GitHub-Login.

## Datenschutzprinzip

Die Uebertragung verwendet eine feste Allowlist. An GitHub gehen nur:

- Request-ID
- stabiler Fehler-Fingerprint
- Exception-Typ, jedoch **nicht die Exception-Nachricht**
- Flask-Endpoint und HTTP-Methode
- optional die konfigurierte App-Version
- bereinigte Stack-Koordinaten: Dateiname, Zeile und Funktionsname beziehungsweise Template, Zeile und Variablenname

Nicht uebertragen werden insbesondere Request-Body, Query-Parameter, Header, Cookies, Sessiondaten, Benutzerkennung, Kundendaten, Datenbankinhalte, Umgebungsvariablen, Logdateien oder Anhaenge.

Technische Fehlerdetails werden nur einem angemeldeten SimpleOffice-Administrator auf der lokalen 500-Seite angezeigt.

## GitHub-Zugang

Empfohlen ist langfristig eine dedizierte GitHub App. Fuer eine schnelle Einzelinstallation reicht ein Fine-Grained Personal Access Token eines separaten technischen GitHub-Kontos.

Das Token darf nur Zugriff auf `JensKapitza/SimpleOffice4Me` erhalten. Als Repository-Berechtigung wird `Issues: Read and write` benoetigt. Keine Contents-, Administration- oder Secrets-Schreibrechte vergeben.

Das Token wird niemals in den Quellcode oder die SimpleOffice-Datenbank geschrieben. Es wird ausschliesslich zur Laufzeit aus einer Umgebungsvariable gelesen.

## Konfiguration

```bash
export SIMPLEOFFICE_GITHUB_ERROR_REPORTING=1
export SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY=JensKapitza/SimpleOffice4Me
export SIMPLEOFFICE_GITHUB_ERROR_TOKEN='github_pat_...'
```

Optional kann ein bereits existierendes GitHub-Label angegeben werden:

```bash
export SIMPLEOFFICE_GITHUB_ERROR_LABEL='automatic-error-report'
```

Ohne `SIMPLEOFFICE_GITHUB_ERROR_LABEL` wird absichtlich kein Label gesendet, damit ein fehlendes Repository-Label die Issue-Erstellung nicht blockiert.

Die App-Version kann fuer Diagnosezwecke mitgegeben werden:

```bash
export SIMPLEOFFICE_VERSION='2026.09.11'
```

## Verhalten

Bei einem unbehandelten Fehler speichert SimpleOffice weiterhin seinen lokalen strukturierten Fehlerdatensatz. Danach sucht der Reporter nach einem offenen Issue mit demselben Fingerprint. Existiert bereits eines, wird dessen Nummer verwendet. Andernfalls wird ein neues Issue angelegt.

Ist GitHub nicht erreichbar, ist das Token ungueltig oder ist die Funktion deaktiviert, bleibt die normale lokale Fehlerbehandlung aktiv. Ein Fehler des Reporters darf keinen weiteren Anwendungsfehler ausloesen.

## Technisches GitHub-Konto

Ein GitHub-Konto und dessen Token koennen nicht programmgesteuert durch SimpleOffice angelegt werden. Das technische Konto muss einmalig direkt bei GitHub erstellt und das Fine-Grained Token dort erzeugt werden. Anschliessend wird nur der Tokenwert auf dem SimpleOffice-Host als Secret/Umgebungsvariable hinterlegt.
