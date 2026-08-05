# Sichere Anhänge aus E-Mail und Kalender

## Zweck, Nutzen und Bedienung

SimpleOffice bewahrt eine importierte `.eml`-Originaldatei unverändert auf. Anhänge werden zunächst nur inventarisiert. Erst nach ausdrücklicher Auswahl und Bestätigung werden sie in einen nicht öffentlich erreichbaren Quarantäneordner geschrieben, mit ClamAV geprüft und bei einem sauberen Ergebnis als eigenständige Dokumente freigegeben. Diese Dokumente können anschließend über die Oberfläche oder den schreibenden WebDAV-Zugang in LibreOffice, Nautilus und anderen DAV-Clients bearbeitet werden.

1. Eine EML-Datei wie jedes andere Dokument importieren.
2. In der Detailansicht **Anhänge sicher prüfen** öffnen. Die Vorschau extrahiert noch nichts.
3. Anhänge markieren und die rote Bestätigungsschaltfläche verwenden.
4. Nur bei ClamAV-Ergebnis `clean` erscheinen neue Dokumente.
5. Unter `/documents/security` sehen Sicherheitsadministratoren Status und Prüfhistorie und können den Bestand prüfen oder Signaturen aktualisieren.

Freigegebene Dateien erhalten `attachment`, `source:eml`, `source-document:<ID>` und das Attribut `attachment_origin` mit Quell-ID, ursprünglichem Pfad, MIME-Teil, Inhaltstyp, SHA-256, Message-ID, Betreff und Absenderangabe. Die 30 Minuten gültige Vorschau ist an Benutzer und Quellhash gebunden.

## Installation und Konfiguration

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install clamav clamav-daemon
sudo freshclam
sudo systemctl enable --now clamav-daemon
```

Fedora/RHEL: `sudo dnf install clamav clamav-update clamd && sudo freshclam`. macOS/Homebrew: `brew install clamav && freshclam`.

- `SIMPLEOFFICE_SECURITY_ADMINS=alice,bob`: Benutzer für Serverprüfung und Signaturupdate; ohne Wert gibt es keinen Web-Administrator.
- `SIMPLEOFFICE_CLAMAV_SCANNER=/usr/bin/clamdscan`: optionaler absoluter Pfad; nur `clamdscan` oder `clamscan` werden akzeptiert.
- `SIMPLEOFFICE_CLAMAV_TIMEOUT=120`: Zeitlimit, intern auf 5 bis 900 Sekunden begrenzt.

`clamscan` funktioniert ohne Daemon, `clamdscan` ist bei vielen Dateien schneller. Vor `clamd` muss eine Signaturdatenbank vorhanden sein. Der ClamAV-TCP-Socket besitzt keine eigene Authentifizierung und darf nicht ins Internet veröffentlicht werden; ein lokaler Unix-Socket ist vorzuziehen. Programme laufen mit festen Argumentlisten ohne Shell. Der Dienstbenutzer braucht Leserechte auf Dokumente und Schreibrechte auf `.simpleoffice/quarantine`.

## Standards und Designentscheidungen

- [RFC 2045 §5](https://www.rfc-editor.org/rfc/rfc2045.html#section-5) definiert MIME-Inhaltstypen und [§6](https://www.rfc-editor.org/rfc/rfc2045.html#section-6) Transferkodierungen. Ein standardkonformer Parser dekodiert Base64/Quoted-Printable erst im bestätigten Vorgang.
- [RFC 2183 §2](https://www.rfc-editor.org/rfc/rfc2183.html#section-2) beschreibt `inline`, `attachment` und Dateinamen. Dateinamen sind unzuverlässige Metadaten: Pfadanteile und Steuerzeichen werden entfernt, Längen begrenzt und niemals als Befehl genutzt.
- [RFC 5545 §3.8.1.1](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.1.1) erlaubt Kalender-`ATTACH` als URI oder eingebettete Daten. Externe URIs werden aus Sicherheits- und Datenschutzgründen niemals automatisch abgerufen. Eingebettete Kalenderanhänge sollen denselben Workflow verwenden; diese Ausbaustufe stellt ihn zunächst für EML-Originale bereit. Kalenderverweise bleiben beim ICS-Roundtrip erhalten, werden aber noch nicht als Dateien freigegeben.
- Die Umsetzung folgt der offiziellen Anleitung zu [ClamAV-Scans](https://docs.clamav.net/manual/Usage/Scanning.html) und [freshclam](https://docs.clamav.net/manual/Usage/SignatureManagement.html). Exitcode `0` ist sauber, `1` ein Fund, andere Werte sind Fehler. `--remove` wird nie benutzt; Fehlalarme bleiben prüfbar.

## Sicherheit, Datenschutz und Rechte

- Vorschau und Bestätigung verlangen eine Anmeldung und sind benutzergebunden.
- Quarantänedateien werden zufällig benannt, exklusiv mit `0600` angelegt und nicht über Download, Freigaben oder WebDAV angeboten.
- Bei fehlendem Scanner, Timeout oder Fehler gilt **fail closed**: keine Freigabe.
- Die Serverprüfung umfasst nur reguläre Dateien im verwalteten Bestand; Symlinks und Systempfade werden übersprungen.
- Nur konfigurierte Sicherheitsadministratoren dürfen Update und Bestandsscan auslösen. Dokumentrechte werden nicht gelockert.
- Mail-Absender und Betreff sind unbestätigte Fremdangaben, kein Identitätsnachweis. Daten werden nicht extern übertragen.

## Fehler, Grenzen und Rückkehr

Bei verändertem Original, abgelaufener Vorschau, mehr als 100 Teilen, mehr als 50 MiB je Anhang oder 200 MiB gesamt wird abgebrochen. Infizierte Dateien bleiben als `.infected` isoliert; Scannerfehler als `.error`. Kennwortgeschützte Archive können nicht vollständig geprüft werden. Ein sauberes Ergebnis schließt neue oder unbekannte Schadsoftware nicht aus. Eine manuelle Fehlalarm-Freigabe und die Materialisierung von Kalender-`ATTACH` sind noch nicht implementiert.

Es gibt keine Migration. Neue Daten liegen unter `.simpleoffice/attachment-manifests`, `.simpleoffice/quarantine` und `.simpleoffice/malware-scan.json`. Ein Code-Rollback verändert keine Originaldatei. Quarantänedaten nur nach Prüfung und gemäß Aufbewahrungsregeln löschen.

## Tests

Geprüft werden Vorschau ohne Extraktion, unveränderte Originalbytes, MIME/Base64, sichere Namen, Herkunftstags und -attribute, Benutzer-/Hashbindung, saubere Freigabe, Fund-Quarantäne, Scannerfehler, feste ClamAV-Aufrufe, Adminrechte und WebDAV-Kompatibilität.
