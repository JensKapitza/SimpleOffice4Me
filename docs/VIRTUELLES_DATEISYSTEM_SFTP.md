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

Public-Key beziehungsweise App-Passwort und Ordnerrecht sind getrennte
Schranken. Ein auf `read` begrenzter Geräteschlüssel bleibt auch bei
Ordnerrolle `manage` schreibgeschützt. Ein schreibender Schlüssel erweitert
umgekehrt keine Ordnerrolle. Schlüssel dürfen Benutzer ausschließlich für das
eigene Konto anlegen und widerrufen; höchstens 20 Schlüssel mit 1 bis 365 Tagen
Laufzeit sind möglich.

## SFTP installieren und starten

Die Webanwendung benötigt Paramiko nicht. SFTP wird optional installiert:

```bash
python -m pip install '.[sftp]'
ssh-keygen -t ed25519 -f /etc/simpleoffice/sftp_host_ed25519_key -N ''
chmod 600 /etc/simpleoffice/sftp_host_ed25519_key
```

Für eine lokale Ersteinrichtung ohne systemweiten Service stehen dieselben
plattformübergreifenden Prüfungen bereit:

```bash
./start-sftp.sh status       # nur prüfen
./start-sftp.sh init         # fehlenden Schlüssel einmalig erzeugen
./start-sftp.sh              # getrennten Dienst im Vordergrund starten
```

Unter Windows heißen die Befehle `start-sftp.bat status`, `init` und
`start-sftp.bat`. `init` überschreibt niemals einen vorhandenen Schlüssel.
Eine vorhandene Einrichtung mit `SIMPLEOFFICE_SFTP_HOST_KEY` bleibt maßgeblich.
Für Dauerbetrieb ist weiterhin die mitgelieferte systemd-Beispieldatei unter
`docs/simpleoffice-sftp.service.example` vorgesehen.

Konfiguration über geschützte Umgebung beziehungsweise den Service-Manager:

```bash
export SIMPLEOFFICE_DOCUMENT_ROOT=/srv/simpleoffice/documents
export SIMPLEOFFICE_DOCUMENT_ADMINS=jens
export SIMPLEOFFICE_SFTP_HOST_KEY=/etc/simpleoffice/sftp_host_ed25519_key
export SIMPLEOFFICE_SFTP_BIND=127.0.0.1
export SIMPLEOFFICE_SFTP_PORT=2222
export SIMPLEOFFICE_SFTP_MAX_BYTES=536870912
export SIMPLEOFFICE_SFTP_MAX_CLIENTS=32
export SIMPLEOFFICE_SFTP_PASSWORD_AUTH=false
simpleoffice-sftp
```

Standardmäßig lauscht der Dienst nur auf `127.0.0.1:2222`. Für externen
Zugriff sollte die Freigabe gezielt über Firewall oder VPN erfolgen. Ein
Unter **Einstellungen → WebDAV, SSHFS und Ordnerrechte** wird der Inhalt von
`~/.ssh/id_ed25519.pub` hinterlegt. Der private Schlüssel verlässt das Gerät
nicht. Optional kann `SIMPLEOFFICE_SFTP_PASSWORD_AUTH=true` weiterhin ein
App-Passwort aus den WebDAV-Einstellungen zulassen; das normale Browser-
Passwort wird nie verwendet. Für automatisierte Mounts wird Public Key
empfohlen.

```bash
ssh-keygen -t ed25519 -a 100 -f ~/.ssh/id_ed25519
sftp -i ~/.ssh/id_ed25519 -P 2222 jens@server
mkdir -p ~/SimpleOffice
sshfs -p 2222 -o IdentityFile=~/.ssh/id_ed25519,IdentitiesOnly=yes,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3 jens@server:/ ~/SimpleOffice
```

## rsync über SSH

Der Dienst kann zusätzlich das rsync-Serverprotokoll freigeben. Eine allgemeine
Shell bleibt gesperrt. Aktivierung:

```bash
sudo apt install rsync
export SIMPLEOFFICE_RSYNC_ENABLED=true
export SIMPLEOFFICE_RSYNC_MAX_BYTES=2147483648
export SIMPLEOFFICE_RSYNC_MAX_FILES=100000
export SIMPLEOFFICE_RSYNC_TIMEOUT=3600
```

