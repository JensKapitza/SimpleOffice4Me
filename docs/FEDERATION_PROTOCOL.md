# SimpleOffice4Me Federation Protocol (SOFP) v1

## Ziel

SOFP verbindet bekannte, administrativ gekoppelte SimpleOffice4Me-Instanzen. Es kombiniert Objekt-Synchronisation mit einem content-addressed, BitTorrent-aehnlichen Chunk-Transport. Dokumente, Kontakte, Kalender und Aufgaben koennen pro Peer und Richtung getrennt freigegeben werden.

Das Protokoll muss auch asymmetrische Topologien beherrschen: Ein Steuerknoten A darf einen direkten Server-zu-Server-Transfer B -> C veranlassen, obwohl B nur A kennt. C muss A vertrauen und den Transfer lokal erlauben; B erhaelt fuer C nur eine kurzlebige, auf genau diesen Transfer begrenzte Berechtigung.

## Grundprinzipien

1. Bekannte Peers und Default-Deny.
2. Send, Receive, Relay und Third-Party-Transfer sind getrennte Rechte.
3. Lokale Empfangspolicy hat immer Vorrang.
4. Manifest vor Payload; bekannte Inhalte werden nicht erneut uebertragen.
5. Content-addressed Daten mit SHA-256 und Merkle-Verifikation.
6. Dateien werden in unabhaengige Chunks zerlegt und koennen parallel von mehreren Quellen kommen.
7. Delta-Sync ueber monotone Cursor.
8. Idempotente Operationen und persistente Transfer-Sessions.
9. Keine implizite Loeschung; Tombstones werden lokal bewertet.
10. Jeder Transfer und jede Delegation ist auditierbar.

## Peer- und Rollenmodell

Ein Peer kann getrennt folgende Faehigkeiten erhalten:

- `send`: eigene freigegebene Objekte anbieten.
- `receive`: angebotene Objekte annehmen.
- `seed`: bereits verifizierte Chunks fuer andere bereitstellen.
- `relay`: Datenstrom weiterleiten, ohne ihn dauerhaft zu speichern.
- `orchestrate`: Transfers zwischen anderen Instanzen initiieren.
- `third_party_source`: auf delegierte Anweisung an einen fremden Zielknoten senden.
- `third_party_sink`: von einem durch einen vertrauten Orchestrator delegierten Quellknoten empfangen.

Alle Rechte gelten zusaetzlich pro Ressourcentyp und optional Sammlung/Projekt/Kunde.

```json
{
  "peer_id": "backup-01",
  "resources": {
    "documents": {"send": false, "receive": true, "seed": false},
    "contacts": {"send": false, "receive": true},
    "calendars": {"send": false, "receive": true},
    "tasks": {"send": false, "receive": true}
  },
  "third_party_sink": true
}
```

## Capability Negotiation

`GET /federation/v1/capabilities`

```json
{
  "protocol": "sofp",
  "versions": [1],
  "instance_id": "uuid",
  "resources": ["documents", "contacts", "calendars", "tasks"],
  "hashes": ["sha256"],
  "chunk_hashes": ["sha256"],
  "merkle": true,
  "range": true,
  "multi_source": true,
  "third_party_transfer": true,
  "relay": true,
  "max_parallel_chunks": 16,
  "preferred_chunk_bytes": 4194304,
  "max_payload_bytes": 52428800
}
```

## Objekt- und Blobidentitaet

Logische Objekte werden durch `(origin_instance_id, object_id)` identifiziert. Dokumentinhalt wird separat durch `blob_hash` identifiziert. Dadurch kann derselbe Blob beliebig viele Dokumentobjekte referenzieren, ohne erneut gespeichert oder uebertragen zu werden.

Jedes Objekt besitzt mindestens `object_id`, `origin_instance_id`, `resource_type`, `revision`, `modified_at`, `deleted` und `content_hash`.

## Swarm-Manifest und Chunking

Jeder Blob besitzt ein unveraenderliches Transfermanifest:

```json
{
  "blob_hash": "sha256:FILE_HASH",
  "size": 987654321,
  "chunk_size": 4194304,
  "chunk_count": 236,
  "merkle_root": "sha256:ROOT",
  "chunks": [
    {"index": 0, "offset": 0, "length": 4194304, "hash": "sha256:..."},
    {"index": 1, "offset": 4194304, "length": 4194304, "hash": "sha256:..."}
  ]
}
```

Chunks sind einzeln adressierbar und verifizierbar. Die Standardgroesse ist verhandelbar. Sehr kleine Dateien duerfen einen einzelnen Chunk verwenden. Fuer sehr grosse Dateien kann die Chunk-Liste paginiert oder als Merkle-Unterbaum geliefert werden.

