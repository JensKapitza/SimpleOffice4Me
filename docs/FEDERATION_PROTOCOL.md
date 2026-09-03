# SimpleOffice4Me Federation Protocol (SOFP) v1

## Ziel

SOFP verbindet bekannte, explizit vertrauenswürdig konfigurierte SimpleOffice4Me-Instanzen. Die Richtung wird pro Datentyp getrennt geregelt. Eine Instanz darf Daten anbieten, ohne Daten anzunehmen; eine andere darf nur sammeln. Unterstützt werden zunächst `documents`, `contacts`, `calendars` und `tasks`.

Das Protokoll ist kein unkontrolliertes Multi-Master-Dateisystem. Jede Seite behält die Entscheidungshoheit darüber, welche Objekte sie anbietet und welche sie annimmt.

## Grundprinzipien

1. **Bekannte Peers:** keine öffentliche Peer-Suche. Instanzen werden administrativ gekoppelt.
2. **Send und Receive getrennt:** Berechtigungen gelten je Peer und Ressourcentyp unabhängig.
3. **Default deny:** neue Peers und neue Ressourcentypen sind zunächst gesperrt.
4. **Manifest vor Payload:** zunächst werden nur kleine Objektbeschreibungen ausgetauscht.
5. **Content-addressed:** Dokumente werden primär über SHA-256 identifiziert. Bekannter Inhalt wird nicht erneut übertragen.
6. **Delta-Sync:** nach dem Erstabgleich werden nur Änderungen seit einem Cursor übertragen.
7. **Idempotenz:** Wiederholung einer Anfrage darf keine doppelten Objekte erzeugen.
8. **Keine implizite Löschung:** entfernte Objekte werden als Tombstone angekündigt. Ob ein Peer lokal löscht, archiviert oder ignoriert, bestimmt dessen Policy.
9. **Auditierbar:** Annahme, Ablehnung, Konflikte und Transfers werden protokolliert.
10. **Pull-basierte Payloads:** ein Sender kündigt Änderungen an; der Empfänger entscheidet anschließend, welche Payload er tatsächlich abruft.

## Peer-Konfiguration

Beispiel:

```json
{
  "peer_id": "backup-01",
  "base_url": "https://backup.example/federation/v1",
  "enabled": true,
  "resources": {
    "documents": {"send": false, "receive": true},
    "contacts":  {"send": false, "receive": true},
    "calendars": {"send": false, "receive": true},
    "tasks":     {"send": false, "receive": true}
  }
}
```

Das beschreibt einen Backup-Knoten, der alles annehmen darf, aber nichts zurückliefert.

Ein Kontaktsammler könnte dagegen ausschließlich `contacts.receive=true` setzen. Eine Arbeitsinstanz kann für einen Peer beispielsweise Kalender senden, Kontakte empfangen und Dokumente vollständig sperren.

## Capability Negotiation

`GET /federation/v1/capabilities`

liefert mindestens:

```json
{
  "protocol": "sofp",
  "versions": [1],
  "instance_id": "uuid",
  "resources": ["documents", "contacts", "calendars", "tasks"],
  "hashes": ["sha256"],
  "compression": ["gzip"],
  "max_page_size": 1000,
  "max_payload_bytes": 52428800
}
```

Die lokale Policy hat immer Vorrang vor den angekündigten Fähigkeiten des Gegenübers.

## Objektidentität

Jedes föderierbare Objekt besitzt:

- `object_id`: stabile UUID der Ursprungsinstanz
- `origin_instance_id`
- `resource_type`
- `revision`: monoton steigende lokale Revision
- `modified_at`
- `deleted`: Tombstone-Flag
- `content_hash`: SHA-256 der kanonischen Nutzdaten, sofern sinnvoll

Für Dokumente gibt es zusätzlich `blob_hash` und `size`. `blob_hash` ist SHA-256 über die tatsächlichen Datei-Bytes.

Die Kombination `(origin_instance_id, object_id)` ist global die logische Identität. Der Hash dient der Inhalts-Deduplizierung und darf nicht allein die Objektidentität ersetzen.

## Änderungsjournal

Jede Instanz führt ein append-only Federation-Changelog. Jeder Eintrag erhält eine monoton steigende `sequence`.

