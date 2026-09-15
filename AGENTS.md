# AGENTS.md

Diese Datei enthält verbindliche Arbeits- und Entwicklungsregeln für AI-Agenten und automatisierte Coding-Werkzeuge im Repository `JensKapitza/SimpleOffice4Me`.

Sie gilt für das gesamte Repository, sofern in einem Unterverzeichnis keine speziellere `AGENTS.md` mit engerem Geltungsbereich vorhanden ist.

## 1. Prioritäten und Geltungsbereich

Regeln sind in dieser Reihenfolge anzuwenden:

1. Sicherheit und Datenintegrität
2. Korrektheit, Kompatibilität und Produktionsreife
3. Testbarkeit und CI-Stabilität
4. Wartbarkeit und einheitliche Architektur
5. Dokumentation und Benutzerfreundlichkeit

Bestehende funktionierende Implementierungen zuerst verstehen und gezielt verbessern. Ein Architekturwechsel ist nur zulässig, wenn ein konkreter technischer Grund dokumentiert ist.

Für C#-/MetaBridge-Arbeiten gelten zusätzlich, sofern diese Dateien im jeweiligen Repository oder Teilbaum vorhanden sind, als verbindliche Referenz:

- `instruction.md`
- `BrabenderCodeAnalysis.ruleset`
- vorhandene `README`- und `docs/`-Dokumentation
- vorhandene Solution-, Projekt-, Build- und Testkonfiguration

## 2. Arbeitsweise des Agenten

Der Agent arbeitet pragmatisch, autonom, batch-orientiert und root-cause-orientiert. Lösbare Aufgaben werden direkt umgesetzt, statt unnötig lange Planungsphasen zu erzeugen.

Nicht verhandelbare Prinzipien:

1. Probleme lösen, nicht kaschieren.
2. Root Cause vor Symptom-Fix.
3. Stabilität vor Cleverness.
4. Sicherheit und Regressionen priorisieren.

Daraus folgt:

- Keine globalen Warning-Suppressions, pauschalen `NoWarn`-Schalter oder Quick Wins als Standardlösung.
- Keine riskanten Umbauten ohne klaren technischen Mehrwert.
- In kleinen, klaren und nachvollziehbaren Batches arbeiten.
- Betroffene Dateien und möglichst konkrete Zeilenstellen nennen.
- Offene Punkte systematisch abbauen und nicht unbegründet liegen lassen.
- Annahmen und verbleibende Unsicherheiten explizit benennen und mit der vernünftigsten risikoarmen Option fortfahren.
- Keine halbfertigen TODO-Lösungen oder Debug-Hacks als dauerhafte Lösung zurücklassen.

## 3. Technische Basis

### Python / SimpleOffice4Me

- Python: mindestens 3.10.
- CI: mindestens Python 3.10 und Python 3.14.
- Flask 3.x bleibt die Web-Basis.
- Neue Abhängigkeiten vermeiden. Notwendige Laufzeitabhängigkeiten sauber in `pyproject.toml` pflegen.
- Optionale Funktionen sollen nach Möglichkeit optionale Abhängigkeiten bleiben.
- Entwicklung und Produktion klar getrennt halten.

### C# / MetaBridge

Diese Regeln gelten nur dort, wo C#-/MetaBridge-Projekte tatsächlich vorhanden sind:

- Bestehende Target Frameworks nicht pauschal ändern; Upgrades schrittweise entlang der Projektabhängigkeiten planen und testen.
- Bestehende Layer und Verantwortlichkeiten erhalten: Web/UI, Business-Logik, Services, Persistenz, gemeinsame Bibliotheken und Hardware-Abstraktionen nicht ohne Grund vermischen.
- Gemeinsame Bibliotheken auf Seiteneffekte in abhängigen Projekten prüfen.
- Drittanbieter-Code nur auf ausdrückliche Anweisung ändern.
- Vorhandene APIs und Verträge nur mit klarer Begründung ändern; Breaking Changes dokumentieren.
- Vorhandene Projekttechnologien wie MongoDB, Razor Views, Gulp/Sass/Less, log4net, NUnit, Coverlet und interne NuGet-Feeds berücksichtigen, sofern sie im betroffenen Projekt verwendet werden.

## 4. Codegröße und Struktur

Für Python gelten die durch CI überwachten Grenzwerte:

- Python-Quelldateien maximal 1000 Zeilen.
- Funktionen/Methoden maximal 300 Zeilen.

Zusätzlich:

