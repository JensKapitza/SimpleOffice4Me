# IMAP-, SMTP- und Sieve-Client mit unveränderlichem E-Mail-Archiv

## Zweck und Nutzen

Der Reiter **IMAP** verwaltet benutzergebundene Mailkonten, prüft die Anmeldung,
kopiert Nachrichten als unveränderte `.eml`-Dateien in das Dokumentarchiv und
verwaltet Sieve-Skripte mit lokaler Git-Versionierung und versendet Nachrichten
oder iTIP-Kalendereinladungen über authentifiziertes SMTP Submission. Der Archivclient verändert
den Mailserver nicht: Er verwendet `EXAMINE`, UID-Suche und `BODY.PEEK[]`, aber
niemals `STORE`, `COPY`, `MOVE`, `DELETE` oder `EXPUNGE`.

Der Login-Test nennt ausdrücklich das verwendete Verfahren **IMAP LOGIN** oder
**SASL PLAIN über TLS**, die vor der Anmeldung angekündigten `AUTH=`-Mechanismen und
`LOGINDISABLED`. Bei typischen Anbieterfehlern weist er auf IMAP-Freischaltung,
vollständigen Benutzernamen, App-Passwort und angekündigtes OAuth hin. OAuth2
selbst ist noch nicht implementiert. Diagnose und Audit enthalten weder das
Passwort noch ungekürzte, potenziell sensible Serverantworten.
Im Modus **Automatisch** wird LOGIN verwendet; meldet der Server jedoch
`LOGINDISABLED` und `AUTH=PLAIN`, wechselt der Client sicher zu SASL PLAIN.
Beide Verfahren sind nur innerhalb einer zertifikatsgeprüften TLS-Verbindung
zulässig und können in der Kontoeinstellung ausdrücklich festgelegt werden.

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
  kein Mailversandprotokoll. Der Versand erfolgt daher getrennt über SMTP Submission.

### SMTP Submission und iTIP

