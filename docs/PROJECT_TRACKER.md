# Projekt-Tracker: Issues, Wiki und Git

SimpleOffice4Me erweitert die bestehende Projektakte um einen kleinen
Trac-artigen Arbeitsbereich. Das Ziel ist bewusst **kein Jira-Klon** und auch
kein eigenes Versionskontrollsystem.

## Bereiche

Jedes Projekt erhält folgende Bereiche:

- **Projekt** – bestehende Projektakte mit Aufgaben, Zeiten, Dateien und
  Abrechnung.
- **Issues** – kleines Ticketsystem für Bugs, Features, Aufgaben und Ideen.
- **Wiki** – Markdown-Seiten mit sicherem serverseitigem Renderer.
- **Code** – erscheint nur, wenn ein freigegebenes lokales Git-Working-Tree
  tatsächlich erkannt wird.
- **Aktivität** – gemeinsame Chronik aus Issue-, Wiki- und Git-Aktivität.

## Issues

Issues besitzen eine projektlokale fortlaufende Nummer (`#1`, `#2`, …),
Titel, Markdown-Beschreibung, Typ, Status, Priorität, Verantwortlichen, Labels,
Milestone, Fälligkeitsdatum und Kommentare.

Änderungen und Kommentare werden zusätzlich in der bestehenden
`RevisionHistory` protokolliert. Der JSON-Speicher
`.simpleoffice-meta/project-tracker.json` wird atomar geschrieben und ist
Bestandteil der Projekt-Replikationskategorie.

Die Bearbeitungsmaske verwendet `updated_at` als einfache
Optimistic-Concurrency-Prüfung. Ein zwischenzeitlich geändertes Issue wird nicht
still überschrieben.

## Wiki und Markdown

Wiki-Seiten werden als Markdown-Inhalt im Projekt-Tracker gespeichert und
versioniert. Der Renderer benötigt keine zusätzliche Laufzeitabhängigkeit.

Unterstützt werden unter anderem:

- Überschriften,
- Absätze und Zeilenumbrüche,
- ungeordnete/geordnete Listen,
- Task-Checkboxen,
- Codeblöcke und Inline-Code,
- Links,
- fett/kursiv/durchgestrichen,
- Blockquotes,
- einfache Tabellen.

Rohes HTML wird immer escaped. Links akzeptieren nur `http`, `https`,
`mailto` oder relative lokale Ziele. Schemes wie `javascript:` werden nicht
als Links ausgegeben.

## Git-Codebereich

SimpleOffice implementiert **kein eigenes Git**. Der Code-Tab wird nur
eingeblendet, wenn alle folgenden Bedingungen erfüllt sind:

1. `git` ist auf dem Server installiert.
2. Im Projekt ist `repository_path` gesetzt.
3. Der Pfad liegt im Dokumentroot oder unter einem explizit freigegebenen
   Repository-Root.
4. Das zugehörige Git-Metadatenverzeichnis liegt ebenfalls innerhalb eines
   freigegebenen Repository-Roots.
5. Der Pfad zeigt exakt auf die Wurzel eines vorhandenen Git-Working-Trees.

Standardmäßig sind nur Repositories innerhalb von `DOCUMENT_ROOT` erlaubt.
Nur Administratoren können den Repository-Pfad eines Projekts konfigurieren.
Normale Projektbenutzer können einen bereits freigegebenen Code-Bereich nutzen,
aber den Serverpfad nicht auf ein anderes Repository umstellen.

Weitere Wurzeln können per Pfadliste freigegeben werden:

```text
SIMPLEOFFICE_PROJECT_REPO_ROOTS=/srv/git:/opt/company-repos
```

Unter Windows wird der jeweilige Plattform-Pfadtrenner verwendet.

Der aktuelle Codebereich ist absichtlich read-only. Angezeigt werden Branch,
HEAD, Arbeitsbaumstatus einschließlich unversionierter Dateien,
Commit-Historie, Verzeichnisbaum und UTF-8-Textdateien bis 512 KiB.
Ein frisch initialisiertes Repository ohne ersten Commit bleibt als leerer
Code-Bereich nutzbar. Markdown-Dateien aus dem Repository werden mit demselben
sicheren Renderer dargestellt.

Git-Aufrufe verwenden Argumentlisten ohne Shell-Interpolation, Zeitlimits,
Ausgabelimits und deaktivieren optionale Git-Helfer wie `core.fsmonitor`.
Unbekannte oder nicht freigegebene Pfade führen nicht zu Dateisystemzugriffen.

## Issue-Referenzen aus Commits

Wenn ein Commit-Betreff beispielsweise `Fix #12`, `Refs #12` oder eine
andere eindeutige `#12`-Referenz enthält, erscheint dieser Commit beim
zugehörigen Issue. Das schließt das Issue derzeit nicht automatisch.

## Abgrenzung

Noch nicht Bestandteil dieses ersten Schritts:

- Git-Schreibaktionen wie Commit, Branch erstellen, Merge oder Push,
- GitHub-Issue-Synchronisierung,
- signierte Webhooks,
- komplexe Wiki-Dateianhänge,
- automatische Issue-Schließung durch Commit-Nachrichten.

Diese Punkte können auf der jetzt vorhandenen Projektgrenze ergänzt werden,
ohne Quellcode oder Git-Historie in SimpleOffice selbst nachzubauen.