- Größere Funktionen in fachlich sinnvolle kleinere Funktionen, Klassen oder Module aufteilen.
- Keine neuen Monster-Module oder monolithischen Controller erzeugen.
- Gemeinsame Logik zentralisieren; Copy-and-paste vermeiden.
- Vor neuen Abstraktionen zuerst nach bestehender vergleichbarer Logik suchen.
- Änderungen müssen zum vorhandenen Stil, zu Hilfsfunktionen und zur bestehenden Architektur passen.
- Seiteneffekte möglichst begrenzen und Datenflüsse klar halten.

### C#-Konventionen

Sofern im betroffenen Projekt vorhanden:

- Bestehende Naming-, Namespace-, Datei- und Pattern-Konventionen übernehmen.
- Neuron-Konventionen berücksichtigen, wenn das Projekt sie verwendet: `mPrefix`, `_Prefix`, PascalCase, SRP und Boy-Scout-Regel.
- Vorhandene gemeinsame Bausteine bevorzugen, z. B. Glia-DI, `Maybe<T>`, Dapper, FluentMigrator und bestehende Konfigurationshierarchien.
- Vorhandene Patterns bevorzugen, z. B. File Assertions, Smart Detection, Scriban-Templates, Service Overrides, Named Tuples, `DataFaker`, parametrisierte Konfiguration, Partial Files und vorhandene Package-Reference-Muster.
- Keine ungenutzten Felder, Variablen, Parameter oder Catch-Blöcke zurücklassen.
- Neue C#-Dateien nur dann mit projektspezifischem UTF-8-BOM/Copyright anlegen, wenn diese Vorgabe im Projekt tatsächlich besteht.

## 5. Sicherheitsregeln

Alle Eingaben sind grundsätzlich nicht vertrauenswürdig.

- Pfade, Dateinamen, Uploads, externe URLs, XML, HTML, Header und Konfigurationswerte validieren und sicher verarbeiten.
- Pfad-Traversal und unbeabsichtigte Dateisystemzugriffe verhindern.
- TLS- und Zertifikatsprüfung niemals pauschal deaktivieren. Lokale Self-Signed-Zertifikate nur über explizite, dokumentierte Trust-/CA-Konfiguration unterstützen.
- Keine Secrets, Tokens, privaten Schlüssel oder Zugangsdaten in Quellcode, Tests, Konfigurationsbeispiele oder Repository-Dateien schreiben.
- Keine Credentials in Query-Parametern, Logs oder Fehlerberichten ablegen.
- Berechtigungsprüfungen serverseitig erzwingen; UI-Prüfungen allein reichen nicht.
- Shell-Aufrufe ohne Shell-Interpolation bevorzugen; ungeprüfte Nutzereingaben niemals direkt in Shell-Kommandos einsetzen.
- Externe URLs und Redirects validieren, wenn dadurch Serverzugriffe ausgelöst werden.
- HTML-, Razor- und JavaScript-Ausgaben kontextgerecht encoden; serverseitige Validierung bleibt erforderlich.
- Fehlerberichte dürfen keine vollständigen fachlichen Nutzdaten enthalten. Zentrales Fehlerreporting übermittelt nur minimale technische Diagnosedaten.
- `pip-audit` ist als Dependency-Sicherheitsprüfung Bestandteil der CI.
- Die CRA-Prüfung über `tools/cra_check.py` ist Bestandteil der CI.
- Für Releases ist ein SBOM zu erzeugen.
- Security-Checks, Authentifizierung, Autorisierung, Validierung und Zertifikatsprüfung dürfen nicht für schnelle Workarounds entfernt oder abgeschwächt werden.

## 6. Fehlerbehandlung und Diagnose

- Fehler niemals still verschlucken.
- Keine pauschalen `except Exception: pass`-Muster.
- Breite Exceptions nur mit gezielter Behandlung, ausreichender Diagnose und sinnvoller Fehlergrenze verwenden.
- Benutzer erhalten verständliche, handlungsorientierte Fehlermeldungen.
- Logs enthalten die für Diagnose und Reproduktion nötigen technischen Informationen, aber keine unnötigen Nutzdaten oder Secrets.
- Netzwerkfehler, Timeouts, ungültige Eingaben, fehlende Konfiguration und optionale Komponenten ausdrücklich behandeln.
- Fehlerzustand und Ursache soweit möglich getrennt darstellen.
- Fehlerverhalten muss reproduzierbar und diagnostizierbar bleiben.

## 7. Warning- und Build-Policy

Warnungen werden ursächlich reduziert. Reihenfolge:

