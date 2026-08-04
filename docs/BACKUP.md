# Backups

SimpleOffice4Me kann einen portablen, komprimierten Sicherungsstand des
Dokumentordners erstellen. Das Archiv enthält Originaldateien, Sidecar-Daten,
Konfiguration, Suchindex und Revisionshistorie sowie ein SHA-256-Manifest.

```bash
python tools/backup.py \
  /srv/simpleoffice/documents \
  /mnt/backup/simpleoffice-2026-07-26.tar.gz
```

Die Zieldatei muss außerhalb des Dokumentordners liegen und darf noch nicht
existieren. Zuerst entsteht eine `.partial`-Datei; erst nach erfolgreichem
Abschluss wird sie atomar auf den endgültigen Namen verschoben. Verändert sich
eine Quelldatei während der Sicherung, bricht der Lauf ab.

Die `.partial`-Datei wird exklusiv angelegt, sodass ein bereits vorhandener
Pfad oder symbolischer Link nicht überschrieben wird. Unter Linux und macOS
erhalten temporäres und fertiges Archiv unabhängig von einem offenen `umask`
die Rechte `0600`: nur der anlegende Benutzer darf lesen und schreiben. Unter
Windows gelten die Zugriffslisten des Zielordners; das Werkzeug lockert sie
nicht. Für ein gemeinsam genutztes Backup-Ziel sollten dessen NTFS-Rechte
deshalb vorab ausdrücklich geprüft werden.

Standardmäßig werden symbolische Links nicht verfolgt und Dateisystemgrenzen
nicht überschritten. Ein bewusst eingebundener weiterer Datenträger kann
explizit aufgenommen werden:

```bash
python tools/backup.py \
  --allow-other-filesystems \
  /srv/simpleoffice/documents \
  /mnt/backup/simpleoffice-mit-mounts.tar.gz
```

## Sicherung prüfen

```bash
python tools/backup.py \
  --verify-only \
  /mnt/backup/simpleoffice-2026-07-26.tar.gz
```

Die Prüfung liest alle gesicherten Dateien aus dem Archiv und vergleicht
Größe und SHA-256 mit dem eingebetteten Manifest. Sie extrahiert keine Datei.

Zusätzlich wird die vollständige Archivstruktur geprüft. Doppelte Dateinamen,
nicht im Manifest aufgeführte Dateien, Pfade außerhalb von `SimpleOffice4Me/`,
Symlinks, Hardlinks und Spezialdateien führen zu einem sichtbaren Fehler. Das
verhindert, dass eine manipulierte oder mehrdeutige TAR-Struktur trotz korrekter
Prüfsummen als gültige Sicherung gemeldet wird. Das Manifest ist auf 64 MiB
begrenzt, damit ein fremdes Archiv keine übergroßen eingebetteten JSON-Metadaten
in den Arbeitsspeicher laden lässt. Nicht portabel darstellbare Pfade führen
bereits beim Erstellen zu einem Fehler, statt eine unprüfbare Sicherung zu
erzeugen.

## Betriebsgrenzen

- Das Archiv ist nicht verschlüsselt. Für personenbezogene Dokumente sollte
  das Zielmedium verschlüsselt sein, etwa mit LUKS, BitLocker oder FileVault.
- Eine Sicherung auf demselben Rechner schützt nicht ausreichend vor Defekt,
  Diebstahl oder Ransomware. Mindestens eine regelmäßig geprüfte Kopie sollte
  getrennt bzw. offline aufbewahrt werden.
- Eine automatische Wiederherstellung ist absichtlich noch nicht enthalten.
  Vor einem Restore müssen Pfade, Berechtigungen und die Archivintegrität
  geprüft werden.
