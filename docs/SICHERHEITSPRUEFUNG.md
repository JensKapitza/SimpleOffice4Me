# Sicherheitsprüfung je Änderung und je Repository-Stand

## Beim Implementieren

1. **Analyse und Planung.** Einstieg, Akteur, Schutzgut und nächste Vertrauensgrenze anhand der [Projektpolicy](../SECURITY.md) benennen. Das gewünschte Verhalten bei verweigertem Zugriff, ungültigen Daten und Teilfehlern vor der Änderung festlegen.
2. **Implementierung.** Vorhandene Autorisierungs-, Speicher- und Validierungsgrenzen wiederverwenden. Alternative Eingänge wie Hintergrundjobs, Downloads und Protokollrouten einbeziehen.
3. **Tests und Soll-Ist-Vergleich.** Einen erlaubten Ablauf und die relevanten Ablehnungen ausführen. Je nach Grenze gehören ein zweiter Benutzer, ein widerrufener Zugriff, ein Größenlimit oder ein fehlgeschlagener Teilschritt dazu. Bei Ablehnung auch Datenbestand und Nebenwirkungen prüfen.
4. **Zusätzliches Sicherheitsreview.** Ein Agent liest den finalen Diff Zeile für Zeile samt erforderlichen Aufrufern, Datenempfängern und Fehlerpfaden. Bei mehreren Agenten übernimmt das möglichst jemand außerhalb der Implementierung. Bei Einzelarbeit einen getrennten Reviewdurchgang dokumentieren. Neue Erkenntnisse fließen in Tests und gegebenenfalls das Bedrohungsmodell zurück.
5. **Abschluss.** Im PR oder bestehenden Arbeitsprotokoll stehen geprüfter Commit, betroffene Grenze, erwartetes und beobachtetes Verhalten, ausgeführte Tests und Restfragen. Für reine Textänderungen genügen deren tatsächliche Auswirkungen, etwa geänderte Berechtigungsempfehlungen oder sensible Beispiele.

## Vollständiger Scan in fortsetzbaren Paketen

- Den Checkout mit `git rev-parse HEAD` festschreiben. `git ls-tree -r -z --full-tree HEAD` liefert das Inventar einschließlich versteckter Pfade, Dateimodi und Objekt-IDs. Scanner-Image, Optionen und tatsächlich verfügbare Testumgebung protokollieren. Ein historischer Commit oder die Bezeichnung `main` allein identifiziert keinen neuen Lauf.
- **Zuerst die folgenreichen Übergänge lesen.** Mit Identität, Rechten, Dokumentzugriff, Protokollen und Hostaktionen beginnen. Danach Parser, Netzwerkzugriffe, übrige Anwendung, Clients, Templates, Tests, Build-, Installations-, Update- und CI-Dateien sowie Dokumentation bearbeiten. Die Reihenfolge verändert den Umfang nicht.
- **Je Paket ein Register führen.** Eine Zeile enthält Revision, Pfad, Blob-ID, inklusive Zeilenbereiche, Prüfer, geprüfte Eigenschaft, Beleg, Ergebnis und offene Abhängigkeiten. Zusammenhängende Zeilen dürfen einen gemeinsamen Nachweis haben. Nach einer Unterbrechung beim ersten offenen Bereich fortsetzen. Querverweise auf eine andere Datei zählen dort erst nach eigener Prüfung.
- **Die Quelle tatsächlich lesen.** Eingaben über Normalisierung, Rechteprüfung und Verarbeitung bis zur Ausgabe oder Zustandsänderung verfolgen. Bei Bedingungen auch Ablehnung, Ausnahme und Wiederholung lesen. Verwendete Verträge an mindestens einem echten Aufrufer und der tatsächlichen Implementierung abgleichen. Automatisierte Treffer, Tests und Linter ergänzen diese Prüfung. Eine Suche nach Schlüsselwörtern belegt keine Zeilenabdeckung.
- **Den Nenner erhalten.** Jede versionierte Datei erhält einen Status. Bei generiertem Text Herkunft, Generator und Umfang der direkten Prüfung getrennt ausweisen. Bei Binärdateien Herkunft, Integritätsbezug und Ladepfad erfassen. Bei Symlinks Ziel und Verwendung prüfen, bei Submodulen den referenzierten Commit erfassen. Unlesbares oder unzugängliches Material bleibt offen. Ausschlüsse brauchen Begründung und eine ausdrücklich bestätigte Entscheidung.
- **Abschluss rechnerisch prüfen.** Je Textdatei die erfassten Intervalle vereinigen und mit Zeile 1 bis Dateiende im Git-Blob vergleichen, einschließlich einer letzten Zeile ohne Zeilenumbruch. Leere Dateien separat erfassen. Dateianzahl mit dem Inventar abgleichen. Geprüfte, offene und begründet ausgenommene Bereiche getrennt zählen. Ein Vollscan mit Lücken erhält den Status „teilweise geprüft“.
- **Bei einer neuen Revision** geänderte Blobs und betroffene Aufrufer erneut prüfen. Auch bei gleichem Blob kann sich durch eine neue Route, Konfiguration oder Abhängigkeit die Sicherheitsbewertung ändern. Ergebnisse nur mit dokumentiertem Vergleich übernehmen.

