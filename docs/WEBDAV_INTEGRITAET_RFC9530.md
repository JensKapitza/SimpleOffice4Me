# WebDAV-Übertragungsintegrität nach RFC 9530

## Zweck und Nutzen

WebDAV transportiert Office-, Mail- und Archivdateien häufig über mehrere
HTTP-Verbindungen, Reverse Proxies oder Synchronisationsläufe. TLS schützt die
jeweilige Verbindung, erkennt aber nicht jede Beschädigung zwischen mehreren
Systemgrenzen. SimpleOffice4Me ergänzt deshalb die bestehenden starken ETags
um die aktuellen HTTP-Felder `Content-Digest`, `Repr-Digest` und
`Want-Content-Digest` aus [RFC 9530](https://www.rfc-editor.org/rfc/rfc9530.html).

- Ein Client kann bei `PUT` SHA-256 oder SHA-512 mitsenden. Eine abweichende
  Datei wird verworfen, bevor Quote, Version oder Nutzdatei verändert werden.
- `GET` liefert einen Digest der vollständigen ausgewählten Repräsentation und
  einen Digest der tatsächlich übertragenen Bytes.
- Bei `Range`-Downloads bleibt `Repr-Digest` auf die vollständige Datei
  bezogen. `Content-Digest` schützt den einzelnen Bereich oder den vollständigen
  `multipart/byteranges`-Nachrichteninhalt. Dadurch kann ein Client mehrere
  Downloads zu einer Datei zusammensetzen und beide Ebenen prüfen.
- `HEAD` liefert `Repr-Digest`, aber keinen irreführenden Inhaltsdigest, weil
  eine HEAD-Antwort keinen Nachrichteninhalt überträgt.

LibreOffice, Nautilus, Windows Explorer, Finder und FreeFileSync müssen diese
Felder nicht kennen. Unbekannte HTTP-Antwortfelder werden ignoriert; der
bisherige Öffnen-, Speichern- und Sync-Ablauf bleibt kompatibel. Werkzeuge oder
Skripte mit Digest-Unterstützung erhalten zusätzliche Ende-zu-Ende-Prüfung.

## Bedienung und Konfiguration

Die Ausgabe von Digests ist nach dem Update ohne zusätzliche Konfiguration
aktiv. Für normale Desktop-Clients bleibt die Einrichtung aus
[WEBDAV_DATEIVERWALTUNG.md](WEBDAV_DATEIVERWALTUNG.md) und
[LIBREOFFICE_WEBDAV.md](LIBREOFFICE_WEBDAV.md) unverändert.

Ein schreibender Client berechnet den Digest über exakt die Bytes des PUT-Body
und sendet beispielsweise:

```http
Content-Digest: sha-256=:BASE64-DES-32-BYTE-SHA256-WERTES:
```

oder, für Algorithmuswechsel und doppelte Prüfung:

```http
Content-Digest: sha-512=:BASE64-DES-64-BYTE-SHA512-WERTES:, sha-256=:BASE64-DES-32-BYTE-SHA256-WERTES:
```

`OPTIONS` und erfolgreiche `PUT`-Antworten nennen die Präferenz
`Want-Content-Digest: sha-512=9, sha-256=10`. Das ist gemäß RFC nur ein Hinweis:
Clients ohne Unterstützung dürfen weiter ohne Digest schreiben. ETag oder
Lock-Token bleiben bei vorhandenen Dateien unabhängig davon Pflicht.

Nach einem erfolgreichen `PUT` enthalten `Content-Location` und `Repr-Digest`
eine eindeutige Beschreibung der nun gespeicherten Ressource. Ein Client kann
den Wert direkt mit seinem lokalen SHA-256 vergleichen.

## Voraussetzungen

- HTTPS und ein getrennter WebDAV-App-Zugang bleiben erforderlich.
- Es werden ausschließlich Funktionen der Python-Standardbibliothek verwendet;
  zusätzliche Pakete oder externe Dienste sind nicht nötig.
- Proxy und WSGI-Server müssen die vier Digest-Felder unverändert weitergeben.
- Das bestehende Upload-Limit gilt vor der Prüfung weiterhin auf HTTP-Ebene.

## Auswertung des Standards

### MUST

- [RFC 9530 Abschnitt 2](https://www.rfc-editor.org/rfc/rfc9530.html#section-2)
  definiert `Content-Digest` als Structured-Fields-Dictionary aus
  Algorithmusschlüssel und Byte Sequence. Die Implementierung akzeptiert diese
  Form, prüft Base64 und die exakte Digestlänge und lehnt doppelte oder
  mehrdeutige unterstützte Schlüssel ab.
- [Abschnitt 3](https://www.rfc-editor.org/rfc/rfc9530.html#section-3) bezieht
  `Repr-Digest` auf die vollständige ausgewählte Repräsentation. Deshalb bleibt
  dieser Wert auch bei 206-Antworten der Digest der ganzen Datei.
- Für zustandsändernde Antworten verlangt
  [Abschnitt 3.1](https://www.rfc-editor.org/rfc/rfc9530.html#section-3.1) die
  Berechnung über die referenzierte Repräsentation. SimpleOffice4Me kennzeichnet
  diese nach `PUT` mit `Content-Location` und berechnet `Repr-Digest` über den
  tatsächlich atomar gespeicherten Dateistand, nicht blind über den Request.
- Abgekündigte Algorithmen dürfen nach
  [Abschnitt 5](https://www.rfc-editor.org/rfc/rfc9530.html#section-5) nicht für
  eine potentiell gegnerische Situation verwendet werden. MD5, SHA-1 und
  Prüfsummenalgorithmen werden daher nicht als ausreichende Uploadprüfung
  akzeptiert.

### SHOULD und RECOMMENDED

- RFC 9530 empfiehlt in
  [Abschnitt 5](https://www.rfc-editor.org/rfc/rfc9530.html#section-5), aktive
  Algorithmen zu verwenden. Implementiert sind die im
  [IANA-Register](https://www.iana.org/assignments/http-digest-hash-alg/)
  aktiven Schlüssel `sha-256` und `sha-512`; SHA-256 hat für die vorhandene
  ETag- und Metadatenarchitektur die höchste Präferenz.
- Werden mehrere unterstützte Digests geliefert, müssen alle übereinstimmen.
  Das verhindert, dass ein korrekter schwächer gewichteter Wert einen
  abweichenden zweiten Wert verdeckt.

### MAY

- [Abschnitt 2](https://www.rfc-editor.org/rfc/rfc9530.html#section-2) erlaubt
  anwendungsspezifische Prüfregeln. SimpleOffice4Me macht den Header optional,
  validiert ihn aber strikt, sobald ein Client ihn sendet. Ein behaupteter,
  nicht prüfbarer Digest wird nicht stillschweigend ignoriert.
- [Abschnitt 4](https://www.rfc-editor.org/rfc/rfc9530.html#section-4) erlaubt
  Präferenzfelder in Antworten. `Want-Content-Digest` kündigt SHA-256 und
  SHA-512 mit Gewichten von 10 und 9 an.
- Digest-Felder dürften als Trailer übertragen werden. Trailer werden bewusst
  nicht verarbeitet, weil Flask/übliche WSGI-Server sie nicht verlässlich als
  Ende-zu-Ende-Eingabe bereitstellen; unterstützt werden Header-Felder.

## Sicherheits- und Datenschutzverhalten

RFC 9530 weist in
[Abschnitt 6.1](https://www.rfc-editor.org/rfc/rfc9530.html#section-6.1) darauf
hin, dass Digests keine Authentifizierung und keinen Schutz aller HTTP-Felder
bieten. Ein Angreifer könnte ohne TLS auch Datei und Digest gemeinsam ersetzen.
Deshalb ersetzen Digests weder HTTPS noch Basic-Authentisierung, Rechteprüfung,
ETag-Vorbedingungen, Locks, ClamAV oder Audit-Historie.

Vor einer Uploadprüfung werden Anmeldung, Schreib-Scope, Benutzerpfad, Lock und
ETag geprüft. Erst danach wird der Digest bewertet; erst bei Erfolg folgen Quote
und atomare Speicherung. Fehlende, ungültige und abweichende Digests erweitern
niemals Rechte. Die Audit-Historie speichert Aktion, Benutzer, Pfad, Algorithmen,
Größe und Zeitpunkt, aber weder den gelieferten Digestwert noch Dateiinhalte.

Die Feldlänge ist auf 2 KiB begrenzt. Unterstützte Werte haben feste
Ausgabelängen. So können sehr große Structured Fields oder Base64-Payloads nicht
als unnötige Speicher- oder CPU-Last dienen. Digests werden mit konstantzeitiger
Byteprüfung verglichen.

## Fehler- und Ausfallverhalten

| Situation | Antwort | Dateiwirkung |
| --- | --- | --- |
| Digest stimmt überein | normales `201`/`204` | atomare Anlage oder neue Revision |
| Digest stimmt nicht | `422 Unprocessable Content` | keine Datei-, Quota- oder Versionsänderung |
| Syntax, Base64, Länge oder doppelter Schlüssel ungültig | `400 Bad Request` | keine Änderung |
| nur nicht unterstützte/abgekündigte Algorithmen | `400 Bad Request` mit Präferenz | keine Änderung |
| ETag veraltet | `412 Precondition Failed` vor Digestprüfung | keine Änderung |
| fremder Lock | `423 Locked` vor Digestprüfung | keine Änderung |
| Client sendet keinen Digest | bisheriger PUT-Ablauf | kompatibles Verhalten |

Ein Prozess- oder Datenträgerfehler nach erfolgreicher Prüfung wird weiterhin
durch den bestehenden temporären, atomaren Schreibpfad behandelt. Der Digest
ersetzt kein Backup. Bei einem Fehler kann derselbe idempotent abgesicherte PUT
mit aktuellem ETag oder Lock wiederholt werden.

## Formate und Interoperabilität

- Syntax: RFC-8941-Dictionary mit Byte Sequences, wie von RFC 9530 verlangt.
- Algorithmen: `sha-256` und `sha-512`; Antwortdigests verwenden `sha-256`.
- Vollständige GET-Antwort: `Content-Digest` und `Repr-Digest` sind identisch.
- Ein Bereich: `Content-Digest` schützt nur die 206-Nutzbytes;
  `Repr-Digest` schützt die ganze Datei.
- Mehrfachbereiche: `Content-Digest` wird über den vollständigen MIME-artigen
  Multipart-Inhalt einschließlich Grenzen und Teilkopfzeilen berechnet.
- HEAD: nur `Repr-Digest`.
- 304, 412 und 416 dürfen Repräsentationsmetadaten enthalten, liefern aber
  keinen behaupteten Digest eines nicht übertragenen Inhalts.
- Die veralteten Felder `Digest` und `Want-Digest` aus RFC 3230 werden nicht
  erzeugt oder als Sicherheitsnachweis akzeptiert.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenmigration und keine Änderung am Dateiformat, Dokumentindex,
an Berechtigungen oder Aufbewahrungsregeln. Bestehende App-Passwörter und
Desktop-Einbindungen bleiben gültig. Clients, die neue Felder ignorieren,
arbeiten wie zuvor. Ein Client aktiviert Uploadprüfung selbst, indem er
`Content-Digest` sendet.

## Tests

Automatisierte Tests decken ab:

- vollständige GET- und HEAD-Antworten auf stabilem und hierarchischem Pfad;
- einzelne, offene, Suffix- und mehrteilige Range-Antworten;
- SHA-256, SHA-512 und beide Algorithmen in einem Request;
- neue und vorhandene Dateien sowie beide PUT-Endpunkte;
- abweichende, ungültige, doppelte und ausschließlich nicht unterstützte Werte;
- Mutation-freie 400-/422-Fehler, Audit ohne Digest-Leak und Präferenzfelder;
- Zusammenspiel mit ETag, Locks, Quote, Benutzertrennung und atomarer Versionierung.

## Bekannte Grenzen und Deaktivierung

- LibreOffice, FreeFileSync und Betriebssystem-Dateimanager prüfen RFC-9530-
  Felder je nach Version möglicherweise nicht selbst. Die Header stören ihren
  bisherigen WebDAV-Ablauf nicht.
- Upload-Digests bleiben zur Kompatibilität optional. Eine serverweite Pflicht
  ist noch nicht konfigurierbar.
- HTTP-Trailer, HTTP Message Signatures sowie Digests von PROPFIND-/REPORT-XML
  sind nicht implementiert.
- Parameter an Structured-Field-Dictionary-Einträgen werden nicht ausgewertet;
  ein Client muss die Digest-Byte-Sequence ohne Parameter senden. Damit werden
  nicht verstandene Sicherheitszusagen nicht stillschweigend ignoriert.
- SHA-256 wird beim Öffnen bereits für den starken ETag über die vollständige
  Datei berechnet. Digests verursachen dafür keinen zweiten Vollscan;
  `Content-Digest` für Range-Antworten benötigt einen zusätzlichen begrenzten
  Lesevorgang über die ausgelieferten Bereiche.

Zur faktischen Deaktivierung auf Clientseite wird kein `Content-Digest`
gesendet; damit gilt exakt der bisherige Uploadpfad. Antwortfelder können von
Clients ignoriert oder am Reverse Proxy entfernt werden. Für eine vollständige
Rückkehr zum früheren Serververhalten kann der zugehörige Commit ohne
Datenrückbau zurückgenommen werden, da keine gespeicherten Nutzdaten oder
Schemata verändert werden.
