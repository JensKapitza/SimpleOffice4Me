# AGENTS.md

Diese Datei beschreibt verbindliche Arbeits- und Entwicklungsregeln fuer AI-Agenten und automatisierte Coding-Werkzeuge im Repository `JensKapitza/SimpleOffice4Me`.

Sie gilt fuer das gesamte Repository, sofern in einem Unterverzeichnis keine speziellere `AGENTS.md` mit engerem Geltungsbereich vorhanden ist.

## 1. Grundprinzipien

- Bestehende Architektur nicht ohne konkreten technischen Grund ersetzen.
- Funktionierende Implementierungen bevorzugt verbessern, vereinheitlichen und absichern statt parallel neu zu bauen.
- Keine halbfertigen Workarounds, Debug-Hacks oder dauerhaft versteckten Fallbacks einbauen.
- Aenderungen muessen wartbar, nachvollziehbar und fuer den produktiven Betrieb geeignet sein.
- Bestehende Schnittstellen, Daten und Nutzerablaeufe nicht unnoetig brechen.
- Gemeinsame Logik zentralisieren. Copy-and-paste-Implementierungen vermeiden.
- Keine unnoetigen neuen Abhaengigkeiten einfuehren.

## 2. Python-Basis

- Unterstuetzte Python-Version: `>= 3.10`.
- Der Code muss mindestens unter Python 3.10 und Python 3.14 funktionieren, solange die CI diese Versionen prueft.
- Flask 3.x ist die bestehende Web-Basis und soll nicht ohne zwingenden Grund ersetzt werden.
- Neue Laufzeitabhaengigkeiten muessen in `pyproject.toml` gepflegt werden.
- Optionale Funktionen sollen nach Moeglichkeit optionale Abhaengigkeiten bleiben.

## 3. Codegroesse und Struktur

Die CI erzwingt derzeit folgende Grenzen:

- Python-Quelldateien: maximal 1000 Zeilen.
- Funktionen/Methoden: maximal 300 Zeilen.

Daraus folgen diese Regeln:

- Grosse Module fruehzeitig in fachlich sinnvolle Teilmodule zerlegen.
- Grosse Funktionen in klar benannte Hilfsfunktionen zerlegen.
- Keine neuen Monster-Module oder monolithischen Controller erzeugen.
- Wiederverwendbare Logik in gemeinsame Hilfs- oder Service-Schichten verschieben.
- Seiteneffekte moeglichst begrenzen und Datenfluesse klar halten.

## 4. Tests und Pflichtpruefungen

Vor Abschluss einer Aenderung muessen mindestens die fuer den betroffenen Bereich relevanten Pruefungen ausgefuehrt werden.

Standardpruefungen:

```bash
python tools/check_file_size.py . --limit 1000
python tools/check_function_size.py app tools --limit 300
python -m compileall -q app tools
python -m unittest discover -s tests -v
```

Bei Security-/Release-relevanten Aenderungen zusaetzlich:

```bash
python -m pip_audit
python tools/cra_check.py
python tools/generate_sbom.py
python -m json.tool artifacts/sbom.cdx.json > /dev/null
```

Regeln:

- Neue relevante Logik mit Tests absichern.
- Regressionen bestehender Funktionen nicht akzeptieren.
- Eine Aenderung nicht als fertig markieren, wenn bekannte Tests fehlschlagen.
- Fehlerhafte Tests nicht einfach entfernen oder abschwaechen, um CI gruen zu bekommen.
- Falls ein Test wegen einer bewusst geaenderten Spezifikation angepasst werden muss, muss die neue Erwartung fachlich begruendet sein.

## 5. Fehlerbehandlung und Diagnose

- Fehler nicht still verschlucken.
- Benutzer erhalten verstaendliche, handlungsorientierte Fehlermeldungen.
- Technische Details gehoeren in Logs bzw. Diagnoseinformationen.
- Keine pauschalen `except Exception: pass`-Muster.
- Externe Fehlerquellen wie Netzwerk, Dateisystem, Parser, Fremdprogramme und APIs explizit behandeln.
- Fehlerzustand und Ursache soweit moeglich getrennt darstellen.
- Wiederholbare Fehler sollen ueber stabile Diagnoseinformationen nachvollziehbar sein.
- Zentrales Fehlerreporting bleibt datensparsam; keine vollstaendigen fachlichen Nutzdaten oder Secrets automatisch uebertragen.

## 6. Security