Statische Prüfung und ausgeführte lokale Tests separat ausweisen. Ausschließlich synthetische Daten und lokale Fixtures verwenden. Befunddetails mit sensiblen Daten gehören in den [vertraulichen Meldeweg](SECURITY.md).

## Optionale Komponenten und Modell-Downloads

- Eine Installationsaktion ist eine privilegierte Serveraktion: Route erfordert Administratorrolle, Anmeldung, CSRF und eine kleine Formulargrenze. Der Client sendet nur einen internen Feature-Namen aus einer Allowlist, keine URL, Version, Befehlszeile oder Dateipfad.
- Downloadquelle, Versionsbezug, Maximalgröße und SHA-256 kommen aus dem serverseitigen Manifest. Redirects und Proxys sind je nach Komponente begrenzt; nachgeladene Daten werden vor Freigabe geprüft.
- Downloads bleiben bei nicht genügend freiem Speicher, ungültiger Prüfsumme, zu großer Antwort oder Abbruch unvollständig und werden aus temporären Verzeichnissen entfernt. Fertige Browsermodelle werden in anwendungsverwaltete private Datenbereiche unter `.simpleoffice-meta` gestellt.
- `app.component_runtime.resolve_command()` akzeptiert nur bekannte Programme, bevorzugt Systeminstallationen und prüft bei verwalteten Programmen reguläre Dateien, Zielverzeichnis, Ausführbarkeit sowie den im privaten Manifest gebundenen Datei-Hash. Diese Prüfung ist keine Signatur und schützt nicht vor einem Angreifer, der bereits denselben lokalen Benutzer kontrolliert.
- Archive werden nicht anhand clientgesteuerter Pfade entpackt. Jede Extraktion braucht separat geprüfte Mitgliedsnamen, Typen, Größen und Zielpfade. Promotion einer Installation muss den letzten funktionsfähigen Stand bei Fehlern erhalten.
- APK, Termux, Docker, Linux, Windows und macOS sind getrennte Laufzeitziele. Eine Linux-Datei oder ein Python-Wheel darf nicht als Android-APK-Runtime erscheinen. Eine Plattform erhält den Status „unterstützt“ erst nach festem Artefakt, Lizenzprüfung, Integritätstest und einem echten Smoke-Test der Funktion.
- Modelle bleiben optional und werden nur nach ausdrücklicher Nutzeraktion geladen. Ein Setup-Status darf lediglich das Vorhandensein oder die Prüfung belegen; Audio, OCR, Kamera, Spracheingaben und Dokumente werden für den Download nicht übertragen.
- Bei fehlendem Binary müssen alle Verbraucher denselben Resolver benutzen. Eine Anzeige „gefunden“ ist kein Nachweis, dass Codec, Audiogerät, Berechtigung oder Modell tatsächlich funktionieren. Smoke-Tests müssen die erzeugte Ausgabe prüfen.

## Erster Prüfstand · Dokumenttransfer

Revision `39b4df619b225af2b84a8c690c6f02afdb688088`, 5.333 versionierte Einträge. Review durch Codex. Die zwei Dateien unten wurden vollständig gelesen. Die übrigen 5.331 Einträge sind in diesem Durchgang offen.

| Datei und Git-Blob | Gelesene Zeilen | Prüfung und Ergebnis |
| --- | --- | --- |
| `app/document_transfer_access.py` · `148dd3e34cef0f281896121f6ed9f70b3b7874fc` | 1–11, 12–15, 16–20 | Importe und Vertrag, Sperren für private und zurückgehaltene Dokumente, Auswertung des Scanstatus. Ein fehlender Scan erlaubt die weitere Prüfung. Das ist von einem bestätigten sauberen Scan zu unterscheiden. Die Leseberechtigung muss der Aufrufer prüfen. |
| `app/documents_routes_transfer.py` · `968bd654dd1cb36ae67f11df0e3f06461fde1ff5` | 1–21, 22–37, 38–46, 47–65, 66–83, 84–94, 95–99 | Gemeinsamer Zugriffspfad prüft Dokumentstatus, logisches Leserecht und Speicherstatus. Beide Routen verlangen Anmeldung. Die Dateieinbettung begrenzt die Länge und vergleicht Version und kopierte Bytezahl. Die Vorbereitungsantwort setzt `private, no-store`. Beim Download wird der Zugriff erneut geprüft. |

