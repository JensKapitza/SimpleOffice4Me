# Von TagSpaces inspirierte Dateiverwaltung

## Recherche und Abgrenzung

Als Produktreferenz wurde am 5. August 2026 das öffentliche Projekt [TagSpaces](https://github.com/tagspaces/tagspaces) und dessen [Benutzerdokumentation](https://docs.tagspaces.org/) untersucht. Es wurde kein Quellcode kopiert oder abgeleitet. SimpleOffice übernimmt ausschließlich allgemeine Produktideen und implementiert sie eigenständig in Python, im vorhandenen Dokumentmodell und mit dessen Audit- und Rechtekonzept.

TagSpaces ist AGPL-3.0 beziehungsweise teilweise kommerziell lizenziert. Gerade deshalb bleibt diese Umsetzung bewusst eine unabhängige Neuimplementierung interoperabler Konzepte und übernimmt weder Komponenten noch Dateiformatdetails, die nicht öffentlich dokumentiert und allgemein beschreibbar sind.

## Was TagSpaces gut löst

- **Offline-first und keine Cloudpflicht:** Die [Projektbeschreibung](https://github.com/tagspaces/tagspaces#tagspaces) stellt lokale Dateien, Plattformunabhängigkeit und fehlenden Vendor-Lock-in in den Mittelpunkt. Das passt zu SimpleOffice und zu Synchronisationswerkzeugen wie FreeFileSync.
- **Metadaten reisen mit Dateien:** Die [Tagging-Dokumentation](https://docs.tagspaces.org/tagging/) bietet Tags im Dateinamen oder JSON-Sidecars. Sidecars erhalten den Originalnamen; Dateinamen-Tags sind dagegen in jedem Explorer sichtbar, haben aber Pfadlängen- und Umbenennungsnachteile.
- **Explizite AND/OR/NOT-Suche:** Die [Suchdokumentation](https://docs.tagspaces.org/search/) trennt Pflicht-, optionale und auszuschließende Tags und kombiniert sie mit Typ-, Größen- und Datumsfiltern.
- **Mehrere Sichten statt Zwangshierarchie:** Liste, Galerie, Kanban, Karte und Kalender zeigen dieselben Dateien für verschiedene Arbeitsweisen.
- **Vorschau und lokale Bearbeitung:** Viele Dateitypen können ohne Cloudkonto betrachtet werden; editierbare Textformate bleiben reguläre Dateien.
- **Begrenzte Indizierung:** Binäre Einbettungen werden laut Suchdokumentation nicht in den Volltextindex aufgenommen. Das hält Indexe relevant und reduziert Ressourcenverbrauch.

## Was SimpleOffice bei der Übertragung verbessert

### Stabile Dokument-ID statt Tag-Umbenennung

SimpleOffice ändert Dateinamen nicht, um Tags einzubetten. Das verhindert kaputte Links, WebDAV-URLs und lange Windows-Pfade. Die bestehende stabile Dokument-ID bleibt auch nach Verschiebungen erhalten. Tags werden weiterhin revisionssicher im internen Metadatensatz geführt.

### Neutrales portables Sidecar

Der neue Export legt neben dem bestehenden internen Datensatz im versteckten Ordner `.simpleoffice` eine Datei `<Originalname>.simpleoffice.json` an. Das Format enthält:

- Schema- und Versionskennung,
- stabile Dokument-ID und Originaldateiname,
- SHA-256 zur Konflikt- und Zuordnungsprüfung,
- Zustand, Tags und Beschreibung,
- bei extrahierten Anhängen die Herkunft,
- Exportzeitpunkt.

FreeFileSync muss versteckte Ordner einschließen, damit Sidecars gemeinsam mit Dateien übertragen werden. Anders als bei einer stillen automatischen Spiegelung startet der Benutzer den Export sichtbar. Dadurch entstehen keine unerwarteten Schreibvorgänge in eingebundenen oder nur lesbaren Archiven.

### Audit und Konflikterkennung

Jeder Einzel- und Sammel-Export wird protokolliert. Der SHA-256-Wert bindet Metadaten an einen konkreten Dateiinhalt. Ein künftiger Sidecar-Import darf bei abweichendem Hash nicht still überschreiben, sondern muss eine Konfliktentscheidung verlangen. Diese Ausbaustufe exportiert bewusst nur; dadurch kann ein Fremdprogramm noch keine Rechte, Aufbewahrung oder interne Metadaten verändern.

### Malware-Grenze vor Nutzbarkeit

Tagging und Vorschau allein sind keine Sicherheitsfreigabe. Aus EML extrahierte Dateien erscheinen in SimpleOffice erst nach bestätigter Quarantäne- und ClamAV-Prüfung. Nur saubere Dateien gelangen in WebDAV und damit in LibreOffice oder Dateimanager.

### Schreibende Standardintegration

Anstelle eigener Office-Editoren stellt SimpleOffice bestehende Dateien über einen gesperrten, ETag-geschützten WebDAV-Endpunkt bereit. LibreOffice, Nautilus und kompatible Dateimanager speichern damit in die normale Versionierung zurück. App-Passwörter sind getrennt vom Web-Login und können widerrufen werden.

## Bedienung und FreeFileSync

Auf der Dokumentseite **Sidecars für alle Dateien aktualisieren** wählen. Danach in FreeFileSync den Dokumentwurzelordner als Quelle oder Ziel verwenden und sicherstellen, dass versteckte `.simpleoffice`-Ordner nicht ausgeschlossen sind. Empfohlen ist zunächst **Vergleichen nach Dateiinhalt** oder nach Zeit und Größe mit Vorschau, bevor Änderungen ausgeführt werden.

Sidecars sind klein und menschenlesbar. Sie dürfen kopiert und gesichert werden. Sie enthalten jedoch Tags, Beschreibungen und gegebenenfalls Mail-Herkunft; Synchronisationsziele müssen datenschutzrechtlich genauso geschützt werden wie die Dokumente.

## Rechte, Sicherheit und Datenschutz

- Der Export verlangt eine angemeldete Sitzung und verändert keine Dokumentrechte.
- Es werden keine Zugangsdaten, Freigabelinks oder WebDAV-App-Passwörter exportiert.
- Der Export folgt keinen Symlinks und schreibt nur in den kontrollierten Metadatenordner des Dokuments.
- Externe Tools dürfen Sidecars lesen, aber SimpleOffice importiert sie in dieser Version nicht automatisch.
- Dateien aus Quarantäne werden nicht exportiert oder per WebDAV angeboten.
- Sidecars können sensible Herkunft enthalten; FreeFileSync-Ziele und Backups müssen entsprechend geschützt werden.

## Fehler, Migration, Tests und Deaktivierung

Nicht schreibbare Ordner werden als Fehler gezählt; andere Dateien werden weiter verarbeitet. Ein erneuter Export ersetzt das Sidecar atomar. Bestehende Dateien, Tags und Pfade benötigen keine Migration. Alte Installationen ignorieren das zusätzliche JSON.

Tests prüfen unveränderte Originalbytes, deterministische Tags, SHA-256, Herkunft, atomischen Einzel- und Sammelexport, fehlende Dateien, Symlinks sowie Audit-Ereignisse. WebDAV-Tests prüfen Lesen, Schreiben, Sperren und ETag-Konflikte getrennt.

Zur Deaktivierung den Export nicht ausführen. Vorhandene `*.simpleoffice.json`-Dateien können nach Prüfung der Backup- und Aufbewahrungsvorgaben entfernt werden; interne Metadaten bleiben bestehen. Ein automatischer Sidecar-Import, Explorer-Shell-Erweiterungen, Vorschaubilder und gespeicherte komplexe Suchabfragen sind dokumentierte mögliche Folgeschritte.
