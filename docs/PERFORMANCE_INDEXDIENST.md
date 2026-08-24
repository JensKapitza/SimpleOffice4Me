# Schneller Start und getrennter Dokumentindex

## Zweck und Nutzen

Bei großen Beständen darf das Einlesen, Hashen, Extrahieren und OCR-Verarbeiten
von Dokumenten weder den Login noch normale HTTP-, WebDAV-, CalDAV- oder
CardDAV-Anfragen blockieren. SimpleOffice startet den Dokumentindex deshalb als
eigenen, niedrig priorisierten Prozess. Waitress und Indexer teilen weder den
Python-GIL noch den WSGI-Threadpool.

Dashboard und Inbox lesen außerdem nur noch eine begrenzte Seite aus einer
kleinen SQLite-Projektion. Bei 50.147 Dateien werden für das Dashboard höchstens
acht und für eine Inbox-Seite höchstens 100 Metadaten-Sidecars geöffnet. Die
frühere vollständige Schleife über alle JSON-Dateien entfällt.

## Technische Auslegung und Primärquellen

- Python empfiehlt `subprocess` zum Erzeugen neuer Prozesse. Der Indexdienst
  wird ohne Shell und mit einer festen Argumentliste gestartet. Unter Unix wird
  eine neue Sitzung verwendet, unter Windows die Prioritätsklasse
  `BELOW_NORMAL_PRIORITY_CLASS`.
  [Python `subprocess`](https://docs.python.org/3/library/subprocess.html)
- SQLite-WAL erlaubt parallele Leser und einen Schreiber. SimpleOffice behält
  kurze, unabhängige Verbindungen bei, setzt den persistenten Journalmodus aber
  nicht mehr bei jedem einzelnen Dateizugriff erneut. Schema- und
  Migrationsprüfungen werden pro Prozess und Dokumentwurzel einmal ausgeführt;
  ein gelöschter Index wird trotzdem erkannt und neu aufgebaut.
  [SQLite Write-Ahead Logging](https://www.sqlite.org/wal.html)
- Waitress bearbeitet Webanfragen weiterhin in seinem eigenen begrenzten
  Threadpool. Rechenintensive Indexarbeit wird nicht als WSGI-Thread ausgeführt.
  [Waitress Arguments](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html)
- Flask empfiehlt für den Produktivbetrieb einen dedizierten WSGI-Server; der
  Indexer ist kein zweiter Webserver und erhält keinen Netzwerkport.
  [Flask: Waitress](https://flask.palletsprojects.com/en/stable/deploying/waitress/)

## Ablauf

1. Der Launcher lädt Konfiguration und WSGI-Anwendung.
2. Ein eigener Python-Prozess wird ohne Shell gestartet.
3. Der Indexer belegt eine nicht blockierende, pro Dokumentwurzel eindeutige
   Betriebssperre. Ein zweiter Launcher beendet seinen doppelten Indexauftrag,
   statt ihn später nochmals auszuführen.
4. Standardmäßig wartet der Indexer zwei Sekunden, damit Waitress zuerst den
   Port annimmt.
5. Der Prozess läuft unter Unix mit Niceness 10 und gibt nach jeder Datei
   standardmäßig eine Millisekunde Rechen-/I/O-Zeit ab.
6. Fortschritt und Fehler werden atomar in
   `.simpleoffice-meta/scan-status.json` geschrieben und im Dashboard gezeigt.

Der Fortschritt trennt `new` und `updated`: `new` bezeichnet eine zuvor nicht
bekannte Datei. `updated` zählt bekannte Dokumente, deren Inhalt, Zeitstempel,
Pfad, erkannte Dateinamen-Tags oder reparierbare Metadaten beim Scan neu in den
Index übernommen wurden. Unveränderte Dateien erhöhen nur `files`; ein reiner
erneuter Sichtkontakt wird nicht als Aktualisierung gezählt.

Ein Absturz des Indexdienstes beendet den Webserver nicht. Beim nächsten Start
wird der reparierbare Index fortgesetzt beziehungsweise erneut abgeglichen.

## Konfiguration

| Variable | Standard | Bereich / Wirkung |
|---|---:|---|
| `SIMPLEOFFICE_BACKGROUND_INDEX` | `1` | `0`, `false`, `no` oder `off` deaktiviert nur den automatischen Start |
| `SIMPLEOFFICE_INDEX_DELAY_SECONDS` | `2` | 0–300 Sekunden Startverzögerung |
| `SIMPLEOFFICE_INDEX_NICE` | `10` | Unix-Niceness 0–19; unter Windows wird die Prioritätsklasse verwendet |
| `SIMPLEOFFICE_INDEX_YIELD_MS` | `1` | 0–100 ms Pause je Datei; 0 maximiert Indexdurchsatz, höhere Werte priorisieren Webzugriffe |
| `SIMPLEOFFICE_WSGI_THREADS` | `4` | Unverändert 1–64 Waitress-Threads |

Empfehlung für einen interaktiven Server ist der Standard. Bei ausschließlich
nachts laufender Indexierung kann `SIMPLEOFFICE_INDEX_YIELD_MS=0` den
Gesamtdurchsatz erhöhen. Auf langsamen Netzspeichern sind 2 bis 10 ms sinnvoll,
wenn die Oberfläche wichtiger als eine kurze Scanzeit ist.

## Login, Dashboard und Inbox

Der Login prüft weiterhin nur die Authentifizierungsdaten. Sein Redirect zum
Dashboard verursacht jetzt:

- genau eine Speicherplatzabfrage für den konfigurierten Dokumentordner;
- keine Suche über alle Betriebssystem-Mounts oder möglicherweise getrennte
  SMB-/NFS-Laufwerke;
- eine begrenzte SQLite-Abfrage und höchstens acht Sidecar-Lesezugriffe;
- keine vollständige Dokument-, OCR- oder Hashprüfung.

Externe Archive werden weiterhin nur über die vorhandene, ausdrücklich
ausgelöste Archivsuche geprüft. Die Inbox ist paginiert und liest 100 Einträge
je Seite.

## Sicherheit, Datenschutz und Rechte

- Der Indexprozess läuft unter demselben nicht privilegierten Betriebskonto und
  erhält keine eigenen Netzwerkports oder zusätzlichen Datei- und Benutzerrechte.
- Die feste Argumentliste verhindert Shell-Injektion. Der Dokumentpfad wird als
  einzelnes Argument übergeben.
- Die Prozesssperre enthält keine Zugangsdaten. Betriebssystem-Locks werden auch
  nach einem Absturz automatisch freigegeben.
- SQLite und JSON bleiben abgeleitete, lokal gespeicherte Indexdaten. Dokumente,
  Freigaben, Aufbewahrungsregeln und Audit-Historie werden nicht migriert oder
  gelockert.
- Dateisystemgrenzen, Symlinkregeln, Extraktionslimits und OCR-Zeitlimits gelten
  unverändert im separaten Prozess.

## Fehler- und Ausfallverhalten

- Kann der Prozess nicht gestartet werden, startet Waitress trotzdem und das
  Dashboard zeigt einen verständlichen Fehler. Vorhandene Indexdaten bleiben
  lesbar.
- Eine bereits gehaltene Indexsperre beendet einen doppelten Worker erfolgreich,
  ohne dessen Status zu überschreiben.
- Fehler einzelner Dateien werden gezählt; der Scan läuft mit den vorhandenen
  Sicherheitsgrenzen weiter.
- Ein beschädigter oder gelöschter SQLite-Index wird aus den unveränderten
  Dateien und Sidecars wieder aufgebaut.
- Die erste Anzeige während eines Neuaufbaus kann unvollständig sein, blockiert
  aber nicht. Sie wächst mit der Indexprojektion.

## Kontrolliertes Stoppen und Aktualisieren

`stop.sh` beziehungsweise `stop.bat` beendet zuerst den Indexprozess und danach
den Webprozess mit dem normalen Betriebssystemsignal. Der Launcher wartet beim
eigenen Ende bis zu zehn Sekunden auf den Indexer. Es wird kein fremder Prozess
nur aufgrund einer wiederverwendeten PID beendet: Die private PID-Datei unter
`instance/run` enthält zusätzlich Prozessrolle, Startkennung und einen erwarteten
Kommandozeilenmarker. Stimmen diese Merkmale nicht überein, gilt der Eintrag als
veraltet und wird lediglich entfernt.

`update.sh` und `update.bat` prüfen zuerst auf lokale Änderungen. War der Dienst
aktiv, wird er danach kontrolliert gestoppt, das Fast-Forward-Update eingespielt
und anschließend wieder gestartet. War er vorher gestoppt, bleibt er gestoppt.
Schlägt das Beenden fehl, findet kein Git-Update statt. Damit laufen während des
Austauschs von Python-Dateien weder alter Indexcode noch ein alter WSGI-Prozess.

```bash
./stop.sh
./update.sh
./start.sh
```

Unter Windows entsprechend `stop.bat`, `update.bat` und `start.bat` verwenden.
Ein erzwungenes `kill -9` beziehungsweise `taskkill /F` ist absichtlich nicht
Teil der Skripte; bei einem hängenden Prozess bleiben Status und PID-Datei zur
Diagnose erhalten.

## Migration und Rückwärtskompatibilität

`document_listing` ist eine additive, löschbare SQLite-Projektion. Beim ersten
normalen Scan werden auch unveränderte Dateien ohne erneutes Hashen oder OCR in
diese Tabelle übernommen. Es gibt keine Nutzdatenmigration.

Startskripte, URLs, Dateiformate, App-Passwörter sowie WebDAV-/CalDAV-/CardDAV-
Verhalten bleiben kompatibel. Manuelle Befehle wie `flask scan-documents`
funktionieren weiterhin synchron für Wartungszwecke.

## Automatisierte Tests

Geprüft werden:

- 50.147 projizierte Inbox-Dokumente bei nur 25 Sidecar-Lesezugriffen für eine
  angeforderte Testseite;
- Backfill unveränderter Dateien ohne erneutes SHA-256-Hashing;
- paginierte Inbox und schneller Login-Redirect ohne `_all_documents()`;
- kein Mount-Probing im Dashboard;
- eigener Python-Prozess, sichere feste Argumentliste und Deaktivierung;
- nicht blockierende Sperre gegen doppelte Indexdienste;
- Status, Abschlusswerte und begrenzte Einstellungen;
- vollständige bestehende Test-Suite.

## Bekannte Grenzen und Rückkehr

Der Indexprozess ist kein verteilter Queue-Dienst. Mehrere Rechner mit derselben
Netzfreigabe benötigen weiterhin einen einzigen verantwortlichen
SimpleOffice-Indexer und eine für Dateisperren geeignete Freigabe. Langsame oder
fehlerhafte Datenträger können den Indexer selbst verzögern, nicht aber den
WSGI-Threadpool. Die SQLite-Projektion zeigt nur bereits verarbeitete Dateien.

Zur vorübergehenden Deaktivierung wird
`SIMPLEOFFICE_BACKGROUND_INDEX=0` gesetzt. Ein manueller Scan ist dann weiterhin
möglich. Für die vollständige Rückkehr wird der Commit zurückgenommen; die
zusätzliche SQLite-Tabelle kann bestehen bleiben oder zusammen mit dem gesamten
reparierbaren `index.sqlite3` gelöscht werden. Dokumente und Sidecars bleiben
unverändert.
