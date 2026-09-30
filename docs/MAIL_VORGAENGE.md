# Mail-Vorgänge

## Zweck

Mail-Vorgänge ergänzen den bestehenden IMAP-/SMTP-/EML-Stack um kollaborative
Bearbeitung. Die originale E-Mail bleibt unverändert im privaten EML-Archiv.
Ein Vorgang speichert ausschließlich Metadaten wie Teilnehmer, Rechte,
Lesestatus, interne Kommentare, Entwürfe und Status.

Damit bleiben drei Zustände bewusst getrennt:

- **IMAP-Status** wie `\\Seen` gehört zum Mailserver und wird weiterhin nur
  über die vorhandenen IMAP-Funktionen verändert.
- **Persönlicher Lesestatus** gehört zum Vorgang und wird pro Teilnehmer und
  EML-Referenz gespeichert. Das Öffnen im Vorgang setzt kein IMAP-Flag.
- **Vorgangsstatus** beschreibt den Arbeitsstand: `offen`,
  `in_bearbeitung`, `wartet` oder `erledigt`.
- **Entwurfs-/Versandstatus** bildet die Freigabe getrennt ab: `draft`,
  `ready`, `approved`, `sending`, `sent`, `rejected` oder `failed`.

## Architektur und Datenhaltung

`MailCaseStore` liegt im bestehenden Control-Bereich unter
`.simpleoffice-meta/mail-cases.sqlite3`. SQLite läuft mit Foreign Keys und WAL;
schreibende Vorgänge verwenden `BEGIN IMMEDIATE`, damit parallele Änderungen
nicht still überschrieben werden.

Die Tabellen bilden folgende Bereiche ab:

- `mail_case`: Titel, Status, Konto, Kontoinhaber und Zeitstempel.
- `mail_case_message`: Referenz auf eine vorhandene EML sowie Richtung und
  Threading-Header.
- `mail_case_participant`: lokale oder vorbereitete föderierte Teilnehmer mit
  expliziten Rechten.
- `mail_case_read_state`: `first_read_at` und `last_read_at` je Teilnehmer
  und Nachricht.
- `mail_case_comment`: ausschließlich interne Kommunikation.
- `mail_case_draft`: Antwortentwürfe; noch kein impliziter Versand.

Mailinhalt, IMAP-/SMTP-Passwörter und andere Credentials werden nicht in dieser
Datenbank gespeichert.

## E-Mail-Identität und Zuordnung

Für archivierte Nachrichten wird die SHA-512-Identität der unveränderten EML
verwendet: `sha512:<digest>`. Live gelesene IMAP-Nachrichten erhalten vor der
Vorgangszuordnung dieselbe SHA-512-Identität und werden über den vorhandenen
read-only Archivpfad als EML gesichert. Eine IMAP UID allein ist keine dauerhafte
globale Identität.

Ein Vorgang kann mehrere E-Mails referenzieren. Das Entfernen einer Zuordnung
löscht nur Vorgangsmetadaten; die EML bleibt im Archiv erhalten.

Für Thread-Vorschläge werden ausschließlich exakte `Message-ID`,
`In-Reply-To` und `References` ausgewertet. Die Suche ist auf das betreffende
Mailkonto begrenzt. Mehrdeutige Treffer werden nicht automatisch verknüpft.

## Rechte

Jede Operation prüft die Rechte serverseitig. Unterstützt werden:

- `read`
- `comment`
- `compose`
- `send_request`
- `manage_participants`
- `manage_status`
- `manage_mail`

Der Kontoinhaber wird beim Erstellen als lokaler Teilnehmer mit allen Rechten
eingetragen und kann weder entfernt noch auf einen eingeschränkten Rechtesatz
reduziert werden. Ein Benutzer ohne `read` sieht den Vorgang nicht.
`comment`, `compose`, `send_request`, Teilnehmer-, Status- und Mailänderungen
werden jeweils separat geprüft. Eine Versandfreigabe und der tatsächliche
SMTP-Versand dürfen ausschließlich durch den Besitzer des zugehörigen
Mailkontos erfolgen.

Teilnehmer können als `local_user` oder als vorbereiteter
`federated_user` mit `peer_id` und `remote_user_id` modelliert werden.
Diese Datenstruktur gibt keine IMAP-/SMTP-Credentials weiter und implementiert
noch keinen vollständigen Federation-Versand.

## Oberfläche

Die vorhandene Mailoberfläche bleibt erhalten. Aus einer Live- oder
Archiv-Nachricht kann ein Vorgang erstellt oder die E-Mail einem vorhandenen
Vorgang hinzugefügt werden. Bei einem eindeutigen Thread-Treffer wird die
passende Zuordnung angeboten, aber nicht automatisch ausgeführt.

Die Vorgangsansicht enthält:

- Titel und Status,
- Teilnehmer und Rechte,
- Timeline aus E-Mails, internen Kommentaren und Entwürfen,
- EML-Vorschau,
- interne Kommentarfunktion,
- Antwortentwürfe,
- bestehende ClamAV-gesicherte Anhangsdownloads.

