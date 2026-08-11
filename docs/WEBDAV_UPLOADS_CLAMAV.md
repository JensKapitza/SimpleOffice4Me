# ClamAV-Prüfung vor WebDAV-Uploads

## Zweck und Sicherheitsziel

LibreOffice, FreeFileSync, Nautilus, Windows Explorer, Finder und andere
WebDAV-Clients senden neue oder geänderte Dateien mit `PUT`. Eine solche Datei
kann Schadcode enthalten, selbst wenn sie nur wie ein Office-Dokument, PDF oder
Bild aussieht. SimpleOffice kann deshalb jeden WebDAV-`PUT` zunächst in einer
privaten Quarantäne speichern und erst nach einem sauberen ClamAV-Ergebnis in
den sichtbaren Dokumentbestand übernehmen.

Die Prüfung ist optional und standardmäßig deaktiviert, damit bestehende
Installationen ohne ClamAV nicht überraschend alle Schreibvorgänge verlieren.
Wer sie aktiviert, erhält dagegen ein **fail-closed**-Verhalten: Fund,
Scannerfehler, Zeitüberschreitung oder unsichere Quarantäne veröffentlichen
keine neue Dateiversion. Ein vorhandenes Dokument bleibt bytegenau erhalten.

Die Funktion ergänzt den bestätigungspflichtigen Workflow für
[EML-Anhänge](ANHAENGE_CLAMAV.md). EML-Dateien selbst bleiben unverändert; ihre
Anhänge werden erst nach Bestätigung extrahiert. Direkte WebDAV-Uploads sind
bereits eine ausdrückliche Benutzerhandlung und werden unmittelbar vor ihrer
Veröffentlichung geprüft.

