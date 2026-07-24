# Synchronisation und Föderation

SimpleOffice4Me trennt bewusst **Dateisynchronisation** von **föderierter Suche**. Das verhindert, dass ein Knoten versehentlich zum Eigentümer fremder Originale wird.

## Synchronisierte Eingangsordner

Ein normaler Client schreibt in einen freigegebenen lokalen Ordner, den der Dokumentscanner als Eingang beobachtet. Dafür eignen sich insbesondere:

- **Nextcloud Desktop Client**: ein synchronisierter Unterordner wie `SimpleOffice-Eingang`.
- **Syncthing**: plattformübergreifend und ohne zentralen Anbieter; das ist die moderne, offene Alternative zu Resilio/BitTorrent Sync.
- Ein eigener WebDAV-, SMB- oder lokaler Backup-Client, sofern er nur Dateien ablegt und keine Symlinks erzeugt.

Nach einem Scan erhalten alle Dateien eine SHA-256-Prüfsumme. Beim Web-Upload kann die Option *Direkt ins feste Archiv* verwendet werden: die Datei landet in `archive/<erste-zwei-Hash-Zeichen>/<vollständiger-Hash>/`. Gleicher Inhalt wird zuverlässig als Duplikat markiert; abweichende Dateinamen werden dennoch nicht verloren. Die Originale bleiben damit vollständig erhalten und der Index kann jederzeit neu aufgebaut werden.

Synchronisierte Ordner sollten nur in **eine** Richtung als Eingang verwendet werden. Das Metadatenverzeichnis `.simpleoffice-meta/` und das Revisionsarchiv `.simpleoffice-history/` dürfen nicht in einen fremden Sync-Ordner kopiert werden, weil parallele Git-Schreibvorgänge Konflikte erzeugen können.

## Externe Archive

Ein externes Archiv erhält im Wurzelordner die kleine Datei `.simpleoffice-archive.json`. Sie enthält eine zufällige Archiv-ID, einen Namen und Tags. Die zentrale Registry kennt diese Kennung weiter, wenn die Platte nicht angeschlossen ist. Die Oberfläche unter `/documents/archives` kann eingehängte Laufwerke prüfen:

- Linux: Einhängepunkte aus `/proc/mounts` (und damit auch udev/`/dev`-Mounts).
- macOS: `/Volumes`.
- Windows: alle verfügbaren Laufwerksbuchstaben über die Windows-API.

Die Suche liest nur den Wurzelordner eines Laufwerks und folgt keinen Links. So ist sie schnell und durchsucht keine fremden Daten. Der konkrete Einhängepunkt wird zuletzt gesehen gespeichert; nicht angeschlossene Archive bleiben als „nicht verbunden“ sichtbar.

## Föderation: Katalog teilen, Verantwortung behalten

Für die nächste Ausbaustufe wird jeder Knoten einen kleinen, signierten Katalog-Endpunkt anbieten. Er liefert nur die vom Eigentümer freigegebenen Felder: Dokument-ID, Titel/Pfad-Alias, Hash, Tags, Zustand, Version, Aufbewahrungsstatus und eine Abruf-URL. Die Datei selbst und private Notizen bleiben beim verantwortlichen System, bis eine berechtigte Person sie explizit öffnet.

Ein föderierter Suchtreffer zeigt daher immer Eigentümer-Knoten und Archiv-ID, Online-Status der Originaldatei, Rechte für Metadaten/Datei/Bearbeitung sowie die unveränderliche Hash-/Versionskennung.

Knoten tauschen Katalogänderungen inkrementell über Ereignis-IDs aus; keine globale Schreibreplikation. Änderungen an einem fremden Dokument werden als signierte Anfrage an den Eigentümer gesendet und dort mit Benutzer und Git-Revision protokolliert. Dies ermöglicht eine Cockpit-artige zentrale Sicht, ohne Zuständigkeiten oder Aufbewahrungsfristen zu vermischen.

Vor einer produktiven Föderation sind OAuth/OIDC, pro Knoten ein Schlüsselpaar, HTTPS und ein Rechteabgleich zwingend. Sie werden nicht durch einen offenen Dateishare ersetzt.
