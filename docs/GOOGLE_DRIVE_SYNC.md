# Google-Drive-Synchronisation

## Ziel

SimpleOffice4Me kann einen klar abgegrenzten lokalen Dokumentbereich mit Google Drive synchronisieren. Dabei soll weder der gesamte Google Drive freigegeben noch bei Konflikten oder Löschungen stillschweigend Daten überschrieben werden.

Der lokale Sync-Bereich ist:

```text
<DOCUMENT_ROOT>/GoogleDrive/
```

Auf Google Drive wird beim ersten Abgleich ein von SimpleOffice verwalteter Ordner angelegt:

```text
SimpleOffice4Me
```

Nur dieser von der Anwendung verwaltete Bereich wird für den normalen Datei-Sync verwendet.

## Warum `drive.file` statt Vollzugriff?

SimpleOffice fordert den OAuth-Scope

```text
https://www.googleapis.com/auth/drive.file
```

an. Dieser Scope erlaubt der Anwendung den Zugriff auf Dateien, die von SimpleOffice erstellt oder der Anwendung ausdrücklich freigegeben wurden. Ein pauschaler Zugriff auf den gesamten Drive ist nicht notwendig.

Das ist bewusst enger als `drive` oder `drive.readonly` und reduziert sowohl Datenschutzrisiken als auch den Umfang einer möglichen Google-Verifizierung.

## Zwei Autorisierungswege

### Browser/Server

Die normale Browser-Version verwendet weiterhin den vorhandenen Google-Web-OAuth-Client und `/auth/google/callback`. Dieser Weg kann einen Refresh-Token erhalten und eignet sich damit für länger laufende serverseitige Nutzung.

Konfiguration:

```text
SIMPLEOFFICE_GOOGLE_CLIENT_ID=...
SIMPLEOFFICE_GOOGLE_CLIENT_SECRET=...
SIMPLEOFFICE_GOOGLE_REDIRECT_URI=https://<simpleoffice-host>/auth/google/callback
```

Alternativ kann eine geschützte Google-OAuth-JSON-Datei über `SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE` verwendet werden.

### Android-APK

Die APK verwendet **keinen** lokalen Browser-Callback und keinen Token-Transport über ein Custom Scheme. Google-Autorisierung läuft nativ über Google Identity Services `AuthorizationClient` aus `play-services-auth`.

Für die APK muss in Google Cloud ein Android-OAuth-Client für die Paketkennung

```text
de.simpleoffice4me.android
```

und den SHA-Fingerprint der verwendeten Signatur angelegt werden. Debug-/Testsignatur und Produktionssignatur benötigen entsprechend passende Einträge. Außerdem muss die Google Drive API aktiviert sein.

Die APK fordert nativ nur `drive.file` an. Ein Web-Client-Secret wird nicht in die APK eingebettet.

## Native Android-Autorisierung im Detail

1. Der Benutzer ist bereits in der lokalen SimpleOffice-WebView angemeldet.
2. **Google verbinden** oder **Jetzt synchronisieren** wird in der APK abgefangen.
3. Die WebView übergibt ausschließlich Aktion (`connect`/`sync`) und den aktuellen CSRF-Token an die bereits abgesicherte lokale JavaScript-Bridge.
4. `AndroidGoogleAuthorization` baut eine `AuthorizationRequest` mit genau `drive.file` und startet `Identity.getAuthorizationClient(...).authorize(...)`.
5. Google Play Services zeigt bei Bedarf die native Einwilligung an.
6. Das Access-Token bleibt ausschließlich in Java. Es wird weder JavaScript noch einem Android-Intent oder Deep Link übergeben.
7. Java sendet das Token per POST an:

```text
http://127.0.0.1:8765/settings/google-drive/android-token
```

8. Der POST enthält das bestehende WebView-Session-Cookie und `X-CSRF-Token`.
9. Flask akzeptiert die Übergabe nur von Loopback, nur mit dem APK-User-Agent, nur für `connect`/`sync`, nur mit gewährtem `drive.file` und nur für den bereits angemeldeten SimpleOffice-Benutzer mit `documents`- und `sync`-Rechten.
10. Das Access-Token wird mit der bestehenden SimpleOffice-Verschlüsselung in SQLite gespeichert. Ein vorhandener Refresh-Token aus dem Browser-OAuth wird dabei nicht entfernt.
11. Bei `sync` startet direkt der normale Drive-Abgleich. Danach lädt die APK die lokale Drive-Seite mit einem festen Status (`connected`, `synced`, `cancelled`, `error` oder `unavailable`) neu.

