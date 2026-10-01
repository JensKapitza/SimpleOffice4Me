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
- `mail_case_draft`: Antwortentwürfe einschließlich geprüfter Attachment-Referenzen; noch kein impliziter Versand.

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
Diese Datenstruktur gibt keine IMAP-/SMTP-Credentials weiter. Der v3-Federation-
Envelope handelt den Objekttyp `mail_cases` unabhängig von den älteren
Mail-Fingerprint-Endpunkten aus. Empfangende Administratoren können entfernte
Benutzer-IDs peergebunden einem aktiven lokalen Benutzer zuordnen. Die Zuordnung
liegt in `mail-cases.sqlite3`, kann über die Admin-Endpunkte unter
`/admin/federation/peer-discovery/mail-case-identities` angelegt, ersetzt und
entfernt werden und wird in der Federation-Ereignishistorie protokolliert.

Die Zuordnung allein schaltet noch keine Fallübertragung frei: direkter Trust,
ausgehandelte `mail_cases`-Version und die Receive-Policy des Peers bleiben
erforderlich. Eine fehlende oder ungültige Zuordnung darf nie auf einen
gleichnamigen lokalen Benutzer zurückfallen.

Der berechtigte lokale Teilnehmer kann über „Vorgang an Peer einladen“ einen
Vorgang an einen zuvor hinzugefügten föderierten Teilnehmer senden. Die
Empfangsinstanz erstellt einen lokalen Schattenvorgang mit eigener Fall-ID und
merkt die Zuordnung zur Fall-ID des Absenders peergebunden vor. Einladungen
übertragen keine EML-Datei und keine Mailkonto-Zugangsdaten. Spätere
Ereignisse lösen die Absender-ID über diese Zuordnung auf.

Die Mail-API `POST /documents/mail/cases/<case-id>/federation/<peer-id>/events`
nimmt Kommentar-, Entwurfs-, Versandanforderungs- sowie
Freigabe-/Ablehnungsereignisse entgegen. Der Versand nutzt den gespeicherten
Peer-Token über HTTPS; direkter Trust, `mail_cases`-Capability und die lokale
Send-Policy müssen aktiv sein. Temporäre Netzwerkfehler landen in einer
persistenten Outbox mit maximal 1.000 Einträgen und exponentiellem Backoff.
Der Retry-Worker lässt sich über `SIMPLEOFFICE_MAIL_CASE_FEDERATION_WORKER=0`
abschalten. Ereignisse sind idempotent. Auf der Empfangsinstanz
werden Ereignisse bei aktivierter `auto_accept`-Receive-Policy angewendet;
andernfalls bleiben sie `pending` und können durch einen Administrator geprüft
werden. Das SMTP-Senden bleibt auf der Instanz des Mailkontos.

EML-Referenzereignisse enthalten nur einen opaken Locator und SHA-512-Hash.
Der Abruf prüft den direkten Peer-Trust, nutzt HTTPS und verifiziert den
SHA-512-Hash vor der Vorschau. EML-Anhänge werden im Schattenvorgang nicht zum
Download angeboten, solange kein lokaler Malware-Scan vorliegt. Der
Schattenvorgang ist ein Kollaborationskontext, kein lokales Mailkonto; seine
synthetische Konto-ID erlaubt keinen SMTP-Zugriff.

## Oberfläche

Die vorhandene Mailoberfläche bleibt erhalten. Aus einer Live- oder
Archiv-Nachricht kann ein Vorgang erstellt oder die E-Mail einem vorhandenen
Vorgang hinzugefügt werden. Postfach- und Archivlisten zeigen bei eindeutig
zugeordneten Nachrichten direkt den Vorgang als Indikator an. Bei einem
eindeutigen Thread-Treffer wird die passende Zuordnung angeboten, aber nicht
automatisch ausgeführt.

Die Vorgangsansicht enthält:

- Titel und Status,
- Teilnehmer und Rechte,
- Timeline aus E-Mails, internen Kommentaren und Entwürfen,
- EML-Vorschau,
- interne Kommentarfunktion,
- Antwortentwürfe mit geprüften Anhängen,
- persönlicher Lesestatus je Nachricht und Teilnehmer,
- bestehende ClamAV-gesicherte Anhangsdownloads,
- gefilterte Vorgangshistorie aus der manipulationsgeschützten RevisionHistory.

