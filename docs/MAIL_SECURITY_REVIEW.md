# Mail-Sicherheitsprüfung vom 04.10.2026

Ausgangsstand: `main` `05d96a17bb12e73629d68e7d0ef4b7626400fc30`.
Geprüft wurden Mailkonten, IMAP-/SMTP-/ManageSieve-Zugänge, Archiv- und
Entwurfsdateien, Dokumentzugriff, Mail-Vorgänge, Delegation, Anhangsgates,
Nachrichtendarstellung und Versandzustände. Verbindliche Fachreferenzen sind
`IMAP_SIEVE_EMAIL_ARCHIV.md` und `MAIL_VORGAENGE.md`.

## Bestätigte und korrigierte Befunde

| Befund | Auswirkung | Korrektur |
|---|---|---|
| Private Maildateien waren über allgemeine Dokumentrouten sichtbar | Fremde EMLs und Entwurfsanhänge konnten trotz privater Ordnerrechte gelesen werden; auch Listen und Suche waren betroffen | Mail-Kontextprüfung im bestehenden DocumentStore; ownergebundene Archive, Case-ACL für Vorgangsrouten, gesperrte öffentliche Links |
| Passwort-Env war eine frei wählbare Prozessvariable | Ein Kontoinhaber konnte andere serverseitige Secrets als Authentifizierungsmaterial für einen selbst gewählten Mailserver verwenden | Betreiberfreigabe, gebunden an Benutzer, Konto, Protokoll, Host, Port, TLS-Modus und Login; getrennte ManageSieve-/SMTP-Freigaben |
| Direkte Versandrouten meldeten unklare SMTP-Ergebnisse als einfachen Fehler | Erneuter manueller Versand konnte Nachrichten doppelt zustellen | Gemeinsame Warnmeldungen für unbekannten, partiellen und angenommenen, aber nicht finalisierten Ausgang; kein automatischer Retry |

Die Datenschutzbefunde wurden mit synthetischen Konten und Dateien lokal
reproduziert. Es wurden keine produktiven Mailkonten, Prozess-Secrets oder
externen Empfänger verwendet. Die Korrekturen verändern weder EML-Bytes noch
bestehende Konto-IDs, Entwürfe oder Delegationsdatensätze.

## Bereits vorhandene Schutzmaßnahmen

- Kontozugriff prüft den Besitzer serverseitig; öffentliche Kontolisten enthalten
  keine entschlüsselten Passwörter.
- IMAP und SMTP verwenden TLS mit System-CA- und Hostnamenprüfung; AUTH erfolgt
  nach TLS. ManageSieve verwendet STARTTLS.
- Der Archivclient verwendet read-only-Auswahl und `BODY.PEEK[]`.
- Mailtexte werden als Text aufbereitet und durch die Templates escaped;
  Mail-HTML wird nicht als aktive Anwendungsvorschau eingebettet.
- Anhangsdownloads und Entwurfsuploads verwenden den vorhandenen ClamAV-Gate.
  Entwurfsanhänge prüfen außerdem Herkunft, Scan-ID, Hash und Größe.
- Versandfreigaben frieren Entwürfe ein; `sending` wird atomar gesetzt.
  Delegationen sind kontogebunden, widerrufbar und zeitlich begrenzbar.
- Ausgehende EMLs werden vor SMTP archiviert; BCC bleibt außerhalb der EML-Header.

## Prüfnachweise und Grenzen

### Historische Befunde aus der Dokumentationsprüfung vom 2026-10-05

Die damalige Prüfung reproduzierte zwei Fehler. Beide haben inzwischen
Implementierungen und Regressionstests im Repository; die folgenden Befunde
sind daher kein aktueller Statusbericht:

- [#587: IMAP-Checkpoint überspringt fehlgeschlagene UIDs](https://github.com/JensKapitza/SimpleOffice4Me/issues/587):
  `ArchiveRecovery` speichert UIDs vor der Verarbeitung als offen, bindet sie
  an UIDVALIDITY und Quellkonto und versucht unvollständige Speicher-, Metadaten-
  und Anhangsstufen erneut. `tests/test_imap_archive_recovery.py` prüft unter
  anderem den früheren Fehlerfall UID 7/UID 8, Neustarts und Prozessabbrüche.
- [#588: MIME-Suchindex und Vorschau unterscheiden sich](https://github.com/JensKapitza/SimpleOffice4Me/issues/588):
  Die MIME-Suche und ihre Legacy-/Shadow-/V2-Nachprüfung wurden mit PRs #597 und
  #599 umgesetzt.

Diese Repository-Nachweise belegen keine externe Mailserver-, Geräte- oder
Produktionsabnahme. Der Status der verknüpften GitHub-Issues wird hier nicht
behauptet. Die [Mail-Anleitung](IMAP_SIEVE_EMAIL_ARCHIV.md) und
[Retrieval-Anleitung](DOKUMENTSUCHE_RETRIEVAL.md) beschreiben die aktuelle
Funktion und ihre verbleibenden Grenzen.

Die Regressionen umfassen fremde Benutzer und Administratoren, Listen/Suche,
allgemeine Lese- und Änderungsrouten, neue und bestehende Freigabelinks,
berechtigte und widerrufene Case-Zugriffe, delegierten Versand sowie
V2-Archive ohne Legacy-Datei. Passworttests prüfen Zieländerungen,
Protokolltrennung, fehlende/widerrufene Bindungen und bestehende Passwortarten.

Die Prüfung ist eine Code- und Regressionstestprüfung mit synthetischen Daten.
Ein echter IMAP-/SMTP-/ManageSieve-Anbieter, reale Zustellung, dessen Serverlogs
und ein produktiver ClamAV-Dienst wurden damit nicht abgenommen. Sie ersetzt
keinen vollständigen Penetrationstest aller Mail- und Federation-Pfade.