Die Android-App holt vor manuellen Drive-Aktionen erneut eine native Google-Autorisierung. Für unbeaufsichtigten Hintergrund-Sync ohne Benutzerinteraktion ist weiterhin ein serverseitiger OAuth-Weg mit Refresh-Token und öffentlich erreichbarem HTTPS-Callback erforderlich. SimpleOffice versucht nicht, diese Einschränkung mit Loopback-Callbacks oder Custom-Scheme-Tokens zu umgehen.

## Token-Speicherung

Access- und Refresh-Tokens werden mit der vorhandenen SimpleOffice-Schutzfunktion verschlüsselt in SQLite gespeichert. Refresh-Tokens werden nicht an Browser oder Frontend ausgeliefert.

Ein nativ von Android geliefertes Access-Token ersetzt den aktuell verwendeten Access-Token, lässt einen bereits vorhandenen Refresh-Token aber bestehen. Die bekannte Scope-Menge wird zusammengeführt, damit eine vorherige Browser-Freigabe für Kontakte/Kalender nicht durch eine reine Drive-Autorisierung aus der APK aus den Metadaten verschwindet.

## Sync-Richtungen

Pro Benutzer kann eingestellt werden:

- `bidirectional`: beide Seiten abgleichen,
- `upload_only`: lokale Änderungen nach Google Drive übertragen,
- `download_only`: Drive-Änderungen lokal übernehmen,
- `none`: Datenübertragung deaktivieren.

Zusätzlich gibt es einen separaten Aktiv-Schalter.

## Zuordnung und Datenmodell

Die SQLite-Tabelle `google_drive_state` speichert unter anderem:

- Benutzer,
- ID des verwalteten Drive-Ordners,
- aktuellen Changes-Page-Token,
- Sync-Richtung,
- letzten erfolgreichen Lauf,
- letzten Fehler.

`google_drive_link` ordnet Drive-Objekte lokalen Dokumenten zu. Gespeichert werden unter anderem:

- Google `fileId`,
- lokale SimpleOffice-Dokument-ID,
- relativer lokaler Pfad,
- MIME-Type,
- Google-Version und `modifiedTime`,
- Google-MD5, soweit vorhanden,
- lokaler SHA-256,
- letzter erfolgreich synchronisierter lokaler Hash,
- letzte erfolgreich synchronisierte Google-Version,
- Status und Fehlermeldung.

Für Android ist keine zusätzliche OAuth-Handoff-Tabelle erforderlich. Die Autorisierung wird von Google Play Services durchgeführt; die Übergabe an Flask ist an die bereits bestehende lokale Session und CSRF-Prüfung gebunden.

## Inkrementeller Abgleich

Beim ersten Sync wird zunächst ein Google-Changes-Starttoken bezogen und anschließend der verwaltete Ordner vollständig gelesen. Danach werden nur noch Änderungen über die Drive-`changes`-API abgefragt.

Das ist absichtlich anders als ein permanenter vollständiger Verzeichnisscan. Es reduziert API-Aufrufe und skaliert besser bei größeren Beständen.

Ein manueller **Vollabgleich vormerken** setzt nur den gespeicherten Changes-Cursor zurück. Beim nächsten Lauf wird der verwaltete Drive-Baum erneut vollständig abgeglichen.

## Konflikte

SimpleOffice speichert für beide Seiten einen Baseline-Zustand:

```text
last_synced_local_sha256
last_synced_remote_version
```

Wenn seit dieser Baseline nur eine Seite geändert wurde, kann die Änderung übertragen werden.

Wenn **lokal und Google Drive** seit dem letzten erfolgreichen Sync geändert wurden, wird der Zustand `conflict` gesetzt. Es erfolgt kein automatisches Überschreiben.

Das ist absichtlich konservativ: Bei Geschäftsdokumenten ist eine sichtbare Konfliktentscheidung sicherer als eine vermeintlich intelligente automatische Wahl.

## Löschungen

Ein Remote-Löschvorgang löscht die lokale Datei **nicht automatisch**. Die Verknüpfung erhält `remote_deleted` und die lokale Datei bleibt verfügbar.

Ebenso wird eine lokal fehlende Datei nicht automatisch aus Google Drive gelöscht.

Warum: Ein versehentliches Löschen auf einer Seite darf nicht unbemerkt beide Kopien vernichten. Eine spätere explizite Lösch-/Papierkorbregel kann auf diesem Statusmodell aufbauen.

## Google Docs, Sheets und Slides

