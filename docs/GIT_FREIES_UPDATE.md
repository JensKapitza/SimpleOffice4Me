# Git-freies Update von SimpleOffice4Me

## Ziel

Eine installierte SimpleOffice4Me-Anwendung soll **kein lokal installiertes Git benötigen**, um auf einen neuen Programmstand aktualisiert zu werden. Git bleibt ein Entwicklungswerkzeug und kann im Quellrepository weiterhin verwendet werden, ist aber für den normalen Betrieb und das Einspielen eines Updates nicht erforderlich.

## Warum diese Änderung?

Der bisherige Updatepfad verwendete `git pull --ff-only`. Das ist für eine Entwickler-Arbeitskopie sinnvoll, macht aber jede produktive, portable oder paketierte Installation von einem externen Git-Programm und einer vollständigen `.git`-Arbeitskopie abhängig.

Das ist besonders für Desktop-Pakete, Windows-Installationen, AppImages, Docker-Umgebungen und andere vorkonfigurierte Installationen unnötig. Die Anwendung benötigt zum Ausführen weder Git-Historie noch Branch-Operationen.

Der neue Updatepfad trennt deshalb klar:

- **Entwicklung:** Git kann weiterhin verwendet werden.
- **Installation/Betrieb:** Python und HTTPS reichen für das Anwendungsupdate aus.
- **Versionsinformation:** wird nach dem Update in `.simpleoffice-release.json` festgehalten.

## Ablauf

`update.sh` beziehungsweise `update.bat`:

1. prüft, ob SimpleOffice4Me aktuell läuft,
2. stoppt die Anwendung bei Bedarf,
3. startet `tools/release_updater.py`,
4. löst den gewünschten GitHub-Ref standardmäßig auf `main` in eine konkrete Commit-ID auf,
5. lädt genau diesen Stand als HTTPS-ZIP-Archiv,
6. prüft das Archiv auf Größen-, Pfad- und Symlink-Probleme,
7. ersetzt ausschließlich den von der Anwendung verwalteten Programmbaum,
8. schreibt die installierte Version und Revision nach `.simpleoffice-release.json`,
9. startet die Anwendung wieder, wenn sie vorher lief.

Git wird in keinem dieser Schritte benötigt.

## Schutz lokaler Daten

Der Updater ersetzt Quellcode und mitgelieferte Programmressourcen. Lokale Laufzeitdaten werden ausdrücklich nicht als Teil des Updates behandelt.

Insbesondere bleiben bestehen:

- `.venv`
- `instance`
- `.simpleoffice-history`
- `.simpleoffice-control`
- `.git`, falls eine Entwicklerinstallation dennoch eine Git-Arbeitskopie besitzt
- `node_modules`

Auch die eigentlichen Dokument-/Kundendaten liegen außerhalb des auszutauschenden Programmbaums und werden nicht aus dem Download übernommen.

## Rollback bei Kopierfehlern

Vor dem Ersetzen eines verwalteten Programmordners oder einer Programmdatei wird der vorhandene Stand temporär gesichert. Schlägt das Kopieren während des Updates fehl, stellt der Updater die bereits angefassten Programmteile aus diesem temporären Backup wieder her.

Damit soll ein Dateisystemfehler nicht zu einer halb aktualisierten Installation führen.

## Schutz gegen unsichere Archive

Der Updater akzeptiert keine beliebigen ZIP-Strukturen. Geprüft werden unter anderem:

- genau ein Projektwurzelverzeichnis,
- keine absoluten Pfade,
- keine `..`-Pfadbestandteile,
- keine symbolischen Links,
- maximale Dateianzahl,
- maximale Downloadgröße,
- maximale entpackte Gesamtgröße,
- Vorhandensein von `pyproject.toml` und `app/`.

HTTP wird für den normalen Download nicht akzeptiert. Redirects müssen ebenfalls bei HTTPS bleiben.

## Versionierung

Nach erfolgreichem Update wird `.simpleoffice-release.json` geschrieben. Enthalten sind unter anderem:

- Projektversion aus `pyproject.toml`,
- konkrete GitHub-Commit-ID des heruntergeladenen Standes,
- verwendeter Ref, standardmäßig `main`,
- Build-/Commit-Zeit,
- Updatezeit,
- `update_mode = "https-source-archive"`.

Damit kann eine Installation ihren Stand auch ohne `.git`-Verzeichnis eindeutig anzeigen.

## Anderen Ref verwenden

Standard ist `main`. Für kontrollierte Tests kann vor dem Update beispielsweise ein anderer Branch oder Tag gewählt werden:

```bash
SIMPLEOFFICE_UPDATE_REF=v1.1.0 ./update.sh
```

Unter Windows kann `SIMPLEOFFICE_UPDATE_REF` entsprechend als Umgebungsvariable gesetzt werden.

## Lokales/offline Update-Archiv

Der Python-Updater kann außerdem direkt mit einem bereits vorhandenen ZIP betrieben werden:

```bash
python3 tools/release_updater.py --archive /pfad/SimpleOffice4Me.zip --sha256 <sha256>
```

Die optionale SHA-256-Angabe ermöglicht bei einem extern übertragenen Archiv eine zusätzliche Integritätsprüfung vor dem Einspielen.

## Abgrenzung zum Self-Deploy

Der bestehende Self-Deploy-/Federation-Code besitzt historisch noch Git-Bundle-Funktionen für das Erzeugen und Verteilen bestimmter Releasepakete. Der normale `update.sh`-/`update.bat`-Pfad benötigt diese Funktionen nicht mehr.

Langfristig sollte auch das Self-Deploy-Releaseformat auf ein Git-unabhängiges, manifestbasiertes Quell-/Artefaktpaket umgestellt werden. Diese Änderung kann separat erfolgen, damit der normale Anwendungsupdatepfad bereits jetzt ohne Git funktioniert und das bestehende Federation-Verhalten nicht unnötig gleichzeitig umgebaut wird.
