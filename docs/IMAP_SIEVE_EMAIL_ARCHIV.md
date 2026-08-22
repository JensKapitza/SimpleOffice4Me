# IMAP-Client, Sieve und unveränderliches E-Mail-Archiv

## Zweck und Nutzen

Der Reiter **IMAP** verwaltet benutzergebundene Mailkonten, prüft die Anmeldung,
kopiert Nachrichten als unveränderte `.eml`-Dateien in das Dokumentarchiv und
verwaltet Sieve-Skripte mit lokaler Git-Versionierung. Der Archivclient verändert
den Mailserver nicht: Er verwendet `EXAMINE`, UID-Suche und `BODY.PEEK[]`, aber
niemals `STORE`, `COPY`, `MOVE`, `DELETE` oder `EXPUNGE`.

## Maßgebliche Standards und Entscheidungen

### IMAP

- [RFC 9051 Abschnitt 6.3.3](https://www.rfc-editor.org/rfc/rfc9051.html#section-6.3.3)
  definiert `EXAMINE` als schreibgeschützte Auswahl. Der Archivlauf **MUST** die
  Quelle so öffnen und **MUST NOT** eine `\\Seen`-Markierung verursachen.
- [RFC 9051 Abschnitt 6.4.9](https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.9)
  und [Abschnitt 2.3.1.1](https://www.rfc-editor.org/rfc/rfc9051.html#section-2.3.1.1)
  verlangen, UID zusammen mit `UIDVALIDITY` zu behandeln. Beides wird je Konto
  gespeichert. Ändert sich `UIDVALIDITY`, beginnt die UID-Auswahl neu; SHA-512
  verhindert trotzdem eine zweite Archivkopie.
- [RFC 9051 Abschnitt 6.4.5](https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.5)
  beschreibt `FETCH`. `BODY.PEEK[]` wird verwendet, damit der Abruf keine Flags
  setzt. Die vollständigen Bytes werden unverändert als EML gespeichert.
- [RFC 9051 Abschnitt 11](https://www.rfc-editor.org/rfc/rfc9051.html#section-11)
  verlangt angemessenen Schutz von Zugangsdaten. SimpleOffice akzeptiert nur
  implizites TLS oder STARTTLS mit System-CA-Prüfung; Klartext-IMAP ist nicht
  implementiert. Netzwerkoperationen besitzen ein 30-Sekunden-Limit.
- IMAP ist laut [RFC 9051 Abschnitt 1](https://www.rfc-editor.org/rfc/rfc9051.html#section-1)
  kein Mailversandprotokoll. Termine werden deshalb **nicht** über IMAP versandt.
  Dafür wäre getrennt SMTP Submission nach RFC 6409 plus iTIP nötig.

### Nachrichtenformat und Archividentität

- Die EML bleibt entsprechend [RFC 5322](https://www.rfc-editor.org/rfc/rfc5322.html)
  bytegenau erhalten. Header werden nur für Metadaten gelesen.
- SHA-512 über die vollständigen EML-Bytes ist die Archividentität. Gleiche
  `Message-ID` mit anderem Inhalt bleibt eine andere Nachricht; identische Bytes
  mit anderer UID werden nicht doppelt gespeichert.
- Pro Lauf werden höchstens 1.000 Nachrichten und pro Nachricht höchstens
  100 MiB verarbeitet. Fehler einer Nachricht werden protokolliert und stoppen
  nicht den gesamten Lauf.

### Sieve und ManageSieve

- [RFC 5228 Abschnitt 2.10.6](https://www.rfc-editor.org/rfc/rfc5228.html#section-2.10.6)
  beschreibt die Trennung zwischen Skript und Ausführung. Der Editor speichert
  deshalb zuerst eine lokale Version; Upload und Aktivierung sind explizite
  Aktionen.
- [RFC 5804 Abschnitt 2](https://www.rfc-editor.org/rfc/rfc5804.html#section-2)
  definiert ManageSieve-Kommandos und Antworten. Implementiert sind STARTTLS,
  `AUTHENTICATE PLAIN`, `PUTSCRIPT`, `SETACTIVE` und `LOGOUT`.
- Skripte **MUST** benutzer- und kontogebunden sein, **MUST** vor Upload lokal
  versioniert werden und **MUST NOT** größer als 1 MiB sein.
- Server-Skripte werden nicht automatisch gelöscht. `DELETESCRIPT` ist bewusst
  nicht implementiert.

## Bedienung

1. Im Reiter **IMAP** Server, Port, TLS-Modus, Benutzername und Quellordner
   eintragen. ManageSieve nutzt üblicherweise Port 4190.
2. Das Passwort kann pro Aktion eingegeben werden. Optional kann es
   installationsgebunden AES-256-GCM-verschlüsselt gespeichert werden. Alternativ
   verweist `Passwort-Env` auf eine geschützte Umgebungsvariable.
3. **Login testen** liest Fähigkeiten und Ordneranzahl, verändert aber keine Mail.
4. **Jetzt kopieren** startet einen begrenzten, inkrementellen Archivlauf.
5. Anhänge werden nur bei gesetzter Bestätigung extrahiert. Dann gelten die
   vorhandenen Größenlimits, ClamAV-Prüfung, Quarantäne, Herkunftstags und Audit.
   Ohne bestätigte Extraktion wird nur die originale EML archiviert.
6. Sieve-Skripte können lokal gespeichert, hochgeladen oder hochgeladen und
   aktiviert werden. Jede Speicherung erzeugt eine Git-Auditversion.

## Rechte, Sicherheit und Datenschutz

- Konten, Zugangsdaten, Archivzustand und Sieve-Skripte sind pro SimpleOffice-
  Benutzer getrennt. Ein Benutzer kann keine fremde Konto-ID verwenden.
- Geheimnisse erscheinen weder in Templates, Audit-Snapshots noch Logs.
- Das verschlüsselte Passwort ist an den dauerhaften Installationsschlüssel
  gebunden. Geht dieser verloren, muss das Mailpasswort neu eingetragen werden.
- Die Archivdateien liegen unter `email/<Benutzer-Hash>/<Konto>/<Jahr>/<SHA-512>.eml` und werden
  als normale Dokumente mit Ordnerrechten, Metadaten und Audit behandelt.
- Sieve verändert den künftigen Zustellpfad auf dem Mailserver. Aktivierung ist
  deshalb nie Bestandteil des bloßen Speicherns.

## Auswertung des bereitgestellten ZIP-Pakets

Gut gelöst waren Dry-Run als Standard, UID/UIDVALIDITY, `BODY.PEEK[]`, Schutzregeln,
begrenzte Batches, nachvollziehbare Entscheidungen und Hash-Verifikation. Diese
Ideen wurden unabhängig in die vorhandene SimpleOffice-Architektur übertragen.

Verbessert wurden:

- kein Lösch- oder `EXPUNGE`-Pfad im Archivclient;
- SHA-512 statt SHA-256 als dauerhafte EML-Archividentität;
- unveränderte EML plus separate, bestätigte und virengeprüfte Anhänge;
- Benutzertrennung, verschlüsselte Secrets, Audit und Git-Versionen;
- webbasierter Sieve-Editor mit getrenntem Speichern, Upload und Aktivieren;
- begrenzte Laufzeit, Nachrichtengröße und Anzahl.

Neun ZIP-Einträge besitzen exakt denselben SHA-256-Inhalt wie
`email_inbound_apply.py` und sind daher keine auswertbaren eigenständigen
Skripte. Aus dem ZIP wurde kein Quellcode übernommen.

## Fehler- und Ausfallverhalten

- TLS-, Login-, Ordner- oder Protokollfehler werden verständlich gemeldet; das
  Passwort wird nicht protokolliert.
- Erst nach atomarer lokaler Speicherung und Metadatenregistrierung wird eine UID
  als bearbeitet gespeichert.
- Ein neuer Lauf setzt am letzten UID-Stand fort. Bei geänderter `UIDVALIDITY`
  wird erneut gelesen und über SHA-512 dedupliziert.
- Fehlender oder fehlerhafter ClamAV verhindert die Anhangsübernahme, nicht aber
  die zuvor gespeicherte originale EML.

## Migration, Rückwärtskompatibilität und Deaktivierung

Es gibt keine Datenbankmigration. Ohne gespeicherte Konten ist der neue Reiter
wirkungslos. Zum Deaktivieren keine Archivläufe starten; vorhandene EML bleiben
normale Dokumente. Gespeicherte Konfiguration liegt unter
`.simpleoffice-meta/mail/`. Ein Administrator kann diesen Bereich nach Sicherung
entfernen; Mailserverdaten werden dadurch nicht verändert.

## Tests und bekannte Grenzen

Automatisiert geprüft werden Verschlüsselung, Benutzertrennung, Skriptversionen,
Navigation, bytegleiche EML-Ablage, UIDVALIDITY-Herkunft und das Fehlen sämtlicher
mutierender IMAP-Kommandos. Echte Server unterscheiden sich bei SASL-Mechanismen:
ManageSieve unterstützt derzeit nur `PLAIN` innerhalb TLS. OAuth2, SCRAM,
`CHECKSCRIPT`, serverseitiger Skriptdownload, Hintergrundplanung und SMTP/iTIP-
Versand sind dokumentierte nächste Ausbaustufen.
