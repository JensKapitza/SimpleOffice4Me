# PrinterShare und Federation-Druck

SimpleOffice4Me kann lokal eingerichtete Drucker als PrinterShare verwenden. Linux/macOS nutzt vorhandene CUPS-Drucker (`lpstat`, `lp`/`lpr`), Windows die installierten Windows-Drucker. Normale Drucker und Labeldrucker werden unterschieden; fuer ZPL/EPL bzw. explizit als `raw` konfigurierte Drucker werden RAW-Druckdaten direkt an den Spooler uebergeben.

## Aufbewahrungsmodi

Der Administrator konfiguriert einen Standardmodus. Ein Benutzer bzw. Federation-Sender darf fuer einen einzelnen Auftrag immer eine strengere Obergrenze verlangen. Die Empfaengerseite darf diese Obergrenze niemals lockern.

- `no_store`: SimpleOffice legt keine dauerhafte Kopie des Druckinhalts an. Nach der Uebergabe an den System-Spooler bleiben nur Auftragsmetadaten wie Hash, Groesse, Drucker und Status zurueck.
- `ttl`: Die Druckdatei wird verschluesselt im PrinterShare-Backlog gehalten und nach Ablauf der konfigurierten Frist geloescht. Sie kann bis dahin erneut gedruckt werden.
- `permanent`: Die Druckdatei wird verschluesselt aufbewahrt, bis sie administrativ geloescht wird.

Wichtig: `no_store` ist eine Zusage ueber die SimpleOffice-Anwendungsspeicherung. Betriebssystem, Druckertreiber, CUPS/Windows-Spooler, Reverse-Proxy oder der Drucker selbst koennen fuer die technische Durchfuehrung temporaer puffern. SimpleOffice behauptet deshalb bewusst keine physikalische Null-Speicherung ausserhalb der Anwendung.

## Peer-Policy: Default-Deny

Federation-Druck ist opt-in. Der sendende Server muss fuer den Ziel-Peer `printing.send=true` gesetzt haben. Der empfangende Server muss den Quell-Peer kennen, aktiviert haben und fuer ihn `printing.receive=true` gesetzt haben.

Beispiel auf dem Sender fuer den Ziel-Peer:

```json
{
  "printing": {
    "send": true,
    "receive": false,
    "retention_ceiling": "no_store"
  }
}
```

`printing.retention_ceiling` ist die **administrative Obergrenze fuer die Speicherung auf diesem Ziel-Peer**. `no_store` bedeutet: Druckauftraege werden nur gesendet, wenn die Gegenstelle den No-Store-Vertrag anbietet und anschliessend No-Store authentisiert bestaetigt. `ttl` erlaubt hoechstens einen zeitlichen Backlog; `permanent` erlaubt auch dauerhafte Speicherung. Fehlt das Feld oder ist sein Wert ungueltig, gilt fail-safe `no_store`.

Die Auswahl eines Anwenders fuer einen einzelnen Druckauftrag kann diese Peer-Policy nur strenger machen, niemals lockern. Beispiel: Peer-Policy `no_store` + Anwender waehlt `permanent` ergibt weiterhin `no_store`. Peer-Policy `permanent` + Anwender waehlt `no_store` ergibt ebenfalls `no_store`.

Die Druckerfreigabe selbst erfolgt zusaetzlich pro lokalem Drucker. Ein eingerichteter Systemdrucker wird nicht automatisch in der Federation angeboten.

## Stabile Peer-Identitaet und Credentials

Jede Instanz sollte eine stabile ID ueber `SIMPLEOFFICE_FEDERATION_PEER_ID` erhalten. Ohne diese Variable wird der Hostname verwendet. Die ID muss auf der Gegenstelle als `peer_id` konfiguriert sein.

