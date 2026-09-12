# Git-freies Update, Self-Deploy und Federation-Releases

## Ziel

Eine installierte SimpleOffice4Me-Anwendung soll **kein lokal installiertes Git benötigen** – weder für normale Updates noch für Self-Deploy oder die Verteilung eines Releases über Federation. Git bleibt ausschließlich ein mögliches Entwicklungswerkzeug.

## Warum diese Änderung?

Der frühere Updatepfad verwendete `git pull --ff-only`, Self-Deploy erzeugte `repository.bundle` und Offline-Updates arbeiteten mit `git clone`, `git fetch`, `git merge` und `git reset`.

Das machte portable, paketierte und produktive Installationen unnötig von einem externen Git-Programm und einer vollständigen `.git`-Arbeitskopie abhängig. Für den Betrieb der Anwendung werden jedoch weder Branches noch Commitobjekte benötigt.

Die Architektur trennt deshalb jetzt klar:

- **Entwicklung:** Git kann weiterhin verwendet werden.
- **Installation/Betrieb:** Python, Dateisystem und HTTPS beziehungsweise Federation reichen aus.
- **Versionsinformation:** wird in `.simpleoffice-release.json` persistiert.
- **Integrität:** wird durch SHA-256 auf Release-, Payload- und Einzeldateiebene geprüft.

## Normaler Online-Updatepfad

`update.sh` beziehungsweise `update.bat`:

1. prüft, ob SimpleOffice4Me läuft,
2. stoppt die Anwendung bei Bedarf,
3. startet `tools/release_updater.py`,
4. löst standardmäßig `main` auf eine konkrete GitHub-Commit-ID auf,
5. lädt genau diesen Stand als HTTPS-ZIP,
6. prüft Archivpfade, Symlinks und Größenlimits,
7. ersetzt nur den verwalteten Programmbaum,
8. schreibt `.simpleoffice-release.json`,
9. startet die Anwendung wieder, wenn sie vorher lief.

Git wird dafür nicht benötigt.

## Self-Deploy-Releaseformat

Self-Deploy verwendet kein Git-Bundle mehr. Ein Release-ZIP enthält:

- `release.json` – Release-, Plattform- und Integritätsmetadaten,
- `release-package.zip` – den eigentlichen Programmbaum,
- `INSTALL.py` – einen Offline-Installer ohne Git-Abhängigkeit,
- optional `wheelhouse/*.whl` für vollständig offline installierbare Python-Abhängigkeiten.

`release.json` enthält für jede ausgelieferte Programmdatei:

- relativen Pfad,
- Dateigröße,
- SHA-256,
- Dateimodus,
- zusätzlich einen SHA-256 über die kanonische gesamte Dateiliste (`tree_sha256`),
- SHA-256 und Größe von `release-package.zip`.

Damit kann ein Zielsystem den Inhalt prüfen, ohne Git-Objekte oder eine Git-Historie interpretieren zu müssen.

## Warum Einzeldatei-Hashes und Baum-Hash?

Nur den äußeren ZIP-Hash zu prüfen schützt den Transport, beschreibt aber nicht, welche konkreten Programmdateien zu einem Release gehören. Das Manifest macht den Inhalt explizit und auditierbar.

Die Kombination aus Einzeldatei-Hash, Payload-Archiv-Hash und Baum-Hash erlaubt:

- beschädigte Einzeldateien zu erkennen,
- unerwartete zusätzliche Dateien abzulehnen,
- fehlende Dateien zu erkennen,
- den vollständigen Programmstand eindeutig zu identifizieren,
- Releases auch über andere Transportwege als GitHub identisch zu prüfen.

## Federation

Die bestehende Federation musste für den Transport nicht neu erfunden werden. Sie überträgt bereits das vollständige Release-ZIP:

1. der anbietende Peer meldet Release-Version und SHA-256,
2. der empfangende Peer fordert ein Chunk-Manifest an,
3. große Release-Dateien werden chunkweise übertragen,
4. jeder Chunk wird geprüft,
5. abschließend wird der SHA-256 des vollständigen Release-ZIPs geprüft,
6. anschließend wird zusätzlich das interne Release-/Payload-Manifest verifiziert.

