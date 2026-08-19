# Virtuelles Dateisystem, Ordnerrechte und SFTP

## Zweck und Nutzen

WebDAV und der optionale SFTP-Dienst verwenden dieselbe
`VirtualFileSystem`-Schicht. Sie normalisiert Pfade, blendet interne Metadaten
aus und prüft bei jeder Operation die effektiven Ordnerrechte. Dadurch sehen
LibreOffice, FreeFileSync, Nautilus, Explorer, Finder, `sftp` und `sshfs`
denselben freigegebenen Dateibaum, ohne direkten Zugriff auf Metadaten,
Versionsspeicher oder den physischen Serverpfad.

SFTP ist bewusst **kein SSH-Shellzugang**. Der Dienst akzeptiert nur einen
Session-Kanal mit SFTP-Subsystem. Befehlsausführung, Portweiterleitung,
Symlinks, `chmod` und Spezialdateien werden nicht angeboten.

## Rechte- und Vererbungsmodell

Unter **Einstellungen → WebDAV für Desktop-Programme → Ordnerrechte** kann pro
Ordner für jeden bekannten Benutzer eine Rolle gewählt werden:

- **Kein Zugriff**: Ordner und Inhalt werden nicht aufgelistet; ein direkter
  Abruf antwortet wie eine nicht vorhandene Ressource.
- **Nur lesen**: Auflisten, Metadaten lesen und Inhalte herunterladen.
- **Lesen und schreiben**: zusätzlich anlegen, speichern, umbenennen,
  verschieben und löschen, soweit Locks und Aufbewahrungsregeln es erlauben.
- **Verwalten**: Schreibrecht plus Änderung der Ordnerrechte.

Regeln liegen in der bestehenden `.simpleoffice-folder.json`. Ein Ordner kann
Rechte des Elternordners übernehmen und eigene Einträge überschreiben oder die
Vererbung abschalten. Administratoren werden kommagetrennt über
`SIMPLEOFFICE_DOCUMENT_ADMINS` konfiguriert. Bei genau einem Benutzer darf
dieser die erste Regel selbst setzen; bei mehreren Benutzern ist aus
Sicherheitsgründen ein expliziter Administrator erforderlich.

Historische Installationen bleiben kompatibel: Solange in keinem
übergeordneten Ordner `access_enabled` gesetzt wurde, gilt der bisherige
Schreibzugriff. Die erste gespeicherte Regel aktiviert die ACL für ihren
Teilbaum. Es erfolgt keine automatische Rechteverschärfung oder -lockerung.

App-Passwort und Ordnerrecht sind getrennte Schranken. Ein auf `read`
begrenztes Gerätepasswort bleibt auch bei Ordnerrolle `manage` schreibgeschützt.
Ein schreibendes Gerätepasswort erweitert umgekehrt keine Ordnerrolle.

## SFTP installieren und starten

Die Webanwendung benötigt Paramiko nicht. SFTP wird optional installiert:

```bash
python -m pip install '.[sftp]'
ssh-keygen -t ed25519 -f /etc/simpleoffice/sftp_host_ed25519_key -N ''
chmod 600 /etc/simpleoffice/sftp_host_ed25519_key
```

Konfiguration über geschützte Umgebung beziehungsweise den Service-Manager:

```bash
export SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents
export SIMPLEOFFICE_DOCUMENT_ADMINS=jens
export SIMPLEOFFICE_SFTP_HOST_KEY=/etc/simpleoffice/sftp_host_ed25519_key
export SIMPLEOFFICE_SFTP_BIND=127.0.0.1
export SIMPLEOFFICE_SFTP_PORT=2222
export SIMPLEOFFICE_SFTP_MAX_BYTES=536870912
simpleoffice-sftp
```

Standardmäßig lauscht der Dienst nur auf `127.0.0.1:2222`. Für externen
Zugriff sollte die Freigabe gezielt über Firewall oder VPN erfolgen. Ein
App-Passwort aus den WebDAV-Einstellungen dient auch als SFTP-Passwort; das
normale Browser-Passwort wird nicht verwendet.

```bash
sftp -P 2222 jens@server
sshfs -p 2222 jens@server:/ ~/SimpleOffice -o reconnect
```

Klassisches `rsync` benötigt auf dem SSH-Server ein entferntes
`rsync`-Programm. Der shellfreie Dienst stellt dieses absichtlich nicht bereit;
dafür sind SFTP-fähige Synchronisationsprogramme oder `sshfs` zu verwenden.

## Primäre Standards und abgeleitete Anforderungen

### SSH-Transport