Der normale Bearer-Token authentisiert weiterhin den **Zielserver**. Fuer Druckauftraege reicht das nicht, weil die Empfangsberechtigung pro Quell-Peer gilt. Deshalb signiert der Sender jeden Druckauftrag zusaetzlich mit seinem eigenen `SIMPLEOFFICE_FEDERATION_TOKEN`. Die Gegenstelle besitzt diesen Token bereits verschluesselt im Peer-Datensatz fuer Rueckverbindungen und prueft die Signatur gegen alle fuer `printing.receive=true` freigegebenen Peers.

Die Quellidentitaet wird dadurch aus dem passenden Peer-Credential abgeleitet. `X-SimpleOffice-Peer-ID` ist nur eine zusaetzliche Konsistenzangabe und darf niemals allein Berechtigungen auswaehlen. Werden identische Tokens fuer mehrere druckberechtigte Peers wiederverwendet, wird der Auftrag fail-safe abgelehnt. Fuer jeden Peer ist daher ein eigener Token erforderlich.

Die Peer-Signatur bindet mindestens folgende Werte:

- Peer-ID,
- Zeitstempel und zufaelligen Nonce,
- Drucker-ID,
- Policy-Revision,
- Retention- und TTL-Obergrenze,
- Content-Type und Dateiname,
- SHA-256 und Groesse des Druckinhalts.

Der Zeitstempel darf nur wenige Minuten abweichen. Verwendete Nonces werden kurzzeitig serverseitig registriert; ein Replay desselben signierten Druckauftrags wird mit HTTP `409` abgelehnt.

## Retention-Handshake

Vor der Dateiuebertragung fragt der Sender ab:

```text
GET /federation/v1/print/capabilities
Authorization: Bearer <token-des-zielservers>
```

Die Antwort enthaelt unter anderem:

```json
{
  "schema": 1,
  "enabled": true,
  "policy_revision": "<sha256>",
  "retention_contract": {
    "ceiling_enforced": true,
    "no_store_means_no_application_archive": true,
    "payload_metadata_only_after_spool": true,
    "os_spooler_may_cache": true
  },
  "configured_retention": "no_store",
  "ttl_seconds": 86400,
  "max_job_bytes": 67108864,
  "printers": [
    {"printer_id": "...", "label": "Etiketten", "kind": "label"}
  ]
}
```

Der Sender uebertraegt nur, wenn die verlangte Zusicherung in den Capabilities vorhanden ist. Fuer einen No-Store-Auftrag muss insbesondere `ceiling_enforced=true` und `no_store_means_no_application_archive=true` gelten.

Der Auftrag wird als Rohdatenkoerper gesendet. Die wichtigsten Header sind:

```text
POST /federation/v1/print/jobs/<printer_id>
Authorization: Bearer <token-des-zielservers>
X-SimpleOffice-Peer-ID: <source-peer-id>
X-SimpleOffice-Policy-Revision: <zuvor-gelesene-policy-revision>
X-SimpleOffice-Retention-Ceiling: no_store | ttl | permanent
X-SimpleOffice-TTL-Ceiling: <sekunden>
X-SimpleOffice-Content-Type: application/pdf
X-SimpleOffice-Filename: label.pdf
X-SimpleOffice-Print-Timestamp: <unix-sekunden>
X-SimpleOffice-Print-Nonce: <zufallswert>
X-SimpleOffice-Payload-SHA256: <sha256>
X-SimpleOffice-Payload-Size: <bytes>
X-SimpleOffice-Print-Signature: <hmac-sha256-mit-source-token>
Content-Type: application/octet-stream
```

Die Empfaengerseite prueft die `policy_revision` vor dem Upload und **noch einmal nach vollstaendigem Einlesen des Bodys unmittelbar vor dem Spoolen**. Hat sich die Policy geaendert, wird mit HTTP `409 policy_changed` abgebrochen. Fehlt die Revision, folgt HTTP `428 policy_revision_required`.

Auch Hash und Groesse des tatsaechlich empfangenen Bodys muessen den signierten Werten entsprechen. Erst danach wird der Nonce atomar beansprucht und der Auftrag an PrinterShare uebergeben.