Security darf nicht fuer einen schnellen Workaround deaktiviert werden.

Insbesondere:

- Eingaben grundsaetzlich als nicht vertrauenswuerdig behandeln.
- Pfade gegen Traversal und ungewollte Zugriffe absichern.
- Uploads und Dateitypen validieren.
- HTML/XML und vergleichbare externe Inhalte sicher verarbeiten.
- Keine Secrets, Passwoerter, API-Tokens oder privaten Schluessel in Quellcode, Tests oder Repository-Dateien einchecken.
- TLS-Zertifikatspruefung nicht global oder pauschal deaktivieren.
- Self-Signed- oder lokale Zertifikate ueber explizite Trust-/CA-Konfiguration loesen.
- Shell-Aufrufe ohne Shell-Interpolation bevorzugen; keine ungeprueften Nutzereingaben in Shell-Kommandos einsetzen.
- Externe URLs und Redirects validieren, wenn dadurch Serverzugriffe ausgeloest werden.
- Berechtigungspruefungen nicht nur im UI, sondern serverseitig erzwingen.
- Kritische Operationen auditierbar halten.

## 7. Datenintegritaet

- Keine stillen Datenverluste akzeptieren.
- Migrationen muessen bestehende Nutzerdaten erhalten.
- Schreibende Mehrschritt-Operationen moeglichst atomar ausfuehren.
- Bei kritischen Updates und Dateiersetzungen Rollback beruecksichtigen.
- Hashes/Pruefsummen verwenden, wenn Integritaet bei Import, Export, Update oder Uebertragung relevant ist.
- Bestehende Audit-, Historien- und Versionsmechanismen nicht umgehen.
- Bei Merge-/Deduplizierungsfunktionen unterschiedliche Nutzdaten erhalten; nicht nur einen Datensatz blind bevorzugen.

## 8. Konfiguration und Installation

- Vergleichbare Komponenten sollen vergleichbar konfiguriert werden.
- Sinnvolle Defaults bereitstellen.
- Lokale IP-Adressen, Ports, Pfade und Zugangsdaten nicht unnoetig hartcodieren.
- Fehlende optionale Komponenten klar anzeigen.
- Installation und Erstkonfiguration sollen ohne Spezialwissen moeglich sein.
- Entwicklung, Test und Produktion sauber trennen.
- Konfigurationsfehler frueh und verstaendlich melden.

## 9. Mini-Services: Best-of-all als Mindeststandard

Fuer alle Mini-Services gilt:

> Die beste vorhandene Implementierung innerhalb der Mini-Services ist der Mindeststandard fuer alle vergleichbaren Mini-Services.

Das gilt insbesondere fuer:

- Installation und Setup
- Start, Stop und Neustart
- Autostart/Worker-Integration
- Konfiguration
- Statusanzeige
- Health-Checks
- Fehlerbehandlung
- Logging
- UI-Struktur
- Bedienlogik
- Abhaengigkeitserkennung
- Dokumentation
- Sicherheitsniveau
- Testabdeckung

Ziel:

- Jeder Mini-Service soll ohne Spezialwissen nutzbar sein.
- Dienste sollen sich konsistent verhalten.
- Fehler sollen sichtbar und verstaendlich sein.
- Ein Dienst darf nicht wie ein isoliertes Experiment wirken.
- Vorhandene gute Implementierungen sollen als Vorlage fuer schwachere Dienste dienen.

## 10. Standards und Protokolle

Bei standardisierten Schnittstellen vorhandene Standards und RFCs einhalten.

Das betrifft insbesondere Bereiche wie:

- WebDAV
- CalDAV
- CardDAV
- HTTP
- iCalendar/iTIP
- VTODO
- SFTP
- SIP

Regeln:

- Standards nicht durch proprietaere Abkuerzungen ersetzen, wenn Interoperabilitaet darunter leidet.
- Bestehende Client-Kompatibilitaet mit Thunderbird, LibreOffice, Desktop-Dateimanagern und vergleichbaren Clients erhalten.
- Protocol Edge Cases, Precondition-Handling und korrekte Statuscodes beruecksichtigen.

## 11. Produktionsreife

Eine Funktion ist nicht fertig, nur weil der Happy Path funktioniert.

Mindestens beruecksichtigen:

