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

## Betriebsgrenzen

- Das Archiv ist nicht verschlüsselt. Für personenbezogene Dokumente sollte
  das Zielmedium verschlüsselt sein, etwa mit LUKS, BitLocker oder FileVault.
- Eine Sicherung auf demselben Rechner schützt nicht ausreichend vor Defekt,
  Diebstahl oder Ransomware. Mindestens eine regelmäßig geprüfte Kopie sollte
  getrennt bzw. offline aufbewahrt werden.
- Eine automatische Wiederherstellung ist absichtlich noch nicht enthalten.
  Vor einem Restore müssen Pfade, Berechtigungen und die Archivintegrität
  geprüft werden.
