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

## Vorhandene Google-Anbindung

Die Anwendung hatte bereits Google OAuth für Anmeldung, Google People und Google Calendar. Die Drive-Anbindung verwendet dieselben verschlüsselt gespeicherten OAuth-Token. Neu ist ein gemeinsamer Token-Refresh-Service (`app/google_tokens.py`), damit ein späterer Sync nicht davon abhängt, dass sich der Benutzer gerade neu bei Google angemeldet hat.

Access- und Refresh-Tokens werden weiterhin mit der vorhandenen SimpleOffice-Schutzfunktion verschlüsselt in SQLite gespeichert. Refresh-Tokens werden nicht an Browser oder Frontend ausgeliefert.

## Einrichtung in Google Cloud

1. In der Google Cloud Console ein Projekt auswählen oder anlegen.
2. **Google Drive API** aktivieren.
3. Für den vorhandenen OAuth-Webclient dieselbe Callback-URL verwenden, die bereits für die Google-Anmeldung konfiguriert ist, typischerweise:

```text
https://<simpleoffice-host>/auth/google/callback
```

4. SimpleOffice konfigurieren. Unterstützt werden entweder Umgebungsvariablen:

```text
SIMPLEOFFICE_GOOGLE_CLIENT_ID=...
SIMPLEOFFICE_GOOGLE_CLIENT_SECRET=...
SIMPLEOFFICE_GOOGLE_REDIRECT_URI=https://<simpleoffice-host>/auth/google/callback
```

oder eine Google-OAuth-JSON-Datei:

```text
SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE=/geschuetzter/pfad/client_secret.json
```

5. In SimpleOffice **Drive** öffnen und **Google verbinden** bzw. **Google Drive neu freigeben** wählen.

Die Drive-Seite startet eine inkrementelle OAuth-Freigabe und verwendet anschließend den bereits bestehenden `/auth/google/callback`. Deshalb ist keine zweite Callback-URL erforderlich.

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

Dadurch wird eine neu kodierte oder anderweitig binär veränderte Datei nicht nur anhand ihres Dateinamens erkannt.

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

## UI und Android

Die Drive-Seite ist über **Drive** in der Hauptnavigation erreichbar. Aktionsbuttons verwenden flexible Zeilen und zusätzliche `safe-area-inset-bottom`-Reserve, damit sie auf Android-Geräten nicht hinter der Systemnavigation verschwinden.

## Aktuelle bewusste Grenzen

- Kein automatisches hartes Löschen auf der Gegenseite.
- Google-native Docs/Sheets/Slides werden verlinkt, noch nicht automatisch exportiert.
- Kein Google Picker für beliebige bestehende Drive-Dateien; der Standard bleibt der app-verwaltete Ordner.
- Dateien über dem konfigurierten Limit werden markiert und übersprungen.
- Konflikte werden sichtbar gemacht, aber noch nicht in einem Drei-Wege-Merge aufgelöst.

Diese Grenzen sind Absicht: Die erste Implementierung priorisiert Datenverlustschutz und minimale Google-Berechtigungen vor maximal aggressiver Spiegelung.
