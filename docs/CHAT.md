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

## Richtlinien für strukturierte Chat-Aktionen

Strukturierte Aktionen wie Kontaktangebote, Umfragen und Anfragen besitzen zusätzlich zur allgemeinen Chat-Freigabe eigene, standardmäßig gesperrte Regeln. Beispiel:

```json
{
  "chat": {
    "send": true,
    "receive": true,
    "actions": {
      "contact": {"send": true, "receive": true},
      "poll": {"send": true, "receive": true},
      "request": {"send": true, "receive": true}
    }
  }
}
```

Vor der Übertragung einer strukturierten Aktion wird ein signierter Preflight ausgeführt. Dabei werden nur Aktionstyp, Chat-ID, Nachrichten-ID und Teilnehmerbezug übertragen, aber **keine Kontaktdaten, Formulardaten, Anhänge oder sonstige Nutzdaten der Aktion**.

Wird die Aktion lokal durch den eigenen Administrator verboten, bleibt die Nutzlast lokal und im Chat erscheint z. B. `Aktion „Kontaktangebot“: Durch die Serverregeln deines Admins blockiert.` Die Gegenseite erhält in diesem Fall nichts.

Erlaubt die lokale Seite die Aktion, die Gegenseite aber nicht, erzeugt die empfangende Instanz einen lokalen Systemhinweis wie `Aktion „Kontaktangebot“ von <Benutzer>: Durch die Serverregeln deines Admins abgelehnt.` Die sendende Instanz erhält nur den maschinenlesbaren Ablehnungscode `admin_policy` zurück und erzeugt ihrerseits den Hinweis `Aktion „Kontaktangebot“: Durch die Serverregeln des Chatpartners abgelehnt.` Die verbotene Nutzlast wird nicht übertragen.

Der eigentliche Event-Endpunkt prüft dieselbe Aktionsregel erneut. Ein Peer kann die Sicherheitsprüfung daher nicht umgehen, indem er den Preflight auslässt.

## Erweiterung ohne Schemawechsel

`chat_message` besitzt bereits `message_type` und ein versionierbares JSON-Payload. Stufe 1 erzeugt im UI nur `text`. Reservierte Typen sind bereits:

- `contact` – Kontakt bzw. Contact-ID teilen,
- `poll` – Umfrage mit Optionen und späteren Antworten,
- `request` – strukturierte Anfrage, z. B. „Kontakt vervollständigen“ oder „fehlende Datei hochladen“,
- `system` – lokale, nicht vom Benutzer erzeugte Status- und Policy-Hinweise.

Damit können spätere Stufen Formulare und Aktionen ergänzen, ohne Textnachrichten oder die Grundtabellen umzubauen.