Google-native Dokumente besitzen keine normale Binärdatei wie PDF, DOCX oder XLSX. Sie werden daher aktuell nicht zwangsweise exportiert.

Sie erscheinen als `cloud_only` und behalten den `webViewLink`, sodass sie aus der Drive-Übersicht direkt bei Google geöffnet werden können.

Das vermeidet unklare Fragen wie: Ist ein automatisch erzeugtes PDF noch dasselbe Dokument? Ein späterer Export kann als **Ableitung** ergänzt werden, ohne die Google-Datei als neues unabhängiges Dokument zu behandeln.

## Ordner

Unterordner im verwalteten Bereich werden rekursiv gespiegelt. Lokale Unterordner werden bei Bedarf als Drive-Ordner angelegt; Drive-Unterordner werden lokal angelegt und durch die normale SimpleOffice-Ordnerpolicy geschützt.

Die Verschachtelung ist zum Schutz vor fehlerhaften oder bösartigen Baumstrukturen auf 20 Ebenen begrenzt. Ein vollständiger Lauf ist außerdem auf 10.000 verwaltete Remote-Objekte begrenzt.

## Dateigrößen

Der Standard für einen einzelnen Drive-Sync-Transfer beträgt 128 MiB und kann konfiguriert werden:

```text
SIMPLEOFFICE_GOOGLE_DRIVE_MAX_MIB=128
```

Der Wert ist auf maximal 512 MiB begrenzt und kann das globale SimpleOffice-Uploadlimit nicht überschreiten.

Der aktuelle Upload verwendet Drive `multipart` und hält den Dateiinhalt für den Transfer im Speicher. Für sehr große Dateien sollte später ein resumable Upload ergänzt werden.

## Sicherheit

Die Drive-HTTP-Schicht akzeptiert nur HTTPS und eine enge Google-Hostliste. Redirects werden erneut validiert. Antwortgrößen sind begrenzt.

Dateinamen aus Google Drive werden vor der lokalen Verwendung bereinigt. Pfadtrenner, Steuerzeichen und unter Windows reservierte Dateinamen werden nicht ungeprüft ins Dateisystem übernommen.

Remote-Dateien werden ausschließlich unterhalb von `GoogleDrive/` geschrieben. Die vorhandene DocumentStore-Pfadsicherheit und revisionsfähige Inhaltsersetzung bleibt dabei erhalten.

Für den Android-Token-Endpunkt gelten zusätzlich:

- nur Loopback (`127.0.0.1`/`::1`),
- bestehende SimpleOffice-Sitzung erforderlich,
- globale CSRF-Prüfung plus expliziter `X-CSRF-Token`,
- APK-User-Agent erforderlich,
- JSON-Body maximal 16 KiB,
- Access-Token maximal 8192 Zeichen,
- Scope-Liste maximal 16 Einträge,
- `drive.file` muss enthalten sein,
- Aktion ausschließlich `connect` oder `sync`,
- keine Tokenwerte in Audit-Details oder Antworten.

## UI und Android

Die Drive-Seite ist über **Drive** in der Hauptnavigation erreichbar. Aktionsbuttons verwenden flexible Zeilen und zusätzliche `safe-area-inset-bottom`-Reserve, damit sie auf Android-Geräten nicht hinter der Systemnavigation verschwinden.

In der APK werden **Google verbinden** und **Jetzt synchronisieren** nativ autorisiert. Normale Browser verwenden weiterhin den Web-OAuth- beziehungsweise gespeicherten Server-Token-Weg.

## Aktuelle bewusste Grenzen

- Kein automatisches hartes Löschen auf der Gegenseite.
- Google-native Docs/Sheets/Slides werden verlinkt, noch nicht automatisch exportiert.
- Kein Google Picker für beliebige bestehende Drive-Dateien; der Standard bleibt der app-verwaltete Ordner.
- Dateien über dem konfigurierten Limit werden markiert und übersprungen.
- Konflikte werden sichtbar gemacht, aber noch nicht in einem Drei-Wege-Merge aufgelöst.
- Native Android-Drive-Autorisierung benötigt Google Play Services. Auf Google-freien ROMs bleibt der restliche SimpleOffice-Funktionsumfang verfügbar, Drive muss dort über einen serverseitig autorisierten Token oder später eine alternative Integration genutzt werden.

Diese Grenzen sind Absicht: Die Implementierung priorisiert Datenverlustschutz, minimale Google-Berechtigungen und nachvollziehbare Authentisierungsgrenzen vor maximal aggressiver Spiegelung.