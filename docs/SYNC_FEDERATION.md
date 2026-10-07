# Synchronisation und Föderation

SimpleOffice4Me trennt bewusst **Dateisynchronisation** von **föderierter Suche**. Das verhindert, dass ein Knoten versehentlich zum Eigentümer fremder Originale wird.

## Synchronisierte Eingangsordner

Ein normaler Client schreibt in einen freigegebenen lokalen Ordner, den der Dokumentscanner als Eingang beobachtet. Dafür eignen sich insbesondere:

- **Nextcloud Desktop Client**: ein synchronisierter Unterordner wie `SimpleOffice-Eingang`.
- **Syncthing**: plattformübergreifend und ohne zentralen Anbieter; das ist die moderne, offene Alternative zu Resilio/BitTorrent Sync.
- Ein eigener WebDAV-, SMB- oder lokaler Backup-Client, sofern er nur Dateien ablegt und keine Symlinks erzeugt.

Nach einem Scan erhalten alle Dateien eine SHA-256-Prüfsumme. Beim Web-Upload kann die Option *Direkt ins feste Archiv* verwendet werden: die Datei landet in `archive/<erste-zwei-Hash-Zeichen>/<vollständiger-Hash>/`. Gleicher Inhalt wird zuverlässig als Duplikat markiert; abweichende Dateinamen werden dennoch nicht verloren. Die Originale bleiben damit vollständig erhalten und der Index kann jederzeit neu aufgebaut werden.

Synchronisierte Ordner sollten nur in **eine** Richtung als Eingang verwendet werden. Das Metadatenverzeichnis `.simpleoffice-meta/` und das Revisionsarchiv `.simpleoffice-history/` dürfen nicht in einen fremden Sync-Ordner kopiert werden, weil parallele Git-Schreibvorgänge Konflikte erzeugen können.

## SSH-Quellen und HTTPS-Freigaben

Unter `/documents/sources/ssh` kann ein SSH-System mit Host, Benutzer,
absolutem Remote-Pfad und optionalem lokalem Schlüsselpfad registriert werden.
Der Import läuft einseitig mit `rsync` über SSH, folgt keinen Links und kopiert
erst in einen internen Staging-Bereich. Erst danach werden die regulären
Hash-, Duplikat- und Revisionsprüfungen ausgeführt. Der Server speichert kein
SSH-Passwort und keinen privaten Schlüssel; sinnvoll sind ein eingeschränkter
Schlüssel oder ein SSH-Agent. Unter Windows ist dafür WSL mit `rsync` sinnvoll.

Eine Datei oder einzelne Notiz kann über einen HTTPS-Link mit Passwort und
Ablaufdatum freigegeben werden. Es wird nur ein scrypt-Hash des Passworts
gespeichert. Der Link ist kein ungeschützter Direktpfad: Erst nach erfolgreicher
Passwortprüfung wird die Datei ausgeliefert oder die Notiz angezeigt. Das
öffentliche System muss dafür hinter einer korrekt eingerichteten HTTPS-URL
betrieben werden; für reinen lokalen Betrieb funktionieren die Links nur im
lokalen Netz.

## Externe Archive

Ein externes Archiv erhält im Wurzelordner die kleine Datei `.simpleoffice-archive.json`. Sie enthält eine zufällige Archiv-ID, einen Namen und Tags. Die zentrale Registry kennt diese Kennung weiter, wenn die Platte nicht angeschlossen ist. Die Oberfläche unter `/documents/archives` kann eingehängte Laufwerke prüfen:

- Linux: Einhängepunkte aus `/proc/mounts` (und damit auch udev/`/dev`-Mounts).
- macOS: `/Volumes`.
- Windows: alle verfügbaren Laufwerksbuchstaben über die Windows-API.

Die Suche liest nur den Wurzelordner eines Laufwerks und folgt keinen Links. So ist sie schnell und durchsucht keine fremden Daten. Der konkrete Einhängepunkt wird zuletzt gesehen gespeichert; nicht angeschlossene Archive bleiben als „nicht verbunden“ sichtbar.

## Föderation: aktueller Stand und Kompatibilitätsgrenze

Föderation ist keine reine Zukunftsplanung mehr. SimpleOffice4Me besitzt heute
authentifizierte Peer-Kopplung, Katalog- und Transferpfade sowie den persistenten
V2-Desired-State-Pfad mit deny-first Route Policy und Capability-Grants. Der
Dokumentkatalog bezieht die autoritativen Dokumentfelder im V2-Modus über
`StoragePort`; dokumentgebundene V2-Downloads und Blockmanifeste verwenden
verifizierte, private Materialisierung statt die persistente
DocumentStore-Klartextprojektion.

OAuth/OIDC ist dafür keine generelle Laufzeitvoraussetzung. Die vorhandenen
Peer-Protokolle verwenden ihre dokumentierten peer-spezifischen
Authentisierungs- und Autorisierungsnachweise. HTTPS bleibt für produktive
Netzverbindungen erforderlich; lokale Denies, Scope-Grenzen und
Capability-Prüfungen dürfen durch keinen Transport umgangen werden.

Ältere SOFP-v1- und `federation_transfer`-Pfade bleiben für Alt-Peers bewusst
erhalten. Insbesondere globale Legacy-Blockindex/-cache-, Reparatur- und
Rebalance-Pfade sowie weitere DocumentStore-basierte Enumeration und
Metadaten-/Policy-Readmodelle gehören noch zum Kompatibilitätsfenster. Sie
dürfen erst entfernt werden, wenn die in
[V2 Legacy Cleanup](V2_LEGACY_CLEANUP.md) dokumentierten Readiness-,
Shadow-/Rollback- und Restore-Gates erfüllt sind. Der aktuelle
`legacy_cleanup_status(...)`-Gate ist deshalb fail-closed; zurückbehaltene
Legacy-Daten sind noch keine freigegebenen Löschkandidaten.

Die Protokollgrenzen und aktuellen V2-/V3-Federationspfade sind in
[Federation Protocol](FEDERATION_PROTOCOL.md),
[V2 Federation Jobs](V2_FEDERATION_JOBS.md) und
[V3 Federation](V3_FEDERATION.md) beschrieben.