- fehlende Konfiguration
- ungueltige Eingaben
- leere Daten
- doppelte Daten
- Netzwerkfehler
- Timeouts
- Neustart waehrend laufender Operationen
- nicht verfuegbare optionale Programme
- Dateisystemfehler
- Berechtigungsfehler
- konkurrierende Zugriffe
- Wiederholungsversuche
- sichere Wiederaufnahme oder sauberer Abbruch

Keine Endlosschleifen, haengenden Worker oder unbegrenzt wachsenden Queues/Logs ohne Schutz einbauen.

## 12. Updates und Laufzeitdaten

Programmcode und Laufzeitdaten muessen getrennt bleiben.

Bei Update-, Installer- und Deployment-Aenderungen insbesondere schuetzen:

- `.venv`
- `instance`
- `.simpleoffice-history`
- `.simpleoffice-control`
- Benutzerdateien
- Datenbanken
- Audit-Historien
- lokale Konfiguration

Updates duerfen lokale Laufzeitdaten nicht unbeabsichtigt ersetzen oder loeschen.

## 13. Dokumentation

Groessere neue Funktionen oder relevante Verhaltensaenderungen muessen dokumentiert werden.

Dokumentation soll enthalten:

- Zweck
- Installation/Setup
- Konfiguration
- Betrieb
- Sicherheitsgrenzen
- Fehlerfaelle
- bekannte Einschraenkungen
- relevante Architekturentscheidung
- Begruendung, warum die Loesung so umgesetzt wurde

README und passende Dateien unter `docs/` bei Bedarf im selben Change aktualisieren.

## 14. UI/UX

- Bestehende Bedienmuster wiederverwenden.
- Gleiche Funktionen sollen gleich aussehen und sich gleich verhalten.
- Technische Fehlermeldungen nicht ungefiltert als Benutzertext anzeigen.
- Lade-, Fehler-, Leer- und Erfolgszustand beruecksichtigen.
- Aktionen mit Datenverlust oder kritischer Wirkung klar kennzeichnen bzw. bestaetigen lassen.
- Keine versteckten Nebenwirkungen bei normalen UI-Aktionen.

## 15. Vorgehen bei Aenderungen

Vor dem Implementieren:

1. Betroffenen Code und angrenzende Module lesen.
2. Bestehende Tests pruefen.
3. Vorhandene vergleichbare Implementierung suchen.
4. Bestehende Architektur und Datenfluesse verstehen.

Beim Implementieren:

1. Kleinste sinnvolle Aenderung waehlen.
2. Gemeinsame Logik wiederverwenden.
3. Fehler- und Randfaelle mit implementieren.
4. Tests ergaenzen oder aktualisieren.
5. Dokumentation aktualisieren, wenn Verhalten oder Betrieb betroffen sind.

Vor Abschluss:

1. Syntax/Compile pruefen.
2. Relevante Tests ausfuehren.
3. Groessenlimits pruefen.
4. Security-Auswirkungen pruefen.
5. Datenverlust-/Migrationsrisiken pruefen.
6. Dokumentation pruefen.
7. Keine offenen Debug-Ausgaben, temporaeren Hacks oder unnoetigen TODOs hinterlassen.

## 16. Was nicht getan werden soll

Nicht ohne zwingenden Grund:

- funktionierende Architektur komplett neu schreiben
- Framework wechseln
- neue parallele Subsysteme fuer bereits geloeste Aufgaben erstellen
- Zertifikatspruefung deaktivieren
- Authentifizierung oder Autorisierung umgehen
- Fehler ignorieren
- Daten still verwerfen
- Tests entfernen, um eine Aenderung passend zu machen
- Produktionscode mit hartcodierten lokalen Spezialwerten versehen
- sensible Daten loggen
- Datenbanken oder Laufzeitverzeichnisse bei Updates ersetzen
- grosse Funktionen oder Dateien trotz CI-Grenzen weiter vergroessern

## 17. Definition of Done

Eine Aufgabe gilt erst als abgeschlossen, wenn fuer den betroffenen Umfang gilt:

- Funktion implementiert
- relevante Fehlerfaelle behandelt
- bestehende Funktionen nicht unbeabsichtigt gebrochen
- Tests vorhanden bzw. angepasst
- Tests erfolgreich
- Code kompiliert
- Groessenlimits eingehalten
- Security geprueft
- Datenintegritaet geprueft
- Dokumentation aktualisiert
- Bedienung konsistent
- keine bekannten kritischen TODOs oder Debug-Hacks offen

Wenn nicht alle Punkte anwendbar sind, muessen mindestens die fuer die Aenderung relevanten Punkte erfuellt sein.