- [RFC 6409 Abschnitt 3.1](https://www.rfc-editor.org/rfc/rfc6409.html#section-3.1)
  reserviert Port 587 für Message Submission; [Abschnitt 4.3](https://www.rfc-editor.org/rfc/rfc6409.html#section-4.3)
  verlangt standardmäßig Authentifizierung. SimpleOffice verwendet SMTP AUTH nur
  nach erfolgreichem TLS-Handshake. Port 25 und unverschlüsseltes SMTP sind nicht
  implementiert.
- [RFC 8314 Abschnitt 3](https://www.rfc-editor.org/rfc/rfc8314.html#section-3)
  empfiehlt TLS für Mail-Zugriff und Submission. Unterstützt werden implizites TLS
  auf dem üblichen Port 465 und STARTTLS auf 587, jeweils mit System-CA- und
  Hostnamenprüfung. Fehlt bei STARTTLS die Serverfähigkeit, wird abgebrochen.
- [RFC 6409 Abschnitt 5.1](https://www.rfc-editor.org/rfc/rfc6409.html#section-5.1)
  empfiehlt gültige Adresssyntax. Absender und ein bis 100 Empfänger werden vor
  dem Netzwerkzugriff geprüft; Zeilenumbrüche, doppelte Empfänger und unvollständige
  Domains werden abgewiesen.
- Nachrichten erhalten `Date` und `Message-ID` gemäß
  [RFC 5322 Abschnitt 3.6](https://www.rfc-editor.org/rfc/rfc5322.html#section-3.6).
  Die fertigen SMTP-Bytes werden **vor** Verbindungsaufbau unverändert archiviert.
- Kalendereinladungen nutzen `text/calendar` und die iTIP-Methoden `REQUEST`,
  `REPLY`, `CANCEL`, `COUNTER`, `DECLINECOUNTER` oder `PUBLISH` aus
  [RFC 5546 Abschnitt 3.2](https://www.rfc-editor.org/rfc/rfc5546.html#section-3.2).
  SimpleOffice transportiert die angegebene VCALENDAR-Datei, erfindet aber keine
  Teilnehmerberechtigungen oder Zustellbestätigungen.

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
   Die Kontenübersicht zeigt ausdrücklich **Passwort gespeichert** oder
   **Passwort erforderlich**. Beim Bearbeiten bleibt die Speicheroption für ein
   vorhandenes Secret markiert. Ein neues Passwort bei deaktivierter Option
   entfernt ein veraltetes gespeichertes Secret.
3. **Login testen** liest Fähigkeiten und Ordneranzahl, verändert aber keine Mail.
4. **Jetzt kopieren** startet einen begrenzten, inkrementellen Archivlauf.
5. Anhänge werden nur bei gesetzter Bestätigung extrahiert. Dann gelten die
   vorhandenen Größenlimits, ClamAV-Prüfung, Quarantäne, Herkunftstags und Audit.
   Ohne bestätigte Extraktion wird nur die originale EML archiviert.
6. Sieve-Skripte können lokal gespeichert, hochgeladen oder hochgeladen und
   aktiviert werden. Jede Speicherung erzeugt eine Git-Auditversion.
7. Für SMTP Server, Port, Transport, Benutzer und Absender konfigurieren. Ein
   separates SMTP-Passwort ist optional; andernfalls wird das IMAP-Passwort
   wiederverwendet. **Nur SMTP-Login testen** versendet nichts.
8. Empfänger, Betreff und Reintext eingeben. **Archivieren und senden** schreibt
   zuerst die EML ins private Archiv. Termineinladungen werden im **Kalender**
   beim bestehenden Termin erzeugt; dort SMTP-Konto und Empfänger wählen.

## Rechte, Sicherheit und Datenschutz

- Konten, Zugangsdaten, Archivzustand und Sieve-Skripte sind pro SimpleOffice-
  Benutzer getrennt. Ein Benutzer kann keine fremde Konto-ID verwenden.
- Geheimnisse erscheinen weder in Templates, Audit-Snapshots noch Logs.
- SMTP- und IMAP-Passwörter können getrennt über verschlüsselte Speicherung oder
  geschützte Umgebungsvariablen bereitgestellt werden. Eine explizite Eingabe gilt
  nur für die jeweilige Aktion.
- Das verschlüsselte Passwort ist an den dauerhaften Installationsschlüssel
  gebunden. Geht dieser verloren, muss das Mailpasswort neu eingetragen werden.
- Die Archivdateien liegen unter `email/<Benutzer-Hash>/<Konto>/<Jahr>/<SHA-512>.eml` und werden
  als normale Dokumente mit Ordnerrechten, Metadaten und Audit behandelt.
- Sieve verändert den künftigen Zustellpfad auf dem Mailserver. Aktivierung ist
  deshalb nie Bestandteil des bloßen Speicherns.
- Ausgehende EML liegen unter
  `email/<Benutzer-Hash>/<Konto>/sent/<Jahr>/<SHA-512>.eml`. Der Zustand
  `pending`, `sent` oder `failed`, Empfänger, Absender und Message-ID stehen in
  getrennten Dokumentmetadaten; die EML selbst bleibt unverändert.

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
- **Persistente Wiederaufnahme (#587)**: Jede UID wird vor der Verarbeitung
  im vorhandenen benutzer-/kontogebundenen Archivzustand als offen gespeichert.
  Der Fortschritt kann höhere UIDs erreichen, während frühere Fehler separat
  offen bleiben. „Jetzt kopieren“ verarbeitet offene UIDs zuerst; der Abschluss
  wird erst nach Originalspeicherung, Herkunftsmetadaten und einer ausdrücklich
  bestätigten Anhangsübernahme gespeichert. Ein nicht schreibbarer Checkpoint
  bricht den Lauf ab, statt ohne belastbaren Fortschritt weiterzuarbeiten.
- Die Mailseite nennt offene UIDs und die ausstehende Stufe: Abruf,
  EML-Speicherung, Herkunftsmetadaten oder Anhangsübernahme. Fehlerantworten und
  Lauf-Audit enthalten keine Serverantworten, Mailtexte oder Passwörter.
- Originale werden über den vorhandenen StoragePort bytegenau verifiziert;
  identische EML werden auch bei einem Neustart, anderem Jahr oder geändertem
  UID-Namensraum wiederverwendet. Im V2-Modus zählt die autoritative Quelle,
  auch wenn die Legacy-Projektion fehlt. Andere Bytes am Ziel werden niemals
  überschrieben oder als erfolgreicher Wiederanlauf akzeptiert.
- Bestätigte Anhangsübernahme bleibt für diese offene UID nach einem Neustart
  aktiv, auch wenn die Checkbox beim nächsten Lauf nicht erneut gesetzt wird.
  Bereits abgeschlossene MIME-Teile werden nicht erneut importiert. Für noch
  offene Teile verwendet dieselbe ClamAV-/Quarantäne-/Herkunftslogik feste,
  private Ziele im Jahrgangsordner unter `attachments/<stabile Identität>-<Name>`;
  der vorhandene Dateinamenvalidator begrenzt den ASCII-Zielnamen. Dadurch entsteht
  auch bei einem Abbruch zwischen Import und Checkpoint kein zweites Dokument.
  Infizierte Teile bleiben in Quarantäne. Neue Nachrichten ohne Bestätigung
  werden weiterhin ausschließlich als originale EML archiviert.
- Vorhandene alte Checkpoints werden ohne Datenlöschung übernommen. Weil sie
  historische Lücken enthalten können, erfolgt einmalig ein erneuter Abgleich
  ab der ersten UID, ebenfalls mit dem eingestellten Batch-Limit. Alte Zustände
  belegen keine frühere Anhangsbestätigung; für deren erneute Übernahme muss
  die Checkbox ausdrücklich gesetzt werden.
- Ein Wechsel von `UIDVALIDITY`, Server, Anmeldung oder Quellordner beginnt
  einen neuen Abgleich. Alte offene UIDs werden nicht auf andere Nachrichten
  angewendet: Sie bleiben als frühere Wiederholungen im Archivzustand erhalten
  und erzeugen einen sichtbaren Hinweis. Existieren ihre Nachrichten im neuen
  Namensraum, finalisiert der neue Abgleich die vorhandenen EML und Anhänge.
  Andernfalls Quellordner und vorhandene Originale prüfen; der Hinweis ist
  keine erfolgreiche Nacharchivierung verschwundener Quellnachrichten.
- Die Anwendung verändert weiterhin keine IMAP-Flags und verschiebt oder
  löscht keine Quelle. Ein Lauf verarbeitet insgesamt höchstens das gewählte
  Limit aus offenen und neuen UIDs (maximal 1.000). Eine dauerhaft fehlerhafte
  UID kann bei einem Limit von 1 weitere Nachrichten zurückhalten; Limit
  erhöhen oder die angezeigte Ursache beheben.
- Fehlender oder fehlerhafter ClamAV verhindert die Anhangsübernahme, nicht aber
  die zuvor gespeicherte originale EML.
- Kann die ausgehende EML nicht atomar archiviert und registriert werden, findet
  **kein SMTP-Netzwerkzugriff** statt. Schlägt der Transport fehl, bleibt die EML
  mit Zustand `failed` und gekürzter Fehlerangabe erhalten. Nach erfolgreicher
  Serverannahme wird `sent` auditiert. Eine SMTP-Annahme ist keine Garantie für
  die spätere Zustellung beim Empfänger.
- Das Senden ist synchron und auf 30 Sekunden je Netzwerkoperation begrenzt. Es
  gibt keine automatische Wiederholung, um Doppelzustellungen zu vermeiden.
- Bei unklarem SMTP-Ausgang, teilweiser Empfängerannahme oder fehlgeschlagener
  Archivfinalisierung nach Serverannahme zeigen beide direkten Versandwege
  eine ausdrückliche Warnung vor erneutem Versand. Diese Zustände werden nicht
  als eindeutig fehlgeschlagener Versand dargestellt; Archiv und Serverstatus
  müssen vor einer weiteren Entscheidung geprüft werden.

## Migration, Rückwärtskompatibilität und Deaktivierung

### Administrative Freigabe von Passwortvariablen

Benutzer dürfen keine beliebigen Prozess-Secrets als Mailpasswort auswählen.
`Passwort-Env` und `SMTP-Passwort-Env` bleiben als Kontoeinstellungen erhalten,
werden aber nur mit einer passenden Betreiberfreigabe aufgelöst. Ohne Freigabe
wird vor dem Netzwerkzugriff abgebrochen; manuelle und verschlüsselt gespeicherte
Passwörter funktionieren unverändert.

Der Betreiber setzt `SIMPLEOFFICE_MAIL_ENV_CREDENTIAL_BINDINGS` auf eine
JSON-Liste mit ausschließlich nicht geheimen Bindungsdaten, zum Beispiel:

```json
[{"owner":"alice","account_id":"work","protocol":"imap","env":"MAIL_PASSWORD_ALICE_WORK","host":"imap.example.test","port":993,"security":"tls","username":"alice@example.test"}]
```

Die eigentliche Passwortvariable wird weiterhin separat als Secret bereitgestellt.
Die Freigabe bindet ihren Namen an den exakten Benutzer, die Konto-ID, das
Protokoll, den Host, Port, TLS-Modus und Login-Namen. Änderungen dieser
Kontoeinstellungen benötigen eine neue passende Freigabe. Hostnamen werden
ohne Beachtung der Groß-/Kleinschreibung verglichen.

Für SMTP ist ein separater Eintrag mit `protocol: "smtp"` und den SMTP-
Verbindungsdaten erforderlich, auch bei Wiederverwendung der IMAP-Variable.
ManageSieve benötigt `protocol: "sieve"`, den Sieve-Host/-Port, den Modus
`starttls` und den IMAP-Login-Namen. Eine IMAP-Freigabe erlaubt keine Weitergabe
an einen anderen Dienst. Entfernte oder ungültige Bindungen sperren die
Auflösung sofort. Die Liste ist auf 64 KiB und 1.000 Einträge begrenzt.

Bestehende Konten und Variablennamen werden nicht gelöscht oder umgeschrieben.
Bisher ungebundene Passwortvariablen benötigen nach diesem Sicherheitsupdate
eine Betreiberfreigabe. Eine automatische Freigabe alter Kontoeinstellungen
würde die Sicherheitslücke beibehalten und findet deshalb nicht statt.

Es gibt keine Datenbankmigration. Ohne gespeicherte Konten ist der neue Reiter
wirkungslos. Zum Deaktivieren keine Archivläufe starten; vorhandene EML bleiben
normale Dokumente. Gespeicherte Konfiguration liegt unter
`.simpleoffice-meta/mail/`. Ein Administrator kann diesen Bereich nach Sicherung
entfernen; Mailserverdaten werden dadurch nicht verändert.

## Tests und bekannte Grenzen

Automatisiert geprüft werden Verschlüsselung, Benutzertrennung, Skriptversionen,
Navigation, bytegleiche EML-Ablage, UIDVALIDITY-Herkunft, das Fehlen sämtlicher
mutierender IMAP-Kommandos, Archiv-vor-Versand, Fehlerarchiv, Header-Injection,
Empfängerduplikate und MIME-iTIP. Echte Server unterscheiden sich bei SASL-
Mechanismen: SMTP nutzt die von Python ausgehandelte AUTH-Methode; ManageSieve
unterstützt derzeit nur `PLAIN` innerhalb TLS. OAuth2, SCRAM für ManageSieve,
`CHECKSCRIPT`, serverseitiger Skriptdownload, DSN-Auswertung, Versandwarteschlange,
automatische Wiederholung und Hintergrundplanung sind bewusst nicht implementiert.

### Verifizierte lokale Archivzugriffe

Archivliste und -suche, EML-Vorschau, MIME-Anhangdownload und die
Duplikatprüfung beim manuellen IMAP-Archivieren lesen über den bestehenden
StoragePort. V2 verwendet den ObjectCatalog für den Archiv-Namespace; fehlende
oder veraltete Inhaltsdateien und fehlende Scan-Indexzeilen sind dort keine
Ersatzquelle. V1/Shadow behalten den bestehenden Dateinamensraum, einschließlich
vorhandener noch nicht indizierter EML-Dateien.

Vor MIME-Verarbeitung wird das gesamte Objekt verifiziert. Namespace,
SHA-256, Länge und bei den üblichen SHA-512-Dateinamen die Nachrichtenidentität
müssen stimmen. Maximal 100 MiB plus ein Prüfbyte werden gepuffert;
zu große oder beschädigte Nachrichten werden nicht geparst. Nicht verfügbare
Einträge werden mit einer technischen Diagnose ohne Nachrichtendaten aus der
Archivsuche ausgelassen; direktes Öffnen zeigt eine sichere Fehlermeldung.
Ein intakter Legacy-Inhalt wird bei einem V2-Fehler nicht als Fallback verwendet.

Fallteilnehmer dürfen weiterhin ausschließlich die explizit verknüpfte
Nachricht eines lesbaren Falls über dessen Vorschau-/Anhangroute öffnen.
Kontoinhaber, Konto und SHA-512-Referenz werden zusätzlich am zentralen
Dokumentzugriff geprüft; Widerruf und Entfernung der Zuordnung bleiben wirksam.
Generische Dokumentrouten und öffentliche Freigaben erteilen dadurch keine
Archivberechtigung. MIME-Anhangdownloads benötigen weiterhin einen sauberen
ClamAV-Befund und werden wie bisher protokolliert.

Dies ist eine Teilumsetzung von Issue #471. Metadaten, Ordner-/Access-Policies,
andere Archivierungswege und das globale Cleanup-/Rollback-Gate bleiben
Kompatibilitätsabhängigkeiten; es werden keine Legacy-Daten gelöscht.