1. Build-Fehler
2. Security- und Runtime-Warnungen
3. Logik- und Concurrency-Warnungen
4. Stilwarnungen, sofern risikoarm und mit vertretbarem Aufwand lösbar

Keine pauschalen Unterdrückungen verwenden. Eine Ausnahme ist nur zulässig, wenn sie ausdrücklich gefordert oder technisch notwendig, lokal begrenzt und dokumentiert ist.

### MetaBridge-Analyzer

Wenn `BrabenderCodeAnalysis.ruleset` vorhanden ist, ist diese Datei maßgeblich. Insbesondere gelten dann die dort definierten Schweregrade. Falls die Regeln dort entsprechend konfiguriert sind, sind `CA1823`, `C6259` und `SX1101` ohne unbegründete Unterdrückung zu behandeln. Deaktivierte `SA...`-Regeln dürfen nicht eigenmächtig als neue Pflichtregeln eingeführt werden.

## 8. Datenintegrität und Migrationen

- Keine stillen Datenverluste verursachen.
- Migrationen müssen vorhandene Nutzerdaten erhalten.
- Kritische Änderungen möglichst atomar durchführen.
- Für kritische mehrstufige Operationen Rollback oder sichere Wiederanlaufstrategie vorsehen.
- Prüfsummen oder Hashes verwenden, wenn Integrität bei Übertragung, Import, Export oder Update relevant ist.
- Bestehende Audit-, Historien- und Versionsmechanismen nicht umgehen.
- Bei Merge-/Deduplizierungsfunktionen unterschiedliche Nutzdaten erhalten; nicht blind einen Datensatz bevorzugen.
- Programmcode und Laufzeitdaten getrennt halten.
- Updates dürfen insbesondere `.venv`, `instance`, `.simpleoffice-history`, `.simpleoffice-control`, Benutzerdateien, Datenbanken, Audit-Historien und lokale Konfiguration nicht unbeabsichtigt ersetzen oder löschen.

## 9. Konfiguration und Betrieb

- Vergleichbare Dienste gleichartig konfigurieren.
- Sinnvolle, sichere Defaults bereitstellen.
- Installation und Erstkonfiguration ohne Spezialwissen ermöglichen.
- Fehlende optionale Komponenten klar und verständlich anzeigen.
- Keine unnötigen Pflichtabhängigkeiten einführen.
- Keine hartcodierten lokalen IP-Adressen, Pfade oder Zugangsdaten verwenden, wenn diese konfigurierbar sein müssen.
- Start, Stop und Neustart zuverlässig gestalten; Neustarts dürfen keine Daten beschädigen.
- Status- und Fehlerdarstellung über Dienste hinweg vereinheitlichen.

## 10. Mini-Services: Best-of-all als Mindeststandard

Für vergleichbare Mini-Services gilt das **Best-of-all-Prinzip**:

> Die beste vorhandene Implementierung innerhalb der Mini-Services definiert den Mindeststandard für alle vergleichbaren Mini-Services.

Das gilt mindestens für:

- Installation und Setup
- Start, Stop und Neustart
- Autostart/Worker-Integration
- Konfiguration
- Statusanzeige und Health-Checks
- Fehlerbehandlung und Logging
- UI-Struktur und Bedienlogik
- Abhängigkeitserkennung
- Dokumentation
- Sicherheitsniveau
- Testabdeckung

Jeder Mini-Service soll ohne Spezialwissen nutzbar sein, sich konsistent verhalten und wie ein produktionsreifer Dienst wirken, nicht wie ein isoliertes Experiment.

## 11. Protokolle und Plattformen

- Bestehende Schnittstellen nicht unnötig brechen.
- WebDAV, CalDAV, CardDAV, HTTP, iCalendar/iTIP, VTODO, SFTP, SIP und andere standardisierte Protokolle standardkonform implementieren.
- RFC-Vorgaben nicht durch proprietäre Abkürzungen ersetzen, wenn Interoperabilität darunter leidet.
- Client-Kompatibilität mit Thunderbird, LibreOffice, Desktop-Dateimanagern und vergleichbaren Clients erhalten.
- Protocol Edge Cases, Preconditions und korrekte Statuscodes berücksichtigen.
- Bei plattformübergreifend vorgesehenen Bereichen Desktop, Docker, Windows, Linux und Android berücksichtigen.
- Bewusste Kompatibilitätsabweichungen und bekannte Einschränkungen dokumentieren.

## 12. Produktionsreife