Eine reine Vorgangsansicht öffnet keine IMAP-Verbindung. Dadurch können
berechtigte Mitarbeiter an archivierten E-Mails mitarbeiten, ohne Zugriff auf
das ursprüngliche Mailkonto oder dessen Zugangsdaten zu erhalten.

## Interne Kommentare, Entwürfe und Versandfreigabe

Interne Kommentare sind eigene Vorgangsdatensätze und werden niemals an SMTP
übergeben. Entwürfe speichern An/CC/BCC, optionale Absenderidentität, Betreff und
Text sowie eine begrenzte Liste geprüfter Attachment-Referenzen getrennt von
der EML. Das Erstellen oder Ändern eines Entwurfs versendet keine Nachricht.

Ein Teilnehmer mit `send_request` kann den aktuellen Entwurf zur Freigabe
einreichen. Ab `ready` ist der Inhalt gesperrt, damit er nach Einreichung nicht
unbemerkt verändert werden kann. Nur der Mailkontoinhaber kann die Anfrage
freigeben oder ablehnen. Ein freigegebener Entwurf kann nur vom Kontoinhaber
versendet werden und nur dann, wenn für das Konto der bestehende explizite
Schreibmodus aktiviert ist.

Vor dem SMTP-Zugriff wird der Entwurf atomar auf `sending` gesetzt. Parallele
oder wiederholte Klicks können deshalb keinen zweiten Versand starten. Bei einem
eindeutig vor der Annahme feststehenden Fehler wird `failed` gespeichert; eine
Wiederholung erfordert eine neue bewusste Anforderung und Freigabe. Ist nach dem
SMTP-Aufruf dagegen unklar, ob der Server die Nachricht bereits angenommen hat,
bleibt der Entwurf absichtlich in `sending`, damit keine Doppelzustellung durch
einen Retry entsteht. Nach erfolgreichem SMTP-Versand werden die ausgehende EML
und der Status `sent` mit dem Vorgang verknüpft. Scheitert nur die nachgelagerte
Vorgangsfinalisierung, bleibt ebenfalls `sending` bestehen und die Oberfläche
warnt ausdrücklich davor, erneut zu senden.

CC und BCC werden als SMTP-Empfänger berücksichtigt; BCC wird nicht als
Nachrichtenheader in die EML geschrieben. Die Absenderidentität im Entwurf ist
bewusst nur eine Referenz. Der tatsächliche Absender stammt weiterhin aus der
konfigurierten SMTP-Identität des Kontoinhabers. Eine vollständige
Identitäts-/Signaturverwaltung und ein Federation-Transport des Freigabeablaufs
gehören nicht zu diesem Kern.

## Anhänge

Anhänge eingehender oder bereits archivierter E-Mails bleiben Bestandteil der
jeweiligen unveränderten EML. Beim Download aus einem Vorgang wird der bestehende
ClamAV-Gate wiederverwendet.

Entwurfsanhänge durchlaufen vor jeder Aufnahme in einen Entwurf den vorhandenen
`AttachmentSecurity.scan_webdav_upload`-Quarantänepfad. Nur der Verdict
`clean` wird in den verwalteten Dokumentenspeicher übernommen. Die Dateien
liegen unter einem eigenen MailCase-Ordner, dessen Dokument-ACL ausschließlich
den Mailkontoinhaber als `manage` enthält. Berechtigte Vorgangsteilnehmer lesen
den Anhang nur über die Vorgangsroute nach erneuter serverseitiger Case-ACL-
Prüfung; dadurch entsteht keine unabhängige Dokumentfreigabe.

Im Entwurf werden nur Dokument-ID, bereinigter Dateiname, MIME-Typ, Größe,
SHA-256 und Scan-ID gespeichert. Vor Download und SMTP-Versand werden
Vorgangs-/Entwurfsherkunft, Malware-Scan, Scan-ID, SHA-256 und Dateigröße erneut
gegen die aktuelle Datei geprüft. Eine nach dem Scan veränderte Datei wird
abgewiesen. Es gelten maximal 20 Entwurfsanhänge, 50 MiB pro Anhang und weiterhin
das bestehende 25-MiB-Limit für die vollständig serialisierte ausgehende E-Mail.

BCC-Empfänger werden nur in der SMTP-Envelope geführt; auch bei Nachrichten mit
Anhängen entsteht kein `Bcc`-Header.