## BitTorrent-aehnlicher Multi-Source-Transfer

Ein Empfaenger darf fuer denselben `blob_hash` mehrere autorisierte Quellen benutzen. Er verwaltet eine Piece-/Chunk-Bitmap und fordert fehlende Chunks parallel an.

`GET /federation/v1/blobs/{hash}/availability`

liefert eine kompakte Bitmap oder Range-Liste verfuegbarer Chunks. Der Scheduler soll mindestens unterstuetzen:

- parallele Quellen,
- rarest-first, wenn mehrere Quellen unterschiedliche Chunks besitzen,
- endgame mode fuer die letzten fehlenden Chunks,
- Abbruch doppelter Requests nach erstem gueltigen Ergebnis,
- dynamische Bewertung nach Durchsatz, RTT und Fehlerquote,
- Per-Peer-Bandbreitenlimit,
- globale Upload-/Downloadlimits,
- Fairness zwischen Transfers,
- Pause/Resume nach Neustart,
- Quellwechsel ohne Verlust bereits verifizierter Chunks.

`GET /federation/v1/blobs/{hash}/chunks/{index}` liefert genau einen Chunk. HTTP Range bleibt als Fallback und fuer Streaming-Clients erhalten.

Ein Chunk wird erst nach Hash-Pruefung in den lokalen Chunk-Store uebernommen. Nach Vollstaendigkeit wird der gesamte `blob_hash` bzw. Merkle-Root erneut validiert.

## Partielle Datenstroeme

SOFP muss Dateien nicht vollstaendig lokal zusammensetzen, bevor ein Verbraucher Daten lesen kann. Ein Stream kann aus lokal vorhandenen und parallel von mehreren Servern geladenen Bereichen bestehen.

`POST /federation/v1/streams`

kann einen Stream fuer `blob_hash`, Byte-Range und Prioritaet erzeugen. Der Scheduler priorisiert Chunks, die fuer den aktuellen Lesepunkt benoetigt werden, und laedt optional ein konfigurierbares Read-Ahead-Fenster.

Damit sind unter anderem moeglich:

- Video/PDF lesen, waehrend der Rest noch geladen wird,
- nur einen Byte-Bereich einer grossen Datei abrufen,
- fehlende Bereiche von B und C parallel beziehen,
- einen Datenstrom direkt an einen weiteren Server weiterreichen,
- abgebrochene Transfers exakt ab dem letzten verifizierten Chunk fortsetzen.

## Source Discovery innerhalb des Vertrauensnetzes

Es gibt keine offene Internet-DHT. Stattdessen koennen bekannte Peers fuer einen konkreten Hash autorisierte Quellen nennen:

`POST /federation/v1/sources/query`

Der Aufrufer sendet Hash und Transferkontext. Die Antwort darf nur Peers enthalten, deren Policy eine Bekanntgabe erlaubt. Ein Knoten darf ausserdem Quellen ueber den Orchestrator nutzen, ohne diese dauerhaft als eigenen Peer zu speichern.

Optional koennen Peers kompakte Bloom-/Cuckoo-Filter ihres Blob-Bestands austauschen. Ein positiver Filtertreffer muss vor Nutzung exakt bestaetigt werden.

## Delegierter Server-zu-Server-Transfer (FXP-aehnlich)

### Beispiel A kennt B und C; B kennt nur A

A moechte einen Blob von B nach C replizieren. A soll die Datei nicht selbst transportieren muessen.

1. A fragt C, ob C den Blob und einen delegierten Transfer von B akzeptiert.
2. C prueft lokale `receive`- und `third_party_sink`-Policy.
3. C erzeugt eine kurzlebige Transfer-Session und ein eingeschraenktes Capability-Token.
4. A sendet B einen signierten Transferauftrag mit Ziel C, Blob-Hash, erlaubten Chunks, Ablaufzeit und C-Token.
5. B prueft, ob A `orchestrate` darf und ob B fuer diesen Datentyp `third_party_source` erlaubt.
6. B verbindet sich direkt per HTTPS mit C und uebertraegt die angeforderten Chunks.
7. C verifiziert jeden Chunk und den Gesamtblob und meldet den Abschluss signiert an A.

B muss C dabei nicht dauerhaft kennen oder vertrauen. Das von C ausgestellte Token erlaubt ausschliesslich den bezeichneten Transfer.

`POST /federation/v1/transfers/prepare` auf C erzeugt die Sink-Session.