Eine Funktion ist nicht fertig, nur weil der Happy Path funktioniert.

Mindestens berücksichtigen:

- fehlende Konfiguration
- ungültige und leere Eingaben
- doppelte Daten
- Netzwerkfehler und Timeouts
- Neustart während laufender Operationen
- nicht verfügbare optionale Programme
- Dateisystem- und Berechtigungsfehler
- konkurrierende Zugriffe
- Wiederholungsversuche
- sichere Wiederaufnahme oder sauberer Abbruch

Keine Endlosschleifen, hängenden Worker oder unbegrenzt wachsenden Queues/Logs ohne Schutz einbauen.

## 13. Tests, CI und Qualitätsgates

Vor Abschluss relevante Prüfungen ausführen.

Standardprüfungen:

```bash
python tools/check_project_policy.py .
python tools/check_file_size.py . --limit 1000
python tools/check_function_size.py app tools --limit 300
python -m compileall -q app tools
python -m unittest discover -s tests -v
```

Security-/Release-Prüfungen:

```bash
python -m pip_audit
python tools/cra_check.py
python tools/generate_sbom.py
python -m json.tool artifacts/sbom.cdx.json > /dev/null
```

Regeln:

- Neue relevante Logik mit Tests absichern.
- Fehlerfälle, ungültige Daten, fehlende Konfiguration, Neustarts und Netzwerkfehler testen, wenn sie zum Änderungsumfang gehören.
- Regressionen bestehender Funktionen nicht akzeptieren.
- Tests nicht entfernen oder abschwächen, um CI grün zu bekommen.
- Wenn ein Test wegen bewusst geänderter Spezifikation angepasst wird, muss die neue Erwartung fachlich begründet sein.
- CI muss mindestens Python 3.10 und Python 3.14 abdecken.
- Dateigrößen- und Funktionsgrößen-Gates nicht umgehen.

### C#- und MetaBridge-Prüfungen

Nur anwenden, wenn entsprechende Projekte vorhanden sind:

- Den kleinsten passenden Build ausführen; bei gemeinsam genutzten Bibliotheken anschließend betroffene abhängige Projekte prüfen.
- Mindestens die betroffene Solution oder den passenden Solution Filter bauen und relevante NUnit-Tests ausführen.
- Web- und Desktop-Anteile getrennt prüfen, wenn nur einer der Bereiche betroffen ist.
- Bei Datenbankänderungen Migrationen, Rückwärtskompatibilität, vorhandene Nutzerdaten und Rollback testen.
- Bei Hardware-, Netzwerk- oder Protokolländerungen Fehler, Timeouts, Neustart und fehlende Geräteverbindungen testen.
- Coverage- und Analyzer-Warnungen als Qualitätsindikatoren behandeln; Prüfungen nicht durch Deaktivieren oder Entfernen umgehen.

## 14. Dokumentation

Dokumentation ist Bestandteil der Implementierung.

- Größere neue Funktionen dokumentieren.
- `README` und passende Dateien unter `docs/` aktuell halten.
- Installation, Konfiguration, Betrieb, Fehlerverhalten und bekannte Einschränkungen beschreiben.
- Sicherheitsgrenzen und relevante Architekturentscheidungen erklären.
- Nicht nur das Was, sondern insbesondere das Warum dokumentieren.

## 15. UI/UX

- Bestehende Bedienmuster wiederverwenden.
- Gleiche Funktionen sollen gleich aussehen und sich gleich verhalten.
- Technische Fehlermeldungen nicht ungefiltert als Benutzertext anzeigen.
- Lade-, Fehler-, Leer- und Erfolgszustand berücksichtigen.
- Aktionen mit Datenverlust oder kritischer Wirkung klar kennzeichnen bzw. bestätigen lassen.
- Keine versteckten Nebenwirkungen bei normalen UI-Aktionen.

## 16. Review-Policy

Bei Reviews primär Findings mit echtem Impact melden:

- Bugs
- Sicherheitslücken
- Regressionen
- Ressourcen- oder Concurrency-Probleme

Noise-Nits vermeiden. Jedes Finding enthält nach Möglichkeit:

- Datei und Zeile
- konkretes Risiko
- konkrete Fix-Empfehlung

## 17. Priorisierung mehrerer Aufgaben

1. Roter Build: sofort beheben.
2. Sicherheits- oder Regressionsrisiken: als Nächstes behandeln.
3. Warning-Batches mit hohem Nutzen und geringem Risiko.
4. Danach Cleanup und Stilverbesserungen.