Interne Kommentar-Anhänge sind derzeit nicht implementiert; sie sind optional
und kein Akzeptanzkriterium des Kernmodells.

## Audit

Kritische Vorgangsoperationen werden über die bestehende
`RevisionHistory`/MailStore-Historie auditiert. Protokolliert werden Ereignis,
Akteur, Vorgangs-ID und notwendige technische IDs bzw. Statuswerte. Mailtexte,
Passwörter und vollständige Credentials werden nicht in Audit-Details kopiert.

Unter anderem werden Erstellung, Mailzuordnung, Entfernen einer Zuordnung,
Teilnehmeränderungen, Lesestatus, Kommentare, Entwürfe, Statusänderungen,
EML-Ansicht, Entwurfs-Anhangsänderungen, Versandfreigaben, Versandstatus und
Anhangsdownloads erfasst. Die Vorgangsoberfläche zeigt die letzten gefilterten
Audit-Ereignisse an; geheime Felder bleiben dabei durch die bestehende
RevisionHistory-Redaktion ausgeschlossen.

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
- getrennten persönlichen Lesestatus einschließlich UI-Projektion,
- mehrere E-Mails und Kontogrenzen,
- Entfernen einer Zuordnung ohne EML-Löschung,
- interne Kommentare,
- Entwurfserstellung und -änderung,
- ClamAV-geprüfte owner-private Entwurfsanhänge samt Hash-/Provenienzprüfung,
- manipulationsgeschützte Versandanforderung und Kontoinhaber-Freigabe,
- Read-only-Sperre vor SMTP und atomaren Doppelversandschutz,
- CC/BCC-Verarbeitung ohne BCC-Header,
- exaktes und kontogebundenes Threading,
- stabile SHA-512-Identität bei Live-Mails,
- Route-/UI-Zugriff für delegierte Teilnehmer ohne Mailkonto,
- bestehende IMAP-Read-only- und Mail-Webclient-Funktionen.

Automatische Zustellung lokaler Kommentare und Entwurfsänderungen an alle
föderierten Teilnehmer, Signaturverwaltung und automatischer delegierter
Versand bleiben außerhalb dieses Kernmoduls.


## Versanddelegation und Urlaubsvertretung

Der Kontoinhaber kann in den Mail-Einstellungen eine lokale Vertretung für ein
Mailkonto einrichten. Die Delegation kann dauerhaft oder über ein Start-/Enddatum
begrenzt werden.

Eine Delegation ersetzt keine Vorgangsberechtigung. Der Vertreter muss im
jeweiligen Mail-Vorgang weiterhin mindestens `send_request` besitzen. Ist die
Delegation zum Zeitpunkt der Versandanforderung aktiv, wechselt der eingefrorene
Entwurf direkt von `draft` auf `approved`. Der Vertreter darf anschließend
den Versand auslösen.

Die SMTP-/IMAP-Zugangsdaten werden dabei niemals an den Vertreter ausgegeben.
Der Server löst das gespeicherte SMTP-Konto des Eigentümers erst unmittelbar für
den Versand auf. Der Schreibschutz des Eigentümerkontos bleibt wirksam.
Delegationsänderungen, automatische Freigaben und delegierte Sendungen werden
auditiert.

Nach Ablauf oder Entfernen einer Delegation werden neue Versandanforderungen
wieder auf `ready` gestellt und benötigen die manuelle Freigabe des
Kontoinhabers.

## Konfigurierbare Sichtbarkeit

Die Mail-Einstellungen speichern pro Konto eine Sichtbarkeitskonfiguration für
Postfach, Archiv, Dubletten, neue Nachrichten, Mail-Vorgänge, Teilnehmer,
Kommentare, Antwortentwürfe, Delegation, Kontakte, Kalender und Sieve.

Diese Konfiguration ist ausschließlich eine UI-Konfiguration. Sie erteilt keine
Rechte und darf nicht als Autorisierungsentscheidung verwendet werden.
Vorgangs-ACLs, Kontoeigentum, Schreibschutz und Versanddelegation werden
serverseitig unabhängig davon geprüft.

Vorhandene Konten ohne gespeicherte Sichtbarkeitskonfiguration verwenden aus
Kompatibilitätsgründen weiterhin den bisherigen Zustand: alle Funktionsbereiche
sind sichtbar.