```json
{
  "sequence": 91822,
  "resource_type": "documents",
  "object_id": "uuid",
  "revision": 7,
  "operation": "upsert",
  "content_hash": "sha256:...",
  "blob_hash": "sha256:...",
  "size": 184221
}
```

Peers speichern je Ressourcentyp den letzten bestätigten Cursor. Dadurch ist die Last proportional zur Anzahl der Änderungen und nicht zur Gesamtgröße des Bestands.

## Sync-Ablauf

### 1. Änderungen abfragen

`GET /federation/v1/changes/{resource}?after=<cursor>&limit=<n>`

Antwort enthält ausschließlich Manifestdaten und `next_cursor`.

### 2. Lokalen Bestand prüfen

Der Empfänger prüft in dieser Reihenfolge:

1. Ist `receive` für Peer und Ressourcentyp erlaubt?
2. Ist `(origin_instance_id, object_id, revision)` bereits bekannt?
3. Ist `content_hash` bereits vorhanden?
4. Bei Dokumenten: existiert `blob_hash` bereits im lokalen Blob-Store?
5. Verletzt das Objekt Filter, Größenlimit, Speicherlimit oder andere lokale Regeln?

### 3. Bedarf mitteilen

`POST /federation/v1/need`

```json
{
  "resource": "documents",
  "objects": [
    {"object_id": "...", "revision": 7, "need_metadata": true, "need_blob": false}
  ]
}
```

`need_blob=false` ist der zentrale Mechanismus gegen unnötige Dateiübertragung. Ist derselbe SHA-256-Blob bereits vorhanden, werden höchstens fehlende Metadaten übertragen und der bestehende Blob referenziert.

### 4. Payload abrufen

Metadaten:

`GET /federation/v1/objects/{resource}/{object_id}?revision=7`

Dateiblobs ausschließlich bei Bedarf:

`GET /federation/v1/blobs/{sha256}`

Große Dateien müssen Range Requests und Wiederaufnahme unterstützen. Vor dem Commit wird der empfangene SHA-256 erneut geprüft.

### 5. Bestätigen

`POST /federation/v1/ack`

Der Empfänger bestätigt bis zu welcher `sequence` er die Änderungen vollständig verarbeitet hat. Erst dann wird sein Cursor fortgeschrieben.

## Dokument-Deduplizierung

Vor jedem Dateiabruf wird der lokale Blob-Index nach SHA-256 geprüft.

- Hash vorhanden: keine Dateiübertragung; vorhandenen Blob referenzieren.
- Hash unbekannt: Blob abrufen, SHA-256 verifizieren, atomar einlagern.
- Gleicher logischer Datensatz und gleiche Revision: komplett überspringen.
- Gleicher logischer Datensatz, neuer Hash: neue Revision verarbeiten.

Optional kann beim initialen Pairing ein kompaktes Hash-Inventar/Bloom-Filter ausgetauscht werden. Es dient nur als Optimierung. Wegen möglicher False Positives muss vor dem endgültigen Überspringen ein exakter Hash-Lookup möglich bleiben.

## Kontakte, Kalender und Aufgaben

Diese Ressourcen verwenden dieselbe Manifest-/Cursor-Logik, aber keinen Blobtransfer.

Für kanonische Hashes werden volatile Felder wie lokale Datenbank-ID, Sync-Zeitpunkt und Peer-spezifische Metadaten ausgeschlossen. Dadurch können semantisch identische Datensätze erkannt werden.

Für Kontakte können zusätzliche Match-Indikatoren wie normalisierte E-Mail und Telefonnummer zur Duplikatwarnung dienen. Sie dürfen aber nicht automatisch zwei verschiedene globale Objekt-IDs zusammenführen.

Kalender und Aufgaben behalten UID/RECURRENCE-ID beziehungsweise VTODO-UID als fachliche Kennungen zusätzlich zur föderierten Objekt-ID.

## Konflikte

SOFP verwendet keine pauschale Last-Write-Wins-Regel.

- Eine neue Revision desselben Ursprungsobjekts ersetzt dessen ältere Revision.
- Lokale Bearbeitungen einer importierten Kopie erzeugen einen Konflikt oder einen lokalen Fork, sofern keine explizite Schreibberechtigung zum Ursprung besteht.
- Konflikte werden mit beiden Revisionen gespeichert und in der Oberfläche angezeigt.
- Automatisches Feld-Merging ist nur für explizit dafür freigegebene Ressourcentypen zulässig.