Beispiele für Download und Upload eines freigegebenen Ordners:

```bash
rsync -a --delete -e 'ssh -p 2222 -i ~/.ssh/id_ed25519' jens@server:/Projekte/ ./Projekte/
rsync -a --delete -e 'ssh -p 2222 -i ~/.ssh/id_ed25519' ./Projekte/ jens@server:/Projekte/
```

Der native `rsync`-Prozess arbeitet ausschließlich in einem temporären Abbild.
Er erhält weder den Dokumentenstamm noch interne Metadaten. Nach erfolgreichem
Protokollabschluss werden Änderungen einzeln über die virtuelle Dateisystem-
Schicht übernommen. Dadurch gelten dieselben geerbten Benutzerrechte wie bei
WebDAV und SFTP; Überschreiben und Löschen bleiben konfliktgeschützt,
versioniert, wiederherstellbar und auditiert. Bei aktiviertem ClamAV-Uploadscan
werden neue und geänderte Dateien vor der Übernahme geprüft.

Nur von üblichen rsync-Clients erzeugte `rsync --server`-Aufrufe werden
akzeptiert. Shell-Kommandos, externe Programme, beliebige Optionen, Links,
Geräte- und Spezialdateien werden abgewiesen. Lese-Schlüssel dürfen nur vom
Server herunterladen; Upload, Änderung und `--delete` erfordern Schreibrechte.

## Primäre Standards und abgeleitete Anforderungen

### SSH-Transport