[RFC 4253 §4.1](https://www.rfc-editor.org/rfc/rfc4253.html#section-4.1)
verlangt SSH-2-Unterstützung. Paramiko stellt verschlüsselten Transport und
Hostschlüssel bereit. Der Server startet ohne privaten Hostschlüssel nicht und
lehnt unter POSIX zu weit gefasste Dateirechte ab.

### Authentifizierung

[RFC 4252 §4](https://www.rfc-editor.org/rfc/rfc4252.html#section-4) beschreibt
das Authentifizierungsframework; insbesondere darf `none` nicht als
unterstützte Methode angeboten werden. Der Dienst akzeptiert ausschließlich
gültige, nicht abgelaufene App-Passwörter. Nach
[RFC 4252 §8](https://www.rfc-editor.org/rfc/rfc4252.html#section-8) liegt das
Passwort im verschlüsselten SSH-Transport. Serverseitig bleibt nur der
vorhandene Salt-/`scrypt`-Hash gespeichert.

RFC 4252 bezeichnet `publickey` in §7 als erforderliche Protokollmethode. Die
erste Stufe unterstützt absichtlich nur App-Passwörter. Public-Key-Zuordnung
pro Benutzer ist deshalb eine dokumentierte Konformitätsgrenze.

### SFTP-Dateioperationen

SFTP v3 ist kein veröffentlichter IETF-RFC. Maßgeblich für die verbreitete
Clientpraxis ist der historische
[IETF-Entwurf `draft-ietf-secsh-filexfer-02`](https://datatracker.ietf.org/doc/html/draft-ietf-secsh-filexfer-02).
Die implementierte Teilmenge umfasst `REALPATH`, `STAT`, `LSTAT`,
`OPENDIR`/`READDIR`, `OPEN`/`READ`/`WRITE`/`CLOSE`, `MKDIR`, `REMOVE`, `RMDIR`
und `RENAME`. Symlinks und Unix-Modusänderungen werden mit
`SSH_FX_OP_UNSUPPORTED` abgewiesen.

### WebDAV

Die vorhandene Umsetzung von
[RFC 4918](https://www.rfc-editor.org/rfc/rfc4918.html) bleibt bestehen.
`PROPFIND`, `SEARCH` und Sync-Reports enthalten nur aktuell lesbare Mitglieder.
Schreibmethoden prüfen Quelle, Ziel und Elternordner. Nicht lesbare Pfade
liefern `404`, bekannte nicht schreibbare Ressourcen eine DAV-Antwort mit
`403`.

## Sicherheit und Datenschutz

- Pfade mit `..`, NUL, reservierten Segmenten oder Symlinks werden abgewiesen.
- SFTP erhält niemals den physischen Serverpfad und bietet keine Shell.
- Überschreiben nutzt den beim Öffnen festgestellten SHA-256-Stand. Eine
  zwischenzeitlich geänderte Datei wird nicht unbemerkt ersetzt.
- Rechte- und Dateiänderungen landen in Audit- und Versionshistorie.
  Dateiinhalte werden nicht in ACL-Ereignisse geschrieben.
- Die Verwendung eines App-Passworts wird auch bei SFTP mit dem letzten
  Nutzungszeitpunkt protokolliert.
- Aufbewahrungssperren, WebDAV-Locks, ETags, Größenlimits und Benutzertrennung
  bleiben zusätzliche Prüfungen.
- Hostschlüssel und App-Passwörter gehören nicht ins Repository.

## Fehler- und Ausfallverhalten

- Fehlende SFTP-Zusatzabhängigkeit: Startabbruch mit Installationshinweis.
- Fehlender, verlinkter oder zu offen lesbarer Hostschlüssel: Startabbruch.
- Fehlendes Recht: `SSH_FX_PERMISSION_DENIED` beziehungsweise WebDAV `403/404`.
- Parallele Änderung seit dem Öffnen: Speichern schlägt fehl; die neuere
  Version bleibt erhalten.
- Symlink-/Modusoperation: `SSH_FX_OP_UNSUPPORTED`.
- Ein SFTP-Ausfall beeinflusst den Webprozess nicht, da beide getrennt laufen.

## Tests

Automatisiert geprüft werden Legacy-Kompatibilität, Vererbung und Abschaltung
der Vererbung, Ausblendung nicht lesbarer Ordner, Schreibschutz, `manage`-
Prüfung, Traversal/Symlink/Metadaten-Schutz, Auditierung, WebDAV-PROPFIND-
Filterung, direkte 404-Antworten und kombinierte Geräte-/Ordnerrechte.

## Bekannte Grenzen und Deaktivierung

- Public-Key-Authentifizierung und `authorized_keys` sind noch nicht umgesetzt.
- SFTP unterstützt keine Shell und damit kein serverseitiges `rsync`.
- SFTP-Locks sind optimistische SHA-256-Prüfungen, keine WebDAV-Locks.
- POSIX-Eigentümer, Gruppen, Hardlinks und Symlinks werden nicht abgebildet.
- Zum Deaktivieren wird nur der separate SFTP-Service gestoppt. WebDAV und
  Dateien bleiben unverändert. Der Widerruf aller App-Passwörter deaktiviert
  beide Remote-Protokolle für den Benutzer, ohne ACL- oder Auditdaten zu
  löschen.
