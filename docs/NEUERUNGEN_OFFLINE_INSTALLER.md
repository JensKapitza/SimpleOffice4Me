# Neuerungen: Offline-Installer und Software-Verteilung

Diese Datei beschreibt die neuen Self-Deploy- und Federation-Funktionen rund um fertige Installationsartefakte und erklärt die Gründe für die Architektur.

## Ausgangsproblem

SimpleOffice4Me konnte Releases und Quell-/Build-Inhalte verteilen, fertige Installationspakete waren aber weiterhin stark von GitHub Actions und einer funktionierenden Internetverbindung abhängig. Für eine vollständig offline betreibbare Umgebung ist das unpraktisch: Ein Windows-Installer, eine APK oder ein Docker-Image sollte auch dann verfügbar bleiben, wenn GitHub vorübergehend nicht erreichbar ist oder ein Zielsystem bewusst ohne Internet betrieben wird.

## Ziel

Fertige Installationsartefakte sollen einmal kontrolliert übernommen und danach lokal dauerhaft verfügbar sein. Bekannte Federation-Peers sollen zunächst nur sehen, welche Pakete vorhanden sind, und anschließend gezielt genau die benötigten Dateien abrufen können.

## Was neu ist

- lokaler, hash-adressierter Installer-Cache
- persistenter Katalog der verfügbaren Pakete
- manueller Upload durch Administratoren
- optionaler Import geeigneter GitHub-Actions-Artefakte
- Unterstützung für Windows-, macOS-, Linux-, Android- und Docker-Pakete
- Weitergabe des Katalogs an bekannte Federation-Peers
- gezielter Abruf einzelner Installer statt automatischer Vollübertragung
- chunkweiser Transfer großer Dateien
- Prüfung jedes Chunks und abschließende Gesamtprüfung
- Offline-Nutzung bereits synchronisierter Installationspakete
- getrennte Richtlinien für `software.send` und `software.receive`

## Unterstützte Artefakte

Der Cache ist für fertige installierbare oder transportierbare Pakete vorgesehen, unter anderem:

- `.exe`
- `.msi`
- `.dmg`
- `.pkg`
- `.AppImage`
- `.deb`
- `.rpm`
- `.apk`
- Docker-Archive als `.tar.gz`
- `.zip` als kontrollierter Transportcontainer

Nicht jede beliebige Datei wird akzeptiert. Dateiname, Typ und Größe werden geprüft, bevor ein Artefakt in den lokalen Cache übernommen wird.

## Warum ein hash-adressierter Cache verwendet wird

Der SHA-256-Wert dient als stabile Identität des tatsächlichen Dateiinhalts. Dadurch kann SimpleOffice4Me dieselbe Binärdatei auch dann eindeutig erkennen, wenn sie über unterschiedliche Wege angeboten wird.

Das hat mehrere Vorteile:

- identische Dateien werden nicht mehrfach gespeichert
- ein Katalogeintrag kann eindeutig auf einen konkreten Inhalt zeigen
- Übertragungsfehler werden zuverlässig erkannt
- ein Peer kann vor dem Download prüfen, ob der Inhalt bereits lokal vorhanden ist
- Dateinamen allein müssen nicht als Vertrauensanker dienen

## Warum Metadaten vor den eigentlichen Binärdaten übertragen werden

Installationspakete können sehr groß sein. Eine Federation sollte deshalb nicht automatisch mehrere Gigabyte übertragen, nur weil ein Peer grundsätzlich Software empfangen darf.

Stattdessen wird zuerst nur der Katalog angeboten. Der Empfänger entscheidet anschließend, welches konkrete Artefakt lokal benötigt wird. Das spart Bandbreite und Speicher und verhindert überraschende Hintergrundübertragungen.

## Chunkweiser Transfer

Große Binärdateien werden in überprüfbare Blöcke zerlegt. Der Empfänger kann bereits vorhandene gültige Chunks wiederverwenden und nur fehlende oder beschädigte Teile erneut laden.

Nach jedem einzelnen Chunk wird dessen Hash geprüft. Nach Abschluss wird zusätzlich der Hash der gesamten Datei verifiziert.

### Warum zwei Prüfebenen sinnvoll sind

Eine reine Gesamtprüfung würde einen Fehler erst nach dem vollständigen Download erkennen. Die Chunk-Prüfung erkennt Schäden früher und ermöglicht Wiederaufnahme. Die abschließende Gesamtprüfung stellt sicher, dass Reihenfolge und vollständiger Inhalt trotzdem exakt stimmen.

## GitHub Actions als Quelle

GitHub Actions bleibt eine mögliche Quelle für fertige Builds. Die heruntergeladenen Actions-Archive werden aber nur als Transportcontainer behandelt. Relevante Installer werden daraus einzeln geprüft und anschließend in den eigenen Offline-Cache übernommen.

Das ist wichtig, weil der dauerhafte lokale Bestand nicht von der Lebensdauer eines Actions-Artefakts abhängen soll.

## Warum der Cache unabhängig von GitHub funktionieren muss

Self-Deploy soll auch in folgenden Situationen funktionieren:

- Internet ist vorübergehend ausgefallen
- GitHub ist nicht erreichbar
- ein Zielsystem hat absichtlich keinen direkten Internetzugang
- ein bereits getestetes Installationspaket soll später identisch wiederverwendet werden
- mehrere interne Instanzen sollen denselben freigegebenen Stand nutzen