`POST /federation/v1/transfers/delegate` auf B uebergibt den Auftrag.

`PUT /federation/v1/transfers/{transfer_id}/chunks/{index}` auf C nimmt Daten direkt von B an.

`GET /federation/v1/transfers/{transfer_id}/status` erlaubt A, den Transfer zu ueberwachen.

## Delegationstoken

Delegation darf niemals normale Peer-Credentials weiterreichen. Ein Capability-Token ist signiert und bindet mindestens:

- `transfer_id`,
- Orchestrator A,
- Quelle B,
- Ziel C,
- `blob_hash`,
- erlaubte Chunk-/Byte-Bereiche,
- erlaubte Methode/Richtung,
- maximale Bytes,
- `issued_at` und kurze `expires_at`,
- Nonce,
- optional Ziel-IP/Host-Key/TLS-Fingerprint.

Das Token ist nicht auf andere Dateien oder Ziele uebertragbar. C darf es nach Abschluss oder Abbruch sofort widerrufen.

## Third-Party Multi-Source

A kann C gleichzeitig mehrere Quellen B, D und E vermitteln. C verteilt die fehlenden Chunks selbst oder A kann einen Transferplan vorschlagen. Die lokale Policy von C entscheidet immer, welche Quellen akzeptiert werden.

Beispiel:

```json
{
  "blob_hash": "sha256:...",
  "sources": ["B", "D", "E"],
  "strategy": "rarest_first",
  "parallelism": 12
}
```

Wenn C nur A kennt, koennen alle Quellen mit separaten, von C ueber A vermittelten One-Time-Capabilities arbeiten. Eine dauerhafte Peer-Beziehung ist nicht erforderlich.

## Relay-Modus

Falls B C wegen NAT, Firewall oder Routing nicht direkt erreicht, darf A optional als Relay dienen. Dabei bleibt der Transfer logisch B -> C; A leitet Chunks nur weiter. Relay ist ein separates Recht und standardmaessig deaktiviert.

Relay-Modi:

- `stream`: ohne dauerhafte Speicherung,
- `cache`: verifizierte Chunks duerfen temporaer gecacht und fuer denselben autorisierten Transfer wiederverwendet werden,
- `store-and-forward`: fuer zeitversetzt erreichbare Knoten, nur bei expliziter Policy.

## FTP-aehnliche Transferoperationen

Neben automatischem Sync gibt es explizite Transfer-Jobs. Sie erlauben Copy/Replicate/Move-artige Operationen zwischen Servern, ohne lokale Dateipfade des Remote-Systems offenzulegen.

- `COPY`: Ziel erhaelt eine weitere Referenz/Kopie.
- `REPLICATE`: Blob wird auf mindestens N gewuenschte Knoten verteilt.
- `MOVE`: COPY plus separat bestaetigte Quell-Loeschanfrage; niemals atomar behaupten, solange die Quellloeschung nicht bestaetigt wurde.
- `VERIFY`: Ziel prueft vorhandene Chunks/Blob.
- `REPAIR`: fehlende oder korrupte Chunks werden aus anderen Quellen rekonstruiert.

Jeder Job besitzt Status, Fortschritt, Bytezaehler, Chunk-Bitmap, Fehlerliste und Audit-Trail.

## Metadaten-Synchronisation

Kontakte, Kalender und Aufgaben verwenden weiterhin Manifest-/Cursor-Sync. Dokument-Metadaten und Blob-Transfer sind getrennt. Ein Ziel kann Metadaten annehmen, den Blob ablehnen oder nur bei Zugriff on-demand laden.

`GET /federation/v1/changes/{resource}?after=<cursor>&limit=<n>` liefert Delta-Manifeste. `POST /federation/v1/need` beschreibt fehlende Metadaten/Blobs. `POST /federation/v1/ack` bestaetigt verarbeitete Sequenzen.

## Deduplizierung

Vor jeder Uebertragung wird geprueft:

1. logische Objekt-Revision bereits vorhanden,
2. `content_hash` vorhanden,
3. `blob_hash` vorhanden,
4. einzelne Chunk-Hashes vorhanden.

Ein vorhandener Gesamtblob wird nie erneut uebertragen. Bei einem teilweise vorhandenen Blob werden nur fehlende oder korrupte Chunks angefordert. Chunks koennen optional blobuebergreifend dedupliziert werden, wenn ihre Hashes identisch sind.

## Konflikte und Loeschungen