Damit kann ein Backup-Server exakt sammeln, ohne Änderungen zurückzuschreiben.

## Löschungen

Ein Tombstone enthält Objekt-ID, letzte Revision und Löschzeitpunkt. Empfangsregeln unterstützen mindestens:

- `ignore`: lokale Kopie behalten
- `archive`: lokal archivieren
- `mirror`: nach definierter Schutzfrist löschen

Für Backup-Instanzen sollte standardmäßig `archive` verwendet werden. Ein kompromittierter oder fehlerhafter Ursprung kann dadurch nicht sofort alle Sicherungskopien vernichten.

## Filter und Policies

Policies können mindestens nach Peer, Ressourcentyp, Richtung und optional Sammlung/Kalender/Adressbuch/Aufgabenliste gelten.

Sinnvolle zusätzliche Grenzen:

- maximale Dateigröße
- erlaubte MIME-Typen
- Tags/Projekte/Kunden
- Speicherquota
- Zeitfenster
- Metadaten-only für Dokumente
- automatische Annahme oder Quarantäne

Die Empfangspolicy wird immer lokal ausgewertet. Ein Remote-System kann sie nicht überschreiben.

## Sicherheit

Produktiv erforderlich:

- HTTPS
- zufällige, unveränderliche `instance_id`
- administratives Pairing
- pro Peer getrennte Credentials
- kurzlebige signierte Requests oder mTLS/OAuth2 Client Credentials
- Replay-Schutz über Timestamp + Nonce
- Rate Limits und Payload-Limits
- SHA-256-Verifikation für Blobs
- keine vom Peer gelieferten lokalen Dateipfade verwenden
- Dateinamen als untrusted Input behandeln
- keine Symlinks übernehmen
- Auditlog für Konfigurations- und Transferereignisse

Credentials und private Schlüssel dürfen nicht im Klartext in der Datenbank gespeichert werden.

## Skalierung

- Cursor statt vollständiger Bestandsabgleiche
- paginierte Manifeste
- Content-addressed Blob-Store
- Hash-Deduplizierung über alle Peers
- Batch-Need/ACK statt Request pro Objekt
- HTTP-Kompression für Metadaten
- Range Requests für große Dateien
- Backpressure: Empfänger bestimmt Batchgröße und Abrufgeschwindigkeit
- exponentielles Retry mit Jitter
- unabhängige Worker/Cursor pro Peer und Ressourcentyp

Dadurch kann ein langsamer Dokument-Peer die Kalender- oder Kontaktsynchronisation nicht blockieren.

## Backup-Modus

Ein Peer kann als `backup` markiert werden. Empfohlene Policy:

```json
{
  "mode": "backup",
  "resources": {
    "documents": {"send": false, "receive": true, "delete": "archive"},
    "contacts":  {"send": false, "receive": true, "delete": "archive"},
    "calendars": {"send": false, "receive": true, "delete": "archive"},
    "tasks":     {"send": false, "receive": true, "delete": "archive"}
  }
}
```

Der Backup-Knoten bestätigt empfangene Revisionen, sendet aber keine fachlichen Änderungen zurück. Restore ist eine separate, explizit gestartete Operation und kein automatischer Reverse-Sync.

## Minimaler Implementierungsplan

1. `FederationPeer` und pro Ressourcentyp `send/receive`-Policies.
2. Append-only Changelog mit `sequence` und Cursor je Peer/Ressource.
3. Capability-, Changes-, Need-, Object-, Blob- und ACK-Endpunkte.
4. SHA-256-basierter Blob-Lookup vor Dokumenttransfer.
5. Worker für inkrementellen Pull mit Retry/Backpressure.
6. Admin-Oberfläche für Peer-Pairing, Richtungen, Ressourcentypen und Status.
7. Auditlog, Quarantäne und Konfliktansicht.
8. Integrationstests mit Sender-only, Receiver-only, Backup und unterbrochenem Transfer.

## Nicht-Ziele von v1

- automatische Verbindung unbekannter Internet-Instanzen
- globaler Multi-Master-Dateisystem-Sync
- automatische Konfliktauflösung ohne fachliche Regeln
- Übertragung eines bekannten Dokumentblobs nur wegen eines anderen Dateinamens
- Remote-Löschung von Backup-Daten ohne lokale Schutzpolicy
