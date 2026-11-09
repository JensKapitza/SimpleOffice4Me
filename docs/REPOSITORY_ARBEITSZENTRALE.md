# Öffentliche Repository-Arbeitszentrale

Die native Flask-Ansicht **`/repository-workspace/`** bündelt Informationen zu
`JensKapitza/SimpleOffice4Me`. Sie ist öffentlich erreichbar, ohne ein
SimpleOffice-Konto anzulegen. Links stehen auf der Anmeldeseite und unter
**Mehr → Arbeitszentrale**. Bestehende Zugriffsrechte für Dokumente, Kontakte,
Administration und andere Office-Bereiche bleiben wirksam.

## Bedienung und Schreibschutz

- **Übersicht:** Board mit „offen“, „in Prüfung“, „blockiert“, „bereit zum Merge“
  und „erledigt“, zusätzlich eine Listenansicht. Filter nach Priorität, Bereich,
  Typ und Status sowie Textsuche.
- **Eintrag:** Ursache/offene Frage, bekannte Blocker, nächste Prüfschritte,
  Teststatus, benötigte Dokumentation, Commit und Originalquellen.
- **Arbeitsliste:** wiederverwendbare Prüfreihenfolge; Einträge vormerken und
  Notizen innerhalb des geöffneten Tabs ergänzen. Nur die booleschen Häkchen
  der allgemeinen Checkliste werden im Browser gespeichert. Repository-Daten,
  private Funde, freie Entwürfe und Notizen werden nicht in `localStorage`
  abgelegt. Sie können ausdrücklich als Markdown exportiert werden.
- **Dokumentationsbedarf:** datierte, quellengebundene Prüfbeispiele sowie
  lokale Entwürfe für noch zu dokumentierende Punkte. Vor einem neuen Issue
  die verlinkten bestehenden Tracker auf Erledigung und Duplikate prüfen.
- **Quellen & Abdeckung:** Abrufzeit, Teilabdeckung, Fehler und fehlende
  Berechtigungen separat anzeigen.

**Daten aktualisieren** lädt ausschließlich Repository-Daten neu. Es führt
keine Softwareinstallation und keinen CI-Neustart aus. Es gibt keine API zum
Mergen, Schließen, Zuweisen, Kommentieren, Freigeben oder Anlegen von Issues.
Die vorhandene automatische Fehlerberichterstattung wird für diesen Bereich
nicht aufgerufen, damit auch Fehler keinen GitHub-Schreibvorgang auslösen.

## Was die Einstufungen bedeuten

Priorität, Bereich und Planungsstatus sind lokale Einschätzungen. Explizite
P0–P3-Labels werden übernommen; andernfalls ordnet die Ansicht fehlgeschlagene
Prüfungen und hohe Security-Schweregrade vorsichtig ein. Titelbegriffe dienen
der Bereichszuordnung; diese Heuristik ist kein geprüfter fachlicher Befund.
Die zugrunde liegenden GitHub-Status und Labels werden nicht verändert.

CI-Läufe werden anhand des exakten Head-SHA zugeordnet. Alte PR-Commits werden
nicht als aktueller Fehler dargestellt; Push- und Pull-Request-Workflows werden
getrennt betrachtet. Abgebrochene, übersprungene, laufende und fehlgeschlagene
Prüfungen sind unterschiedliche Zustände. Ein Build belegt keinen Hardwaretest.
Fehlerursachen werden ohne Logprüfung als ungeklärt bezeichnet.

Grüne beobachtete Checks ergeben **keine automatische Merge-Bereitschaft**.
Branch-Schutzregeln, sämtliche offenen Review-Threads und vollständige
Akzeptanzkriterien werden nicht automatisch geprüft. Die Spalte „bereit zum
Merge“ bleibt daher ohne belegte Freigabe leer. Geschlossene Issues und PRs
sind kein Nachweis einer erfolgreichen Fehlerbehebung oder eines Tests.

Security-Daten werden nur bei erfolgreichem, berechtigtem Abruf bewertet.
`403`, `404`, Zeitlimit oder fehlende Anmeldung bedeuten **ungeprüft/nicht
zugänglich**, nicht „null Funde“ oder „sicher“. Secret Scanning wird nicht
abgerufen. Die Ansicht ist keine vollständige Security-Inventur.