Der frühere Git-Bundle-Inhalt war innerhalb dieses bereits abgesicherten Transports redundant und wurde entfernt.

Die asymmetrischen Federation-Rechte bleiben erhalten: Ein Peer kann Software anbieten, ohne selbst Software annehmen zu müssen. `software.receive=true` bleibt eine explizite Voraussetzung für eingehende Releases.

## Installation auf einem neuen Rechner

Ein Self-Deploy-Archiv kann ohne Git auf einem neuen Rechner installiert werden. `clone_release_archive()` beziehungsweise `tools/self_deploy.py clone` entpackt den geprüften Payload direkt in einen leeren Zielordner.

Mit `--offline-install` wird zusätzlich eine lokale `.venv` angelegt und ausschließlich aus dem enthaltenen Wheelhouse installiert. Auch dafür wird kein Netzwerk und kein Git benötigt.

## Offline-Update einer vorhandenen Installation

`tools/self_deploy.py update` ersetzt den verwalteten Programmbaum aus dem geprüften Payload. Die Updateentscheidung erfolgt über persistierte Release-Metadaten wie Version, Buildnummer, Buildzeit und Revision statt über Git-Ancestry oder Fast-Forward-Prüfungen.

Das ist absichtlich ein Anwendungs-Release-Modell und kein Quellcode-Merge-Modell: lokale Änderungen am ausgelieferten Programmcode sind kein unterstützter Produktionszustand. Lokale Laufzeit- und Nutzdaten liegen deshalb außerhalb des verwalteten Programmbaums.

## Schutz lokaler Daten

Nicht als Programmrelease behandelt werden insbesondere:

- `.venv`
- `instance`
- `.simpleoffice-history`
- `.simpleoffice-control`
- `.git`, falls eine Entwicklerinstallation trotzdem eines besitzt
- `node_modules`

Dokumente, Kundendaten und andere Laufzeitdaten werden nicht Bestandteil eines Self-Deploy-Payloads.

## Rollback bei Dateisystemfehlern

Vor dem Ersetzen vorhandener verwalteter Programmordner oder Dateien wird deren vorheriger Stand temporär gesichert. Schlägt das Kopieren fehl, werden bereits ersetzte Programmteile aus diesem Backup wiederhergestellt.

Der Rollback basiert damit ebenfalls auf dem Dateisystem und nicht mehr auf `git reset --hard`.

## Schutz gegen unsichere Archive

Geprüft werden unter anderem:

- keine absoluten Pfade,
- keine `..`-Pfadbestandteile,
- keine symbolischen Links,
- keine unerwarteten äußeren Release-Dateien,
- maximale Dateianzahl,
- maximale Release- und Entpackgröße,
- vollständige Übereinstimmung der Payload-Dateiliste,
- SHA-256 jeder Payload-Datei,
- SHA-256 des Payload-ZIPs,
- SHA-256 des vollständigen Release-ZIPs bei Federation und lokalem Cache.

Der normale Netzwerk-Updater akzeptiert ausschließlich HTTPS; Redirects müssen ebenfalls HTTPS bleiben.

## Versionierung ohne `.git`

Nach Installation oder Update wird `.simpleoffice-release.json` geschrieben. Darin stehen unter anderem:

- Projektversion,
- konkrete Revision beziehungsweise eindeutiger Payload-Stand,
- Branch/Releasekanal,
- Buildzeit,
- Buildnummer, wenn vorhanden,
- verwendeter Update-Modus.

Wenn ein Buildsystem eine GitHub-Commit-ID kennt, kann es diese über die bestehenden `SIMPLEOFFICE_BUILD_*`-Metadaten einbringen. Ist keine Commit-ID verfügbar, kann der Hash des ausgelieferten Dateibaums den Release trotzdem eindeutig identifizieren.

## Fazit

Git ist nach dieser Umstellung keine Laufzeitvoraussetzung mehr für:

- `update.sh` / `update.bat`,
- Self-Deploy-Builds aus einem vollständigen Programmbaum,
- Installation eines Self-Deploy-Archivs,
- Offline-Updates,
- Federation-Releaseübertragung,
- Rollback bei Kopierfehlern.

Git bleibt für Entwickler weiterhin nützlich, ist aber kein Bestandteil des produktiven Updateprotokolls mehr.