TTL-Obergrenzen unter 60 Sekunden werden am Federation-Endpunkt explizit abgelehnt, statt intern auf 60 Sekunden hochgerundet zu werden. Damit kann die zugesicherte Obergrenze nicht unbemerkt ueberschritten werden.

Die effektive Aufbewahrung ist immer die strengste Variante aus lokaler Peer-Policy des Senders, Auswahl des Anwenders und Empfaenger-Standard. Beispiel: Empfaenger steht auf `permanent`, Sender fordert aufgrund seiner Peer-Policy `no_store` -> effektiver Auftrag ist `no_store`.

## Authentisierte und auftragsgebundene Druckbestaetigung

Nach erfolgreicher Uebergabe an den System-Spooler liefert die Gegenstelle einen Receipt mit der **tatsaechlich angewandten** Aufbewahrung:

```json
{
  "receipt": {
    "schema": 1,
    "job_id": "...",
    "request_nonce": "...",
    "printer_id": "...",
    "status": "spooled",
    "retention": "no_store",
    "expires_at": 0,
    "payload_sha256": "...",
    "payload_size": 12345,
    "policy_revision": "...",
    "completed_at": 1780000000,
    "application_archive": false,
    "os_spooler_may_cache": true
  },
  "receipt_hmac_sha256": "..."
}
```

`receipt_hmac_sha256` ist HMAC-SHA256 ueber das kanonische JSON des Receipts mit dem Token des Zielservers. Der Sender akzeptiert die Bestaetigung nur, wenn:

1. die HMAC-Pruefung erfolgreich ist,
2. `request_nonce` exakt zum aktuellen Auftrag passt,
3. Drucker-ID, Payload-SHA256 und Payload-Groesse exakt zum gesendeten Auftrag passen,
4. die `policy_revision` exakt zur vorher abgefragten Policy passt,
5. die bestaetigte Aufbewahrung die geforderte Obergrenze nicht ueberschreitet,
6. bei `no_store` `application_archive=false` und `expires_at=0` bestaetigt wird,
7. bei `ttl` die bestaetigte Ablaufzeit nicht ueber der vom Sender uebertragenen TTL-Obergrenze liegt.

Damit reicht ein alter, aber gueltig signierter Receipt nicht als Bestaetigung fuer einen neuen Druckauftrag.

Diese Bestaetigung ist eine technische, authentisierte Zusage innerhalb der vertrauenswuerdigen Federation. Sie ist keine kryptographische Moeglichkeit, einen absichtlich manipulierten Remote-Server daran zu hindern, ausserhalb des Protokolls dennoch Daten mitzuschneiden. Deshalb bleibt die Federation auf bekannte, administrativ gekoppelte Instanzen beschraenkt.

Die Bearer-/HMAC-Secrets schuetzen die Zusage nur, solange sie nicht auf dem Transportweg offengelegt werden. Ueber nicht vertrauenswuerrdige Netze ist deshalb HTTPS oder ein geschuetzter VPN-Tunnel erforderlich. Unverschluesseltes HTTP sollte nur in einem entsprechend geschuetzten lokalen/VPN-Netz verwendet werden.

## Backlog-Schutz

Zeitlich oder dauerhaft gespeicherte Druckinhalte werden nicht im normalen Dokumentarchiv abgelegt. PrinterShare speichert sie separat unter `.simpleoffice-meta/printershare-retained/` mit AES-GCM. Die Auftragsdatenbank enthaelt nur Metadaten und den Pfad auf eine ggf. vorhandene verschluesselte Kopie. Bei Ablauf eines TTL-Auftrags wird die verschluesselte Datei entfernt; die Audit-Metadaten duerfen bestehen bleiben.

## Statusbegriff

`spooled` bedeutet: SimpleOffice hat den Auftrag erfolgreich an den lokalen Betriebssystem-Spooler uebergeben. Es bedeutet nicht, dass Papier bereits physisch ausgegeben wurde. Ein spaeterer Papierstau, Offline-Drucker oder Druckerfehler liegt ausserhalb dieser ersten PrinterShare-Version.
