# Chat – Stufe 1

SimpleOffice4Me besitzt einen einfachen, bewusst erweiterbaren Chat für Text und Dateien. Die Nachrichten liegen lokal in `.simpleoffice-meta/chat.sqlite3`. Dateien sind keine zweite Schattenablage: Jeder Anhang wird als reguläres `DocumentStore`-Dokument gespeichert und im Chat nur über seine Dokument-ID referenziert.

## Berechtigungen

Ein Chat hat explizite lokale Teilnehmer und optional genau einen Federation-Peer mit dortigen Teilnehmern. Lokale Administratoren dürfen Chats und Chat-Anhänge zu Administrationszwecken sehen, sind dadurch aber nicht automatisch schreibende Teilnehmer.

Anhänge haben eine Sichtbarkeit:

- `chat`: nur lokale Chat-Teilnehmer und lokale Administratoren. Der Dokument-Metadatensatz enthält die erlaubten lokalen Teilnehmer. Zusätzlich erhält der physische Raumordner eine nicht vererbende VFS-ACL, damit WebDAV/SFTP dieselbe Grenze respektieren. Öffentliche Dokument-Freigabelinks sind für diese Anhänge gesperrt.
- `documents`: normaler Dokumentanhang ohne Chat-Sichtbarkeitssperre.

Der sichere Standard im Formular ist `chat`.

## Federation

Stufe 1 synchronisiert einen Chat zwischen zwei bekannten SimpleOffice-Instanzen. Beim Anlegen wird höchstens ein Peer gewählt. Text und jede Datei werden auf der empfangenden Instanz dauerhaft gespeichert; ein Dateianhang erhält dort eine eigene lokale Dokument-ID.

Chat-Federation ist pro Peer standardmäßig aus. Beide Richtungen müssen in der bestehenden Peer-Policy ausdrücklich freigegeben werden:

```json
{
  "chat": {
    "send": true,
    "receive": true
  }
}
```

Die Instanzen müssen eindeutige gemeinsame Peer-Tokens besitzen. Requests werden mit HMAC-SHA-256 signiert. Signiert werden Peer-ID, Zeitstempel, Nonce, Request-Typ, Ressourcen-ID, SHA-256 und Größe des Payloads. Der Empfänger prüft Zeitfenster, Replay-Nonce, Peer-Policy, eindeutiges Token sowie Inhalt und Größe.

Schlägt die Gegenstelle fehl, bleibt die Nachricht lokal gespeichert und erhält den Zustand `failed`. Im Chat kann die Zustellung erneut angestoßen werden. Bereits empfangene Events und Anhänge sind idempotent; dieselbe ID darf nicht mit anderem Inhalt wiederverwendet werden.

Für die lokale Instanz-ID wird `SIMPLEOFFICE_FEDERATION_PEER_ID` verwendet. Ohne Konfiguration dient der normalisierte Hostname als Fallback.

## Erweiterung ohne Schemawechsel

`chat_message` besitzt bereits `message_type` und ein versionierbares JSON-Payload. Stufe 1 erzeugt im UI nur `text`. Reservierte Typen sind bereits:

- `contact` – Kontakt bzw. Contact-ID teilen,
- `poll` – Umfrage mit Optionen und späteren Antworten,
- `request` – strukturierte Anfrage, z. B. „Kontakt vervollständigen“ oder „fehlende Datei hochladen“.

Damit können spätere Stufen Formulare und Aktionen ergänzen, ohne Textnachrichten oder die Grundtabellen umzubauen.