[RFC 4253 §4.1](https://www.rfc-editor.org/rfc/rfc4253.html#section-4.1)
verlangt SSH-2-Unterstützung. Paramiko stellt verschlüsselten Transport und
Hostschlüssel bereit. Der Server startet ohne privaten Hostschlüssel nicht und
lehnt unter POSIX zu weit gefasste Dateirechte ab. Unsichere SHA-1-KEX sowie
verkürzte MD5-/SHA-1-MACs sind deaktiviert. Banner, Authentifizierung und
Kanaleröffnung besitzen feste Zeitlimits; die Zahl gleichzeitiger Clients ist
begrenzt. Der private Hostschlüssel verbleibt im Service-Manager und niemals im
Repository.

### Authentifizierung

[RFC 4252 §4](https://www.rfc-editor.org/rfc/rfc4252.html#section-4) beschreibt
das Authentifizierungsframework; `none` wird nicht als erfolgreiche Methode
angeboten und nach fünf Fehlversuchen wird keine Anmeldung mehr akzeptiert.
[RFC 4252 §7](https://www.rfc-editor.org/rfc/rfc4252.html#section-7) verlangt
die Methode `publickey`: SimpleOffice prüft Algorithmus und binären
Schlüsselinhalt, Benutzerzuordnung, Ablauf und Rechteumfang. Hinterlegt werden
nur Public-Key-Blob, SHA-256-Fingerprint, Bezeichnung und Ablauf; Kommentare
und private Schlüssel werden nicht gespeichert. Unterstützt werden Ed25519,
RSA mit modernen RSA-SHA2-Signaturen, ECDSA P-256/384/521 und
Security-Key-Ed25519. Hinzufügen, erfolgreiche Anmeldung und Widerruf werden
auditiert.

Bei aktivierter Kompatibilitätsoption gilt zusätzlich
[RFC 4252 §8](https://www.rfc-editor.org/rfc/rfc4252.html#section-8): Das
Passwort liegt im verschlüsselten SSH-Transport. Serverseitig bleibt nur der
vorhandene Salt-/`scrypt`-Hash gespeichert.

### SFTP-Dateioperationen

SFTP v3 ist kein veröffentlichter IETF-RFC. Maßgeblich für die verbreitete
Clientpraxis ist der historische
[IETF-Entwurf `draft-ietf-secsh-filexfer-03`](https://datatracker.ietf.org/doc/html/draft-ietf-secsh-filexfer-03).
Die implementierte Teilmenge umfasst `REALPATH`, `STAT`, `LSTAT`,
`OPENDIR`/`READDIR`, `OPEN`/`READ`/`WRITE`/`CLOSE`, `MKDIR`, `REMOVE`, `RMDIR`
und `RENAME`. Die Größenänderung über `SETSTAT` wird versioniert umgesetzt,
damit `truncate(2)` über SSHFS funktioniert; reine Zeitstempeländerungen sind
ein Kompatibilitäts-No-op. Symlinks, Eigentümer- und Unix-Modusänderungen
werden mit `SSH_FX_OP_UNSUPPORTED` abgewiesen. Statuswerte folgen
[§7](https://datatracker.ietf.org/doc/html/draft-ietf-secsh-filexfer-03#section-7).

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
- Bei aktivem `WEBDAV_UPLOAD_SCAN` werden auch SFTP-Schreibpuffer vor der
  sichtbaren, versionierten Veröffentlichung in der privaten Quarantäne mit
  ClamAV geprüft. Fund oder Scannerfehler veröffentlichen keine Datei.
- Rechte- und Dateiänderungen landen in Audit- und Versionshistorie.
  Dateiinhalte werden nicht in ACL-Ereignisse geschrieben.
- Die Verwendung eines App-Passworts wird auch bei SFTP mit dem letzten
  Nutzungszeitpunkt protokolliert.
- Public-Key-Fingerprints sind sichtbar und auditierbar; Schlüsselmaterial
  wird niemals in Auditereignisse kopiert.
- Aufbewahrungssperren, WebDAV-Locks, ETags, Größenlimits und Benutzertrennung
  bleiben zusätzliche Prüfungen.
- Hostschlüssel und App-Passwörter gehören nicht ins Repository.

## Fehler- und Ausfallverhalten

- Fehlende SFTP-Zusatzabhängigkeit: Startabbruch mit Installationshinweis.
- Fehlender, verlinkter oder zu offen lesbarer Hostschlüssel: Startabbruch.
- Fehlendes Recht: `SSH_FX_PERMISSION_DENIED` beziehungsweise WebDAV `403/404`.
- Sechs oder mehr fehlgeschlagene Anmeldeversuche: Anmeldung bleibt abgewiesen.
- Erreichtes Clientlimit: neue TCP-Verbindung wird ohne Worker abgewiesen.
- Parallele Änderung seit dem Öffnen: Speichern schlägt fehl; die neuere
  Version bleibt erhalten.
- Symlink-/Modusoperation: `SSH_FX_OP_UNSUPPORTED`.
- Ein SFTP-Ausfall beeinflusst den Webprozess nicht, da beide getrennt laufen.

## Tests

Automatisiert geprüft werden Legacy-Kompatibilität, Vererbung und Abschaltung
der Vererbung, Ausblendung nicht lesbarer Ordner, Schreibschutz, `manage`-
Prüfung, Traversal/Symlink/Metadaten-Schutz, Auditierung, WebDAV-PROPFIND-
Filterung, direkte 404-Antworten und kombinierte Geräte-/Ordnerrechte. Hinzu
kommen Public-Key-Parsing, Algorithmusbindung, Fingerprint, Duplikat- und
Fremdbenutzerschutz, Ablauf/Widerruf, RSA-Public-Key-Login, ausdrückliche
Shell-/Exec-/Forwarding-Ablehnung sowie versioniertes Kürzen und Erweitern.

## Bekannte Grenzen und Deaktivierung

- rsync muss auf Client und Server installiert sein. Die optionale Brücke wird
  mit `SIMPLEOFFICE_RSYNC_ENABLED=false` vollständig deaktiviert.
- rsync arbeitet aus Sicherheitsgründen über ein temporäres Abbild. Dafür muss
  temporär Speicher bis zur Größe des freigegebenen Teilbaums verfügbar sein.
- SFTP-Locks sind optimistische SHA-256-Prüfungen, keine WebDAV-Locks.
- POSIX-Eigentümer, Gruppen, Hardlinks und Symlinks werden nicht abgebildet.
- Die Benutzerkeys sind bewusst anwendungsverwaltet und keine systemweite
  OpenSSH-`authorized_keys`; damit können sie keine Server-Shell freischalten.
- SFTP v3 ist ein verbreiteter IETF-Entwurf, aber kein Standards-Track-RFC.
- Zum Deaktivieren wird nur der separate SFTP-Service gestoppt. WebDAV und
  Dateien bleiben unverändert. Der Widerruf aller App-Passwörter deaktiviert
  Passwortzugriff; Public Keys werden einzeln in der Oberfläche widerrufen.
  ACL- und Auditdaten bleiben erhalten.