Es gibt kein pauschales Last-Write-Wins. Neue Revisionen desselben Ursprungsobjekts ersetzen alte Ursprungsrevisionen. Lokale Aenderungen koennen einen Fork/Konflikt erzeugen. Tombstones unterstuetzen `ignore`, `archive` und `mirror`. Backup-Knoten sollten `archive` verwenden.

## Sicherheit

Produktiv erforderlich:

- HTTPS, bevorzugt mTLS zwischen dauerhaft gekoppelten Peers,
- pro Peer getrennte Credentials,
- signierte kurzlebige Capability-Tokens fuer Delegation,
- Replay-Schutz durch Nonce + Ablaufzeit,
- Token-Bindung an Quelle, Ziel, Hash, Richtung und Bytebereich,
- SSRF-Schutz: ein Transferauftrag darf keine beliebige URL enthalten; Ziele stammen aus validierter Session/Capability,
- Schutz gegen DNS-Rebinding und Redirects bei Third-Party-Transfers,
- Rate-, Verbindungs-, Byte- und Speicherlimits,
- SHA-256/Merkle-Verifikation vor Commit,
- Dateinamen und Metadaten als untrusted Input,
- keine Symlinks und keine Remote-Dateipfade,
- verschluesselte Speicherung von Secrets,
- vollstaendiges Auditlog fuer Pairing, Delegation, Transfer, Abbruch und Loeschung.

## Skalierung und Backpressure

- unabhaengige Worker pro Peer/Ressource/Transfer,
- persistente Transfer-Queue,
- Chunk-Pipelining und parallele Verbindungen,
- Empfaenger bestimmt Parallelitaet und Fenster,
- adaptive Chunk-Auswahl nach Durchsatz,
- Retry mit exponentiellem Backoff und Jitter,
- Hash-Inventare als Bloom/Cuckoo-Filter,
- Cursor statt Vollabgleich,
- Metadaten-Kompression,
- Quotas und Prioritaetsklassen,
- Garbage Collection nur fuer unreferenzierte Chunks nach Schutzfrist.

## Backup- und Replikationsmodus

Ein Backup-Knoten kann alle Ressourcen empfangen, nichts automatisch zuruecksenden und Tombstones archivieren. Fuer Dokumente kann zusaetzlich ein Replikationsfaktor definiert werden, z. B. `replicas=3`. Der Orchestrator sucht autorisierte Ziele und verteilt fehlende Chunks direkt zwischen den Servern.

## Minimaler Implementierungsplan

1. Peer-/Policy-Modell mit `send`, `receive`, `seed`, `relay`, `orchestrate`, `third_party_source`, `third_party_sink`.
2. Append-only Changelog und Cursor fuer Objekt-Sync.
3. Content-addressed Blob- und Chunk-Store mit SHA-256/Merkle-Manifest.
4. Chunk-, Availability-, Need- und Resume-Endpunkte.
5. Persistenter Multi-Source-Scheduler mit Bitmap, rarest-first und endgame mode.
6. Transfer-Sessions und signierte One-Time-Capability-Tokens.
7. Direkter B -> C Transfer, von A initiiert, inklusive Statuscallback.
8. Relay-/Store-and-forward-Fallback.
9. Stream-/Range-API mit Read-Ahead-Priorisierung.
10. Admin-UI fuer Peers, Rechte, Bandbreite, Replikation und Transferstatus.
11. Auditlog, Quarantaene, Konflikt- und Tombstone-Ansicht.
12. Integrationstests fuer Multi-Source, Resume, Source-Ausfall, korrupte Chunks, B->C Delegation, Relay und Backup-Restore.

## Abnahmekriterien

- Eine 10-GB-Datei kann nach Unterbrechung ohne erneute Uebertragung verifizierter Chunks fortgesetzt werden.
- Ein Blob kann gleichzeitig aus mindestens drei Quellen geladen werden.
- Unterschiedliche Quellen duerfen unterschiedliche Teilmengen desselben Blobs besitzen.
- A kann B anweisen, direkt an C zu senden, obwohl B C vorher nicht als Peer kannte.
- C kann den delegierten Transfer ablehnen, ohne seine Peer-Konfiguration zu veraendern.
- Bereits vorhandene Chunks werden nicht erneut uebertragen.
- Ausfall einer Quelle stoppt einen Multi-Source-Transfer nicht, solange andere Quellen die fehlenden Chunks besitzen.
- Streaming kann vor Abschluss des Gesamttransfers beginnen.
- Relay funktioniert als Fallback, ohne Third-Party-Rechte zu umgehen.
- Kein Delegationstoken kann fuer einen anderen Hash, ein anderes Ziel oder nach Ablauf wiederverwendet werden.