Die Projektbeispiele zu Release-Dokumentation, Report-Reproduktion, WebDAV
und Geräteabnahme stammen aus dem geprüften Stand vom **09.11.2026**,
Commit `9085c06ed3a98fbf9cc2bc966d109d04741ceec3`. Sie bleiben ausdrücklich als
datierte Planungshinweise markiert; der aktuelle Handlungsbedarf wird daraus
nicht behauptet. Historische Alertzahlen aus #874 sind keine Live-Inventur.

## Optionale GitHub-Anmeldung

Der Public-Modus benötigt weder Token noch GitHub-App-Konfiguration. Für
zusätzliche Daten kann der Betreiber eine **GitHub App** einrichten:

1. In den GitHub-Entwicklereinstellungen eine GitHub App erstellen oder eine
   passende bestehende App verwenden. **Enable Device Flow** aktivieren.
2. Ausschließlich folgende Repository-Leserechte erteilen. Nicht benötigte
   Security-Rechte können entfallen; die Quellen bleiben dann sichtbar ungeprüft.
3. Die App für `JensKapitza/SimpleOffice4Me` installieren bzw. vom Berechtigten
   installieren lassen. Der anmeldende Nutzer muss ebenfalls Zugriff haben.
4. Die **Client-ID** der GitHub App konfigurieren, nicht deren numerische App-ID.
   Kein Client-Secret, privater App-Schlüssel oder PAT ist erforderlich.

| GitHub-App-Berechtigung | Stufe | Verwendung |
|---|---|---|
| Metadata | Read | Repository-Metadaten |
| Issues | Read | Issues und zuletzt geschlossene Einträge |
| Pull requests | Read | PRs und eingereichte Reviews |
| Actions | Read | Workflow-Läufe |
| Checks | Read | Check-Runs am PR-Commit |
| Commit statuses | Read | Kombinierter Commit-Status |
| Contents | Read | Aktueller Hauptbranch-Commit |
| Code scanning alerts | Read | Optional: offene Code-Scanning-Funde |
| Dependabot alerts | Read | Optional: offene Abhängigkeitsfunde |

Die Einstellung liegt in der vorhandenen nativen Konfigurationsdatei:

- Linux/macOS: `$XDG_CONFIG_HOME/simpleoffice/app-config.json`, ersatzweise
  `~/.config/simpleoffice/app-config.json`.
- Windows: `%APPDATA%\SimpleOffice\app-config.json`.

Den folgenden Abschnitt in die bestehende Datei **ergänzen**, andere
Einstellungen erhalten. Den Platzhalter durch die eigene Client-ID ersetzen:

```json
{
  "repository_workspace": {
    "github_client_id": "<Client-ID der eigenen GitHub App>"
  }
}
```

Alternativ ist `SIMPLEOFFICE_GITHUB_WORKSPACE_CLIENT_ID` als Prozess-Fallback
möglich. Die persistierte Konfiguration hat Vorrang. Ein leeres oder ungültiges
Format deaktiviert die optionale Anmeldung; Public bleibt verfügbar.

Die Webseite zeigt einen kurzlebigen Bestätigungscode und verlinkt fest auf
`https://github.com/login/device`. Dort Konto und App prüfen. Der Code wird
nur für die selbst gestartete Anmeldung verwendet. Die Anwendung pollt mit
dem von GitHub vorgegebenen Abstand; `slow_down` verlängert ihn.
Abbruch, Ablehnung und Ablauf werden getrennt behandelt.

Die GitHub-Anmeldung ist **keine SimpleOffice-Anmeldung** und erzeugt keine
Office-Berechtigungen. Ein GitHub-App-User-Token erhält nur die Schnittmenge
aus Rechten des Kontos, Rechten der App und für beide zugänglichen Repositories.
Der Login garantiert deshalb keinen Zugriff auf Security-Funde.

GitHub empfiehlt für gewöhnliche Webanwendungen den Web-Flow mit PKCE. Dieser
Bereich implementiert den ausdrücklich gewünschten Device Flow als optionale
Betriebsart, auch für lokale Desktop-Installationen. Im öffentlichen Betrieb
ist HTTPS erforderlich. HTTP wird nur bei gleichzeitig lokalem Host und
Loopback-Client akzeptiert; Proxy-Header gelten nur entsprechend der bestehenden
vertrauenswürdigen Proxy-Konfiguration.

