# S3-Overlay

Das S3-Overlay stellt einen signierten, schreibgeschützten S3-Zugriff auf
Dokumente bereit. Die einzige unterstützte fachliche Schreiboperation ist ein
neuer Dateiimport in die Inbox. Dokumentdateien bleiben im vorhandenen
Dokumentenspeicher; der S3-Zugang ist keine zweite Datenbank und kein Backup.

## Status und Grenzen

Diese erste Integrationsstufe projiziert ausschließlich:

- `_meta/overlay.json`
- `documents/<document-id>/metadata.json`
- `documents/<document-id>/original/<filename>`
- `inbox/<username>/<client-key>` für eigene S3-Inbox-Uploads

Dokumentordnerrechte werden über das vorhandene virtuelle Dateisystem geprüft.
Die Datei selbst wird über `StoragePort` gelesen und vor der Ausgabe vollständig
integritätsgeprüft. Range-Reads puffern nur den angeforderten Ausschnitt in
einem begrenzten Spool und verifizieren trotzdem den vollständigen Blob.

Kontakte, Kalender, Aufgaben, Projekte, Personal, Mail, Geschäftsdaten, Audit,
Recovery und Federation sind noch keine S3-Provider. Multipart-Upload ist noch
nicht implementiert. Das Overlay darf deshalb noch
nicht als vollständige Sicht auf alle Anwendungsdaten oder als kompatibel mit
allen S3-Clients beworben werden. Issue #482 bleibt für diese Ausbau- und
Gesamtabnahme offen.

## Aktivierung

Das Overlay ist standardmäßig deaktiviert. In der Serverumgebung setzen:

```text
SIMPLEOFFICE_S3_OVERLAY_ENABLED=true
SIMPLEOFFICE_S3_OVERLAY_REGION=us-east-1
SIMPLEOFFICE_S3_MAX_UPLOAD_MIB=512
```

Nach einem Neustart lautet die Endpoint-URL `https://<server>/s3`. Path-Style
ist erforderlich; der virtuelle Bucket heißt `simpleoffice`. Außerhalb von
localhost weist das Overlay unverschlüsselte HTTP-Anfragen ab. Bei einem
Reverse Proxy muss HTTPS korrekt an Flask weitergegeben werden.

Die S3-Grenze kann mit `SIMPLEOFFICE_S3_MAX_UPLOAD_MIB` verkleinert werden. Sie
kann das globale Upload-Limit der Anwendung nicht überschreiten. Ungültige
Werte fallen auf das globale Limit zurück.

## S3-Zugänge verwalten

Administratoren finden die Verwaltung unter **Administration → S3-Overlay**
oder `/admin/s3-overlay`. Zugänge sind auf 1 bis 365 Tage begrenzt; höchstens
zehn aktive Zugänge pro Benutzer sind zulässig. Berechtigungen:

- `read`: Dokumente, die der Benutzer auch im virtuellen Dateisystem lesen darf.
- `inbox:put`: neue Objekte ausschließlich unter `inbox/` importieren.
- optionaler Key-Prefix: zusätzliche Beschränkung des S3-Namespace.

Access Key und Secret werden bei Erstellung angezeigt. Das Secret wird
verschlüsselt in `.simpleoffice-meta/s3-overlay.sqlite3` abgelegt und ist durch
den vorhandenen Anwendungs-Session-Schlüssel geschützt. Dieser Schlüssel muss
bei Backups erhalten bleiben, sonst können gespeicherte S3-Secrets nicht mehr
entschlüsselt werden. Widerruf wirkt unmittelbar. Ein Secret-Wechsel erfolgt,
indem ein neuer Zugang erzeugt und der alte widerrufen wird.

## Unterstützte Protokolloperationen

Aktuell unterstützt:

- AWS Signature Version 4 im Authorization-Header
- presigned GET/HEAD-Requests (maximal sieben Tage)
- `ListBuckets`, `HeadBucket`, `GetBucketLocation`, `GetBucketVersioning`
- `ListObjectsV2` mit Prefix, Delimiter, MaxKeys, StartAfter,
  ContinuationToken und `encoding-type=url`
- `ListObjects` V1 mit Prefix, Delimiter, MaxKeys und Marker
- `HeadObject`, `GetObject`, einzelne Byte-Range-Requests sowie If-Match,
  If-None-Match, If-Modified-Since und If-Unmodified-Since
- `PutObject` ausschließlich für neue Inbox-Inhalte

Payloads bei PUT müssen mit SHA-256 signiert sein. Der Upload wird in einem
begrenzten Spool verarbeitet, nach erfolgreicher Prüfsummenvalidierung über
`StoragePort.import_stream_at` direkt im persönlichen Inbox-Unterordner
abgelegt. Wiederholtes PUT desselben Schlüssels mit identischem Inhalt ist
idempotent; anderer Inhalt für denselben Schlüssel wird mit 412 abgelehnt.

Delete, Copy, ACL-/Tag-/Policy-Mutationen und PUT außerhalb der Inbox werden
verweigert. Multipart-Upload ist derzeit nicht verfügbar. Große Clients, die
automatisch auf Multipart wechseln, können daher noch nicht verwendet werden.

## AWS CLI

Die CLI muss eine kompatible SigV4-Konfiguration haben. Credentials werden
interaktiv unter **S3-Overlay** erzeugt und dürfen nicht in Quellcode oder Logs
abgelegt werden.

```bash
aws configure set default.s3.addressing_style path
aws configure set default.region us-east-1
aws --endpoint-url https://<server>/s3 s3 ls s3://simpleoffice/
aws --endpoint-url https://<server>/s3 s3 cp s3://simpleoffice/documents/<id>/original/datei.pdf ./datei.pdf
aws --endpoint-url https://<server>/s3 s3 cp ./datei.pdf s3://simpleoffice/inbox/datei.pdf
```

Die Befehle sind Beispiele für die unterstützten Operationen, keine Zusage,
dass jeder AWS-CLI-Unterbefehl oder jedes automatische Multipart-Verhalten
funktioniert.

## Sicherheits- und Betriebsgrenzen

- Kein anonymes S3.
- Eine S3-Credential ersetzt keine fachliche Benutzerberechtigung.
- Listings und Objektzugriffe verwenden die vorhandenen Dokumentordnerrechte.
- Interne Steuerdateien, Geheimnisse und physische Blob-Pfade werden nicht
  projiziert.
- Dokumente werden vor GET/Range vollständig integritätsgeprüft.
- Nicht erfüllbare Byte-Ranges liefern `416` mit `Content-Range: bytes */<size>`.
- Upload-Schlüssel werden validiert; gleiche Zielschlüssel überschreiben keine
  Inbox-Dateien.
- S3-Zugangsdaten und Secrets werden nicht in Auditdetails oder Logs abgelegt.
- Admin-Verwaltung ist über die bestehende Session und CSRF-Prüfung geschützt;
  S3-Protokollzugriffe benötigen SigV4.
- Die Endpoint-URL sollte nur über TLS und einen korrekt konfigurierten
  Reverse Proxy veröffentlicht werden.

## Ausstehende Teile aus Issue #482

Für die vollständige Abnahme fehlen mindestens Provider für die übrigen
autoritativen Datenbereiche, eine sichere Provider-Coverage-Prüfung,
ListObjects-V1-Kompatibilität (falls nötig), Multipart-Inbox-Uploads,
komplette Conditional-Request-Semantik, praktische Tests mit AWS CLI/boto3/
rclone/MinIO mc sowie die erweiterte Betriebs- und Security-Abnahme.