**Dynamischer Beleg.** `python -m unittest tests.test_document_transfer_routes tests.test_chat_document_transfer` bestand mit zwölf Tests, darunter verweigerter Dokumentzugriff, infizierte Testmetadaten und Rechteentzug vor dem Chatdownload. **Nächstes Paket:** Implementierungen hinter der Speicher- und Rechteprüfung, geschützter Download, Adresswahl und Chat-Anhang. Deren interne Zeilen sind im obigen Umfang noch offen.

## Folgeprüfung · Downloadadresse und Dateipaket

Codex, statische Zeilenprüfung auf `3a374f94da0f7e5b2a01ca4449d565438c507388`. Das Inventar dieser Revision enthält 5.340 Einträge. In diesem Paket wurden vier Dateien mit zusammen 196 Zeilen vollständig gelesen. Die Zählung des ersten Pakets bleibt an dessen Revision gebunden.

| Datei und Git-Blob | Inklusive Zeilen | Beobachtung |
| --- | --- | --- |
| `app/document_transfer_origin.py` · `ddf44762b0278f7b6331fd1c1df8fb9568d03046` | 1–11, 12–21, 22–37 | Die Funktion übernimmt eine bereits extern erscheinende Request-Adresse. Bei localhost, Loopback oder unbestimmter IP zieht sie den laufenden Proxy heran. Sie prüft Schema, Host, Benutzerinfo, Pfad, Query und Fragment, bevor sie die Authority austauscht. Die Vertrauenswürdigkeit des ursprünglichen Request-Hosts hängt vom Deployment ab. |
| `app/desktop_proxy_status.py` · `7c2e565ac2f8eb4619783cc0e28a038d4b0930ba` | 1–18, 19–40, 41–47 | Vorrang hat der Status des laufenden Prozesses. Der Dateifallback prüft unter anderem Schema und Zeitstempel und verwirft Meldungen älter als 20 Sekunden. Die JSON-Datei wird vor diesen Prüfungen vollständig eingelesen. Schreibrechte und Größenbegrenzung beim Erzeuger bleiben offene Prüfpunkte. |
| `app/document_transfer_packet.py` · `6e22910d4397fedf3f5bc66e7b400d4224e64651` | 1–18, 19–25, 26–37, 38–57 | Der Sender bildet einen gekürzten ASCII-Basisnamen. Der Python-Decoder begrenzt bei Standardparametern die Eingabe, prüft Metadaten, Base64, Bytezahl und SHA-256. Der Digest umfasst die Dateibytes. Absenderidentität und Dateiname werden dadurch nicht authentifiziert. |
| `static/js/document_transfer_packet.js` · `73f290a445997ce23ef2079637265bd338c6b143` | 1–18, 19–30, 31–40, 41–55 | Der tatsächlich im Browser verwendete Empfänger prüft Größe und Digest vor Freigabe des Downloads. Ein Revisionszähler verwirft verspätete Prüfergebnisse nach einer Eingabeänderung. Statusmeldungen verwenden Textausgabe. Erst ein Klick erzeugt den Blob-Download. Die Namensprüfung im Browser erlaubt mehr Randzeichen als die Python-Normalisierung. |

**Lokale Nachweise.** Fünf Python-Tests in `tests.test_document_transfer_origin` und `tests.test_document_transfer_packet` sowie drei Node-Tests in `tests/document_transfer_packet.test.cjs` bestanden. Die Node-Tests verwenden einen simulierten DOM. Die Ergebnisse belegen weder die Schreibrechte im installierten System noch die Host-Konfiguration eines realen Proxys.

**Übergabe.** Beim nächsten Paket zuerst den Schreiber von `desktop-proxy-status.json`, die Herkunft weitergeleiteter Hostheader und die konfigurierbaren Hostgrenzen verfolgen. Danach die Speicherprüfung und den Chatdownload aus dem ersten Paket fortsetzen. Die abweichenden Dateinamensregeln über einen gemeinsamen Satz zulässiger Namen prüfen. Diese Fragen sind offen; eine ausnutzbare Schwachstelle wurde in diesem Paket nicht bestätigt.
