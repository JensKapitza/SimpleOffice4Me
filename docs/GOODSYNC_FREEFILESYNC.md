# GoodSync und FreeFileSync über SFTP

## Zweck

SimpleOffice kann als SFTP-Ziel für GoodSync und FreeFileSync verwendet werden.
Beide Programme sehen ausschließlich den für den angemeldeten Benutzer
freigegebenen virtuellen Dateibaum. Lesen, Schreiben und Löschen richten sich
zusätzlich nach dem Gültigkeitsbereich des verwendeten SSH-Schlüssels.

GoodSync beschreibt SFTP als unterstütztes Sicherungs- und
Synchronisationsziel einschließlich Anmeldung mit privatem Schlüssel in der
[offiziellen SFTP-Anleitung](https://help.goodsync.com/hc/en-us/articles/115003420372-FTP-FTPS-and-SFTP-Backup-Sync).
FreeFileSync dokumentiert SFTP, parallele Verbindungen und Kanäle in der
[offiziellen SFTP-Anleitung](https://freefilesync.org/manual.php?topic=ftp-setup).

GSTP/GSTPS aus GoodSync Connect wird nicht nachimplementiert: Es ist ein
proprietäres Protokoll und würde ein GoodSync-Konto sowie externe Vermittlungs-
oder Relay-Dienste voraussetzen. Die lokale SFTP-Verbindung benötigt diese
externe Infrastruktur nicht.

## GoodSync einrichten

1. In SimpleOffice unter **Einstellungen → WebDAV, SSHFS und Ordnerrechte**
   einen SSH-Public-Key mit Lesen oder Lesen/Schreiben hinterlegen.
2. In GoodSync einen Auftrag vom Typ **Backup** oder **Synchronize** anlegen.
3. Als Dateisystem **SFTP** wählen.
4. Server, Port `2222`, SimpleOffice-Benutzer und privaten Schlüssel eintragen.
5. Als Home-Ordner `/` oder einen freigegebenen Teilbaum wie `/Projekte` wählen.
6. Vor dem ersten Lauf **Analyze** prüfen; Löschungen nur bewusst aktivieren.

Der GoodSync-Ordner `_gsdata_`, temporäre Zustandsdateien, Umbenennen,
Ersetzen, Zeitstempel und Löschfolgen werden wie normale Benutzerdaten
behandelt. Sie bleiben damit innerhalb der ACLs und werden nicht als interne
SimpleOffice-Metadaten interpretiert.

## FreeFileSync einrichten

1. Über die Schaltfläche **Cloud-Speicher** eine SFTP-Verbindung hinzufügen.
2. Server, Port `2222`, Benutzer und Schlüssel auswählen.
3. Den freigegebenen Remote-Ordner auswählen.
4. Für beidseitige Arbeit **Zwei Wege**, für eine Sicherung **Spiegeln** oder
   **Aktualisieren** auswählen.
5. Zunächst eine Verbindung und ein bis zwei Kanäle verwenden. Bei großen
   Verzeichnisbäumen kann die Kanalzahl vorsichtig erhöht werden; der Server
   begrenzt gleichzeitige Verbindungen über `SIMPLEOFFICE_SFTP_MAX_CLIENTS`.

FreeFileSync verwendet unter anderem `.ffs_tmp` und `sync.ffs_db`. Der Server
unterstützt die dafür benötigten Abläufe aus Schreiben, Zeitstempel setzen,
Umbenennen und Löschen. SFTP liefert keine stabilen plattformübergreifenden
Datei-IDs; FreeFileSync fällt deshalb bei erkannten Verschiebungen entsprechend
seiner Dokumentation auf Kopieren und Löschen zurück.

## Sicherheit und Konflikte

- Public Keys können einzeln ablaufen oder widerrufen werden.
- Ein Nur-Lesen-Schlüssel kann weder Synchronisationsdaten noch Nutzdateien
  anlegen, verändern oder löschen.
- Jede neue oder geänderte Datei wird atomar über die VFS-Schicht gespeichert.
- Parallele Schreibzugriffe mit veraltetem SHA-256-Ausgangsstand werden
  abgewiesen, statt neuere Daten unbemerkt zu überschreiben.
- Ersetzen und Löschen erzeugen Wiederherstellungsstände und Audit-Ereignisse.
- Bei aktiviertem `WEBDAV_UPLOAD_SCAN` werden auch SFTP-Uploads mit ClamAV
  geprüft.
- Symlinks, Spezialdateien, Besitzer- und Modusänderungen werden nicht
  übernommen. Portable Zugriffs- und Änderungszeiten werden unterstützt und
  auditiert.

## Fehler- und Ausfallverhalten

Ein abgebrochener Upload wird nicht übernommen, solange der SFTP-Dateihandle
nicht erfolgreich geschlossen wurde. Bei einem Versionskonflikt meldet der
Client einen fehlgeschlagenen Schreib- oder Umbenennvorgang; anschließend sollte
erneut analysiert werden. Der getrennte SFTP-Dienst kann neu gestartet werden,
ohne den Webprozess oder gespeicherte Dokumente zu verändern.

## Tests und Grenzen

Automatisierte Tests bilden typische Abläufe beider Clients nach:

- FreeFileSync: temporäre Datei, Inhalt, Zeitstempel und atomare Umbenennung;
- GoodSync: `_gsdata_`, Zustandsdatei und konfliktgeschütztes Ersetzen;
- parallele Handles, Nur-Lesen-Zugriff, ACL-Ausblendung und Wiederherstellung.

Die proprietäre GoodSync-Connect-Schnittstelle GSTP/GSTPS ist nicht enthalten.
GoodSync wird über seinen offiziell unterstützten SFTP-Modus angebunden.

## Deaktivierung

Der SFTP-Dienst kann gestoppt oder der betreffende SSH-Schlüssel widerrufen
werden. Dateien, Versionen, Rechte und WebDAV bleiben dabei unverändert.