## 18. Arbeitsablauf

Vor dem Implementieren:

1. Betroffenen Code, Tests und angrenzende Funktionen lesen.
2. Vergleichbare vorhandene Lösungen und gemeinsame Hilfslogik identifizieren.
3. Bestehende Architektur, Datenflüsse und Abhängigkeiten verstehen.
4. Bei C#/MetaBridge zuerst `instruction.md`, Ruleset, README, Solution- und Projektdateien lesen, sofern vorhanden.

Beim Implementieren:

1. Kleinste sinnvolle, wartbare Änderung wählen.
2. Root Cause beheben statt Symptome zu kaschieren.
3. Gemeinsame Logik wiederverwenden.
4. Fehler-, Security-, Datenintegritäts- und Randfälle mit implementieren.
5. Tests ergänzen oder aktualisieren.
6. Dokumentation im selben Change aktualisieren, wenn Verhalten oder Betrieb betroffen sind.

Vor Abschluss:

1. Policy-Check ausführen.
2. Syntax/Compile prüfen.
3. Relevante Tests ausführen.
4. Größenlimits prüfen.
5. Security-Auswirkungen prüfen.
6. Datenverlust-/Migrationsrisiken prüfen.
7. Dokumentation prüfen.
8. Keine offenen Debug-Ausgaben, temporären Hacks oder unnötigen TODOs hinterlassen.

## 19. Kommunikation und Ergebnisformat

Rückmeldungen kurz, direkt und ergebnisorientiert halten. Standardformat:

1. Ergebnis zuerst in ein bis zwei Sätzen.
2. Kurze Liste: was geändert wurde, wo, welches Problem dadurch gelöst wurde.
3. Verbleibende Risiken oder nicht durchgeführte Prüfungen ausdrücklich nennen.

Keine langen theoretischen Ausführungen ohne konkreten Umsetzungsbezug und keine unnötigen Wiederholungen.

## 20. Was nicht getan werden soll

Nicht ohne zwingenden, dokumentierten Grund:

- funktionierende Architektur komplett neu schreiben
- Framework wechseln
- parallele Subsysteme für bereits gelöste Aufgaben erstellen
- Zertifikatsprüfung deaktivieren
- Authentifizierung oder Autorisierung umgehen
- Fehler ignorieren
- Daten still verwerfen
- Tests entfernen, um eine Änderung passend zu machen
- Produktionscode mit hartcodierten lokalen Spezialwerten versehen
- sensible Daten loggen
- Datenbanken oder Laufzeitverzeichnisse bei Updates ersetzen
- globale Warning-Suppressions aktivieren
- große Funktionen oder Dateien trotz CI-Grenzen weiter vergrößern

## 21. Definition of Done

Eine Aufgabe gilt für den betroffenen Umfang erst als abgeschlossen, wenn die anwendbaren Punkte erfüllt sind:

- Funktion implementiert
- Root Cause behandelt
- relevante Fehlerfälle behandelt
- bestehende Funktionen nicht unbeabsichtigt gebrochen
- Sicherheits- und Eingabevalidierung berücksichtigt
- keine Secrets oder unsicheren TLS-Workarounds eingeführt
- Datenintegrität, Atomizität und Migrationen berücksichtigt
- Tests vorhanden bzw. angepasst
- Tests erfolgreich
- Code kompiliert
- Größenlimits eingehalten
- CI für Python 3.10 und 3.14 berücksichtigt
- `pip-audit` erfolgreich oder Abweichung begründet dokumentiert
- `tools/cra_check.py` erfolgreich oder Abweichung begründet dokumentiert
- Release-SBOM berücksichtigt
- Dokumentation aktualisiert
- Bedienung konsistent
- keine bekannten kritischen TODOs oder Debug-Hacks offen
- bei C#/MetaBridge: vorhandene Projektinstruktionen, Analyzer, Builds und abhängige Projekte berücksichtigt

## 22. Session-Kurzvorgabe

Für neue Agent-Sessions gilt zusammenfassend:

> Arbeite pragmatisch und root-cause-orientiert. Löse Probleme statt sie zu unterdrücken. Priorisiere Build-Stabilität, Security und Regression-Sicherheit. Arbeite in kleinen, risikoarmen Batches mit konkreten Datei-/Zeilen-Fixes. Kommuniziere kurz und direkt: was geändert wurde, wo, und warum es das Problem löst. Keine unnötigen Refactorings außerhalb des Scopes. Keine globalen Warning-Suppressions ohne explizite Freigabe.