## Aktivierung

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install clamav clamav-daemon
sudo freshclam
sudo systemctl enable --now clamav-daemon
```

Danach in der geschützten Dienstkonfiguration, nicht im Repository:

```bash
SIMPLEOFFICE_WEBDAV_CLAMAV=1
SIMPLEOFFICE_WEBDAV_QUARANTINE_MIB=1024
SIMPLEOFFICE_CLAMAV_SCANNER=/usr/bin/clamdscan
SIMPLEOFFICE_CLAMAV_TIMEOUT=120
```

Nach einer Änderung der Umgebung muss der SimpleOffice-Dienst neu gestartet
werden. `SIMPLEOFFICE_WEBDAV_QUARANTINE_MIB` ist die maximale Größe der
privaten WebDAV-Quarantäne. Der Wert wird auf 1 bis 65.536 MiB begrenzt; der
Standard ist 1.024 MiB. Die normale Upload-Grenze
`SIMPLEOFFICE_MAX_UPLOAD_MIB` gilt zusätzlich.

`SIMPLEOFFICE_CLAMAV_SCANNER` darf ausschließlich auf einen absoluten Pfad zu
`clamdscan` oder `clamscan` zeigen. Ohne Vorgabe wird zuerst `clamdscan`, dann
`clamscan` im `PATH` gesucht. `clamdscan` ist für wiederholte Desktop-Speicher-
vorgänge schneller; `clamscan` funktioniert ohne laufenden Daemon. Unter Unix
verwendet SimpleOffice für `clamdscan` `--fdpass`, damit der lokale Daemon die
private Datei über einen Dateideskriptor erhält.

Der ClamAV-Daemon sollte über einen lokalen Unix-Socket angebunden werden. Sein
TCP-Socket besitzt keine eigene Authentifizierung und darf nicht öffentlich
erreichbar sein. SimpleOffice startet keine Shell und fügt keine vom Client
kontrollierten Argumente in einen Befehl ein.

## Signaturen aktualisieren und Server jetzt prüfen

Aktuelle Signaturen sind Voraussetzung für sinnvolle Ergebnisse. Die
Distribution kann `freshclam` als Dienst oder Timer ausführen; alternativ:

```bash
sudo freshclam
sudo systemctl restart clamav-daemon
```

Über **Dokumente → Sicherheit** (`/documents/security`) sehen Benutzer aus
`SIMPLEOFFICE_SECURITY_ADMINS` Scannerstatus und Version. Dort stehen außerdem
**Signaturen aktualisieren** und **Bestand jetzt prüfen** zur Verfügung. Ohne
diese Umgebungsvariable gibt es bewusst keinen Web-Administrator. Der
Bestandsscan ändert keine Dokumentrechte und löscht Funde nicht automatisch.

Beispiel:

```bash
SIMPLEOFFICE_SECURITY_ADMINS=alice,bob
```

Die offizielle ClamAV-Dokumentation verlangt für `clamd` eine vorhandene und
aktuelle Signaturdatenbank. `freshclam` verwaltet die offiziellen Datenbanken
und seine Konfiguration; Details stehen unter
[Scanning](https://docs.clamav.net/manual/Usage/Scanning.html) und
[Signature Management](https://docs.clamav.net/manual/Usage/SignatureManagement.html).

## Ablauf eines PUT

SimpleOffice verarbeitet einen Upload in dieser Reihenfolge:

1. App-Passwort, Benutzerpfad und Lese-/Schreibumfang prüfen.
2. Pfad, Symlinks, Spezialdateien, Elternordner und Lock-Token prüfen.
3. `If-Match`, `If-None-Match` und weitere HTTP-/DAV-Bedingungen auswerten.
4. Optionalen `Content-Digest` verifizieren und Speichergrenzen prüfen.
5. Payload exklusiv als zufällig benannte `.pending`-Datei mit Modus `0600`
   unter `.simpleoffice-meta/webdav-upload-quarantine` schreiben und `fsync`
   ausführen.
6. ClamAV mit festem Zeitlimit ausführen.
7. Nur bei `clean` die Quarantänedatei entfernen und den bereits vorhandenen
   atomaren, versionierten WebDAV-Schreibpfad ausführen.

Dadurch kostet eine ungültige Anmeldung, ein veraltetes ETag oder ein falscher
Digest keinen Scan. Umgekehrt liegt ein noch ungeprüfter Inhalt niemals im
sichtbaren WebDAV-Baum. Die bestehende Mutationssperre serialisiert Prüfung und
Publikation, sodass zwischen ETag-Prüfung, Scan und atomarem Austausch keine
zweite PUT-Version unbemerkt dazwischengeschoben werden kann.

## Ergebnis und Fehlerverhalten

- `201` oder `204`: ClamAV meldet `clean`; Anlage beziehungsweise Änderung
  wurde anschließend atomar und mit Versionshistorie veröffentlicht.
- `422 Unprocessable Content`: ClamAV hat Schadcode gemeldet. Der Upload bleibt
  zufällig benannt mit Endung `.infected` in der privaten Quarantäne.
- `503 Service Unavailable` mit `Retry-After: 60`: Scanner fehlt, ist nicht
  erreichbar, läuft ins Zeitlimit oder die sichere Quarantäne ist beschädigt.
  Die Payload bleibt, soweit möglich, als `.error` zur administrativen Prüfung.
- `507 Insufficient Storage`: Die konfigurierte Quarantänekapazität reicht
  nicht. Die DAV-Fehlerbedingung lautet `sufficient-disk-space`.

Diese Antworten enthalten `Cache-Control: no-store`. Scanner-Ausgaben und
interne Pfade werden nicht an Desktop-Clients zurückgegeben. LibreOffice oder
ein Dateimanager darf nach einem `503` später erneut speichern. Bei `422` soll
die lokale Datei isoliert und untersucht werden; ein automatischer Retry wäre
nicht sinnvoll.

Andere WebDAV-Fehler behalten ihre Bedeutung: `401` für Zugangsdaten, `403`
für fehlende Schreibrechte, `409` für einen fehlenden Elternordner, `412` für
eine fehlgeschlagene Bedingung, `423` für eine Sperre und `428` für einen
riskanten Blind-Overwrite.

## Normative Standards und abgeleitete Entscheidungen

### RFC 4918 (WebDAV)

- Nach [RFC 4918 §9.7.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.7.1)
  muss ein erfolgreicher `PUT` die durch `GET` gelieferte Entität ersetzen.
  SimpleOffice antwortet daher erst dann erfolgreich, wenn Scan und atomare
  Speicherung abgeschlossen sind. Quarantäne allein ist kein erfolgreicher
  `PUT`.
- Fehlt ein notwendiger Elternordner, verlangt §9.7.1 `409 Conflict` statt
  stiller Ordneranlage. Die Virenprüfung läuft erst nach dieser Pfadprüfung.
- Ein Server darf `PUT` auf Sammlungen mit `405 Method Not Allowed` ablehnen.
  SimpleOffice akzeptiert Datei-`PUT`, während Ordner mit `MKCOL` entstehen.
- [RFC 4918 §8.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-8.6)
  beschreibt ETags als Schutz vor verlorenen Änderungen. Bestehende Dateien
  verlangen weiterhin `If-Match` oder ein gültiges Lock-Token; ein Scan ersetzt
  diesen Konfliktschutz nicht.
- [RFC 4918 §9.10](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10)
  und §9.11 definieren `LOCK`/`UNLOCK`. Der vorhandene Lock wird vor dem Scan
  geprüft und bis nach der Publikation gehalten.

### HTTP Semantics (RFC 9110)

- [RFC 9110 §13](https://www.rfc-editor.org/rfc/rfc9110.html#section-13)
  verlangt die Auswertung von Vorbedingungen vor der Methodenhandlung. Deshalb
  laufen ETag- und DAV-If-Prüfungen vor ClamAV und vor jeder Mutation.
- [§15.5.21](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.21)
  definiert `422 Unprocessable Content`: Syntax und Übertragung sind gültig,
  der enthaltene Schadcode verhindert aber die angeforderte Verarbeitung.
- [§15.6.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.6.4)
  definiert `503 Service Unavailable` für eine vorübergehende Nichtverfügbarkeit
  und erlaubt `Retry-After`. Scanner-Ausfälle werden daher nicht als Erfolg,
  Fund oder allgemeiner `500` dargestellt.

### MUST, SHOULD und MAY der Umsetzung

- **MUST:** Kein ungeprüfter Inhalt wird bei aktivierter Funktion sichtbar.
- **MUST:** Fehlgeschlagene Rechte-, Lock-, Pfad- und HTTP-Bedingungen verändern
  weder Bestand noch Quarantäne und starten keinen Scanner.
- **MUST:** Ein Fund oder Scannerfehler überschreibt keine vorhandene Revision.
- **MUST:** Quarantänedateien sind nicht über WebDAV, Download oder Freigabelink
  adressierbar und dürfen keine Symlinks oder Spezialdateien sein.
- **SHOULD:** Signaturen werden automatisiert aktuell gehalten; Betreiber prüfen
  Scannerstatus und Quarantänebelegung.
- **SHOULD:** Desktop-Clients respektieren `Retry-After` und behalten bei
  Fehlschlag ihre lokale Arbeitskopie.
- **MAY:** Betreiber verwenden `clamscan` statt `clamdscan`, wenn geringe
  Uploadzahl wichtiger als Latenz ist.

## Audit, Datenschutz und Aufbewahrung

Jede Prüfung wird in `.simpleoffice-meta/malware-scan.json` mit Zeitpunkt,
Benutzer, Zielpfad, Größe, SHA-256, Engine und Ergebnis registriert. Zusätzlich
entsteht ein unveränderbarer Audit-Snapshot mit einer der Aktionen:

- `webdav_upload_malware_scanned`
- `webdav_upload_malware_blocked`
- `webdav_upload_malware_scan_failed`

Der Client erhält weder detaillierte Signaturnamen noch Serverpfade. Inhalte
werden nicht an einen externen Dienst übertragen. Dateiname und Zielpfad sind
personenbezogene Metadaten und unterliegen deshalb denselben Zugriffs- und
Sicherungsregeln wie die Dokumenthistorie.

SimpleOffice löscht `.infected`- oder `.error`-Dateien nicht automatisch. So
werden Aufbewahrungsregeln nicht still verändert und Fehlalarme bleiben
nachvollziehbar. Administratoren müssen Quarantänedaten nach dokumentierter
Prüfung, Sicherung und geltender Aufbewahrungsregel entfernen. ClamAVs
automatische Löschoption wird bewusst nie benutzt.

## Kompatibilität und Grenzen

Die Funktion benötigt keine Änderung am WebDAV-Client. Sie gilt für neue
Dateien, Baum-Updates und stabile Dokument-ID-URLs gleichermaßen. GET, HEAD,
PROPFIND, COPY, MOVE und lokale Wiederherstellung werden nicht erneut gescannt;
der Bestandsscan deckt vorhandene Dateien separat ab.

ClamAV reduziert Risiken, beweist aber keine Unschädlichkeit. Unbekannte
Schadsoftware, Makrologik, passwortgeschützte Archive und Parserfehler können
unerkannt bleiben. SimpleOffice entpackt Archive beim WebDAV-`PUT` nicht selbst
und führt hochgeladene Dateien nicht aus. Eine manuelle Freigabe infizierter
Uploads in den sichtbaren Bestand ist absichtlich nicht implementiert.

Die Quarantänekapazität ist eine Anwendungsgrenze, kein Ersatz für
Dateisystemkontingente oder Monitoring. Bei mehreren unabhängigen
SimpleOffice-Instanzen muss der Dokumentwurzelpfad samt Dateisperren gemeinsam
und konsistent bereitgestellt werden.

## Migration, Deaktivierung und Rückkehr

Es gibt keine Datenbankmigration und keine Rechteausweitung. Beim ersten
aktivierten Upload entstehen ausschließlich
`.simpleoffice-meta/webdav-upload-quarantine` und zusätzliche Scan-/Audit-
Einträge. Vorhandene Dokumente werden nicht verändert.

Zum sofortigen Rückkehrverhalten `SIMPLEOFFICE_WEBDAV_CLAMAV=0` setzen und den
Dienst neu starten. WebDAV schreibt dann wie zuvor mit ETag-, Lock-, Digest-,
Quota-, Versions- und Audit-Schutz, aber ohne vorgeschalteten Scan. Bereits
isolierte Quarantänedateien bleiben erhalten und müssen kontrolliert behandelt
werden. Ein Code-Rollback kann die privaten Dateien weiterhin ignorieren.

## Automatisierte Tests

Die Tests decken ab:

- saubere Neu- und Bestandsdateien über Baum- und stabile ID-URLs;
- private `0600`-Zwischendatei und leere Quarantäne nach Erfolg;
- Fund bei Neuanlage und Überschreiben ohne sichtbare Mutation;
- Scannerfehler mit `503`, `Retry-After`, Audit und `.error`-Beleg;
- Kapazitätsfehler mit `507` vor Scanner und Mutation;
- Rechte-, ETag- und Digest-Ablehnung vor dem ersten Scanneraufruf;
- deaktivierte Funktion als rückwärtskompatibler Standard;
- vollständige bestehende WebDAV-Positiv-, Rechte-, Pfad-, Lock-, Konflikt-,
  Wiederanlauf- und Interoperabilitätssuite.
