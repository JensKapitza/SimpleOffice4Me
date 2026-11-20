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

## Erster Prüfstand · Dokumenttransfer

Revision `39b4df619b225af2b84a8c690c6f02afdb688088`, 5.333 versionierte Einträge. Review durch Codex. Die zwei Dateien unten wurden vollständig gelesen. Die übrigen 5.331 Einträge sind in diesem Durchgang offen.

| Datei und Git-Blob | Gelesene Zeilen | Prüfung und Ergebnis |
| --- | --- | --- |
| `app/document_transfer_access.py` · `148dd3e34cef0f281896121f6ed9f70b3b7874fc` | 1–11, 12–15, 16–20 | Importe und Vertrag, Sperren für private und zurückgehaltene Dokumente, Auswertung des Scanstatus. Ein fehlender Scan erlaubt die weitere Prüfung. Das ist von einem bestätigten sauberen Scan zu unterscheiden. Die Leseberechtigung muss der Aufrufer prüfen. |
| `app/documents_routes_transfer.py` · `968bd654dd1cb36ae67f11df0e3f06461fde1ff5` | 1–21, 22–37, 38–46, 47–65, 66–83, 84–94, 95–99 | Gemeinsamer Zugriffspfad prüft Dokumentstatus, logisches Leserecht und Speicherstatus. Beide Routen verlangen Anmeldung. Die Dateieinbettung begrenzt die Länge und vergleicht Version und kopierte Bytezahl. Die Vorbereitungsantwort setzt `private, no-store`. Beim Download wird der Zugriff erneut geprüft. |

**Dynamischer Beleg.** `python -m unittest tests.test_document_transfer_routes tests.test_chat_document_transfer` bestand mit zwölf Tests, darunter verweigerter Dokumentzugriff, infizierte Testmetadaten und Rechteentzug vor dem Chatdownload. **Nächstes Paket:** Implementierungen hinter der Speicher- und Rechteprüfung, geschützter Download, Adresswahl und Chat-Anhang. Deren interne Zeilen sind im obigen Umfang noch offen.