Sobald ein Artefakt lokal vorhanden und geprüft ist, wird für dessen weitere Verwendung kein GitHub-Zugriff mehr benötigt.

## Federation-Modell

Software-Verteilung bleibt explizit richtliniengesteuert. Ein Server kann Software anbieten, ohne selbst eingehende Software akzeptieren zu müssen. Umgekehrt kann eine Instanz als Sammel- oder Backup-Ziel dienen.

Die getrennten Richtungen `software.send` und `software.receive` passen zum allgemeinen Federation-Grundsatz von SimpleOffice4Me: Bekanntschaft zwischen zwei Instanzen bedeutet nicht automatisch vollständigen gegenseitigen Zugriff.

## Warum vorhandene Artefakte nicht ungefragt ersetzt werden

Ein bereits gecachtes Artefakt mit einem bestimmten Hash repräsentiert einen exakt bekannten Inhalt. Neue Angebote dürfen diesen Inhalt nicht still verändern. Änderungen führen stattdessen zu einem neuen Hash und damit zu einem neuen eindeutigen Artefakt.

Damit ist ein späterer Rollback oder eine reproduzierbare Installation möglich.

## Integritäts- und Pfadschutz

Bei der Übernahme werden Dateiname, Dateityp, Größe und Hash validiert. Archive werden so behandelt, dass keine unerwarteten Pfade außerhalb des vorgesehenen Arbeitsbereichs entstehen können.

Downloads aus externen Quellen werden nur über HTTPS akzeptiert. Weiterleitungen werden geprüft, bevor Daten übernommen werden.

## Warum Weiterleitungen besonders behandelt werden

Download-Endpunkte von Build-Systemen liefern häufig zunächst nur eine kurzlebige Weiterleitungs-URL. Diese Zieladresse muss separat validiert werden. Gleichzeitig dürfen Header, die nur für die ursprüngliche Quelle bestimmt sind, nicht automatisch an einen anderen Host weitergereicht werden.

Deshalb verwendet der Installer-Sync einen kontrollierten Redirect-Pfad statt eines unbeschränkten Standard-Downloads.

## Admin-Oberfläche

Im Self-Deploy-/Software-Bereich können Administratoren:

- den lokalen Installer-Katalog sehen
- Dateien manuell in den Cache übernehmen
- vorhandene geeignete Actions-Artefakte importieren
- lokale Installer herunterladen
- nicht mehr benötigte Artefakte löschen
- von Federation-Peers angebotene Pakete erkennen und gezielt übernehmen

## Verhalten bei Ausfällen

Ein Fehler beim GitHub-Import darf vorhandene lokale Artefakte nicht unbrauchbar machen. Ebenso soll ein unterbrochener Peer-Transfer keinen scheinbar vollständigen Installer veröffentlichen.

Temporäre Dateien bleiben deshalb vom produktiven Cache getrennt. Erst nach erfolgreicher Integritätsprüfung wird ein Artefakt als verfügbar übernommen.

## Auswirkungen für Betreiber

Der neue Cache benötigt zusätzlichen Speicherplatz, reduziert dafür aber die Abhängigkeit von externen Build-Diensten. Besonders bei mehreren Instanzen oder großen Installationsdateien sollte der verfügbare Speicher überwacht werden.

Da nicht automatisch der gesamte Katalog übertragen wird, kann die eigentliche Binärdatenmenge gezielt gesteuert werden.

## Auswirkungen für Entwickler

Änderungen in diesem Bereich müssen folgende Eigenschaften erhalten:

- Dateityp-Allowlist bleibt explizit
- Größenlimits werden vor und während Downloads geprüft
- Hash-Prüfungen dürfen nicht übersprungen werden
- temporäre und veröffentlichte Dateien bleiben getrennt
- Federation-Rechte bleiben richtungsabhängig
- externe Downloads bleiben auf sichere Ziele begrenzt
- bestehende Offline-Artefakte funktionieren ohne Netzwerk

## CI und Sicherheitsprüfungen

Die Implementierung wird zusammen mit den normalen Python-, Dependency- und Security-Prüfungen getestet. Zusätzliche Tests decken insbesondere ab:

- Hash-Adressierung
- Dateitypprüfung
- Hashfehler
- Peer-Angebote
- Import eines Actions-Archivs
- Löschen aus dem Cache
- sichere Behandlung externer Download-Ziele

Während der Integration wurde außerdem eine fehlende Tabellen-Semantik in der neuen Admin-Oberfläche gefunden und korrigiert. Dadurch bleibt die neue Oberfläche mit den vorhandenen Accessibility-Prüfungen kompatibel.

## Entscheidungen und Gründe

| Entscheidung | Warum |
| --- | --- |
| lokaler Offline-Cache | Installationen bleiben von GitHub-Verfügbarkeit unabhängig |
| SHA-256 als Identität | Inhalt ist eindeutig und prüfbar |
| Katalog zuerst, Binärdaten später | keine ungeplanten Großtransfers |
| Chunk-Transfer | große Dateien können geprüft und fortgesetzt werden |
| Gesamtprüfung nach Transfer | garantiert vollständige Dateiidentität |
| getrennte Send-/Receive-Policy | Federation bleibt asymmetrisch konfigurierbar |
| Actions-Archive nur als Transportcontainer | lokale Verfügbarkeit hängt nicht von deren Ablaufzeit ab |
| HTTPS und Redirect-Prüfung | externe Downloads bleiben kontrolliert |
| temporärer Bereich vor Veröffentlichung | unvollständige Downloads werden nicht als gültig angeboten |