Eine reine Vorgangsansicht öffnet keine IMAP-Verbindung. Dadurch können
berechtigte Mitarbeiter an archivierten E-Mails mitarbeiten, ohne Zugriff auf
das ursprüngliche Mailkonto oder dessen Zugangsdaten zu erhalten.

## Interne Kommentare, Entwürfe und Versandfreigabe

Interne Kommentare sind eigene Vorgangsdatensätze und werden niemals an SMTP
übergeben. Entwürfe speichern An/CC/BCC, optionale Absenderidentität, Betreff und
Text getrennt von der EML. Das Erstellen oder Ändern eines Entwurfs versendet
keine Nachricht.

Ein Teilnehmer mit `send_request` kann den aktuellen Entwurf zur Freigabe
einreichen. Ab `ready` ist der Inhalt gesperrt, damit er nach Einreichung nicht
unbemerkt verändert werden kann. Nur der Mailkontoinhaber kann die Anfrage
freigeben oder ablehnen. Ein freigegebener Entwurf kann nur vom Kontoinhaber
versendet werden und nur dann, wenn für das Konto der bestehende explizite
Schreibmodus aktiviert ist.

Vor dem SMTP-Zugriff wird der Entwurf atomar auf `sending` gesetzt. Parallele
oder wiederholte Klicks können deshalb keinen zweiten Versand starten. Bei einem
Transportfehler wird `failed` gespeichert; eine Wiederholung erfordert eine
neue bewusste Anforderung und Freigabe. Nach erfolgreichem SMTP-Versand werden
die ausgehende EML und der Status `sent` mit dem Vorgang verknüpft. Scheitert
nur die nachgelagerte Vorgangsfinalisierung, bleibt `sending` bestehen und die
Oberfläche warnt ausdrücklich davor, erneut zu senden.

CC und BCC werden als SMTP-Empfänger berücksichtigt; BCC wird nicht als
Nachrichtenheader in die EML geschrieben. Die Absenderidentität im Entwurf ist
bewusst nur eine Referenz. Der tatsächliche Absender stammt weiterhin aus der
konfigurierten SMTP-Identität des Kontoinhabers. Eine vollständige
Identitäts-/Signaturverwaltung und ein Federation-Transport des Freigabeablaufs
gehören nicht zu diesem Kern.

## Anhänge

Anhänge bleiben Bestandteil der jeweiligen EML. Beim Download aus einem Vorgang
wird der bestehende ClamAV-Gate wiederverwendet. Ein Vorgang erzeugt keine
zweite unsichere Attachment-Ablage.

Interne Kommentar-Anhänge sind derzeit nicht implementiert; sie sind optional
und kein Akzeptanzkriterium des Kernmodells.

## Audit

Kritische Vorgangsoperationen werden über die bestehende
`RevisionHistory`/MailStore-Historie auditiert. Protokolliert werden Ereignis,
Akteur, Vorgangs-ID und notwendige technische IDs bzw. Statuswerte. Mailtexte,
Passwörter und vollständige Credentials werden nicht in Audit-Details kopiert.

Unter anderem werden Erstellung, Mailzuordnung, Entfernen einer Zuordnung,
Teilnehmeränderungen, Lesestatus, Kommentare, Entwürfe, Statusänderungen,
EML-Ansicht und Anhangsdownload erfasst.

## Sicherheit

- Mailkonto-Zugriff bleibt an den Kontoinhaber gebunden.
- Teilnehmer erhalten nur Zugriff auf die zum Vorgang referenzierten
  archivierten EMLs.
- Jede `case_id`, Teilnehmer- und Mailoperation wird serverseitig geprüft.
- Kontofremde Mailzuordnungen werden abgewiesen.
- Persönlicher Lesestatus verändert `\\Seen` nicht.
- EMLs werden durch Vorgangsoperationen nicht verändert.
- Anhänge durchlaufen weiterhin den vorhandenen Malware-Scan.
- Passwörter und andere Mail-Secrets erscheinen weder in Vorgangsdaten noch im
  HTML oder Audit.

## Tests

Die Regressionstests prüfen insbesondere:

- private Vorgänge und serverseitige Rechte,
- Teilnehmerrechte einschließlich Schutz des Kontoinhabers,
- getrennten persönlichen Lesestatus,
- mehrere E-Mails und Kontogrenzen,
- Entfernen einer Zuordnung ohne EML-Löschung,
- interne Kommentare,
- Entwurfserstellung und -änderung,
- manipulationsgeschützte Versandanforderung und Kontoinhaber-Freigabe,
- Read-only-Sperre vor SMTP und atomaren Doppelversandschutz,
- CC/BCC-Verarbeitung ohne BCC-Header,
- exaktes und kontogebundenes Threading,
- stabile SHA-512-Identität bei Live-Mails,
- Route-/UI-Zugriff für delegierte Teilnehmer ohne Mailkonto,
- bestehende IMAP-Read-only- und Mail-Webclient-Funktionen.

Vollständige Federation-Kommunikation, Signaturverwaltung und automatischer
delegierter Versand bleiben bewusst außerhalb dieses Kernmoduls.