## Sitzungen, Cache und Fehlerfälle

Access-Token und Device-Code werden serverseitig mit der vorhandenen
AES-GCM-Funktion verschlüsselt in zusätzlichen Tabellen der Auth-Datenbank
gespeichert. Die Flask-Cookie-Session enthält nur eine zufällige Referenz.
Der bestehende persistente Anwendungsschlüssel muss erhalten bleiben; nach
Schlüsselverlust ist eine erneute Anmeldung erforderlich.

Die Sitzung läuft spätestens nach acht Stunden ab, mit kurzem Sicherheitspuffer.
Ein von GitHub kürzer vorgegebener Ablauf wird eingehalten. Refresh-Tokens
werden nicht gespeichert oder genutzt; danach erneut anmelden. „Abmelden“
verwirft die lokale Verbindung und ihre privaten Daten, ohne eine
Repository-Änderung oder einen GitHub-Revoke-Aufruf auszuführen. Die App kann
zusätzlich in den eigenen GitHub-Einstellungen widerrufen werden.

Öffentliche Snapshots stammen ausschließlich aus anonymen GET-Anfragen.
Kontobezogene Snapshots sind separat und verschlüsselt; sie werden niemals
zum Public-Cache. Beide neuen Persistenzmodule sind in der S3-Datenquelleninventur
ausdrücklich als Authentifizierungs- beziehungsweise privater Cache-Zustand
ausgeschlossen; es gibt dafür keinen generischen S3-Export. Auth-, Status- und Datenantworten verwenden `private,
no-store`; der Service Worker persistiert diese Antworten nicht. CSRF-Schutz
gilt auch für Anmeldung, Polling, Abmeldung und den manuellen Refresh.

Der öffentliche Cache wird für **30 Minuten** geteilt, um das geringe anonyme
GitHub-Abrufbudget zu schützen. Angemeldete Snapshots gelten fünf Minuten;
manuelles Aktualisieren ist frühestens nach 60 Sekunden möglich. GitHub-Limits
verlängern die Wartezeit. Es gibt keine automatische Hintergrundaktualisierung
der Repository-Daten. Die Oberfläche nennt den Datenstand und den nächsten
möglichen Abruf.

Ein Abruf ist auf 24 API-Anfragen und 2 MiB je Antwort begrenzt. Nach einem
Zeitbudget von 45 Sekunden werden keine weiteren Anfragen begonnen; einzelne
Socket-Operationen haben einen Timeout von höchstens acht Sekunden. Dies ist
kein harter Gesamtzeitabbruch eines langsam eintreffenden Antwortstroms. Offene PRs/Issues haben
höchstens zwei Seiten à 100 Einträge; CI, Security und PR-Unterquellen sind
jeweils begrenzt. Höchstens acht PRs erhalten Detailanreicherung. Erreichte
Grenzen werden ausdrücklich als Teilabdeckung ausgewiesen. Andere Quellen
bleiben bei einem Teilfehler nutzbar. Es gibt keine frei eingebbaren
Repository-URLs oder universellen API-Proxy und keine Redirect-Weitergabe von
Zugangsdaten.

## Prüfen

Die neuen Regressionstests verwenden synthetische GitHub-Antworten und
getrennte temporäre Datenbanken. Sie melden sich nicht bei GitHub an:

```bash
python -m unittest discover -s tests -p 'test_repository_workspace*.py' -v
node --test tests/repository_workspace_frontend.test.cjs
```

Ein echter Device-Login erfordert die konfigurierte App und eine bewusste
Autorisierung durch den Nutzer. Mock-Tests sind kein Nachweis dieser externen
Konfiguration. Es werden weder GitHub-App-Registrierung noch Rechteänderungen
automatisch vorgenommen.

## Offizielle Grundlagen

- [GitHub-App-User-Token, Berechtigungsschnittmenge und Device Flow](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)
- [GitHub-App-Sicherheitspraktiken](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/best-practices-for-creating-a-github-app)
- [Device Flow aktivieren](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration)
- [REST-Abruflimits und bedingte Anfragen](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)
- [Code-Scanning-Endpunkte](https://docs.github.com/en/rest/code-scanning/code-scanning)
- [Dependabot-Endpunkte](https://docs.github.com/en/rest/dependabot/alerts)
