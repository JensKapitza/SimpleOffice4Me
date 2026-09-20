# CRA-Meldebereitschaft

Stand: 20.09.2026.

Diese Datei beschreibt die technische und organisatorische Vorbereitung auf die
Meldepflichten nach Art. 14 des Cyber Resilience Act (CRA). Sie ist keine
Feststellung, dass der CRA im konkreten Vertriebs- oder Nutzungsszenario
anwendbar ist.

## Fristen bei anwendbarem CRA

- **24 Stunden:** Frühwarnung nach Kenntnis einer aktiv ausgenutzten
  Schwachstelle bzw. eines erheblichen Sicherheitsvorfalls.
- **72 Stunden:** ergänzende Meldung mit den zu diesem Zeitpunkt verfügbaren
  technischen Informationen und einer ersten Bewertung.
- **Abschlussbericht:** nach Maßgabe des CRA und des konkreten Vorfalltyps,
  sobald Ursachenanalyse und Abhilfemaßnahmen belastbar dokumentiert sind.

Die tatsächliche Übermittlung erfolgt über die vorgesehene CRA Single Reporting
Platform. SimpleOffice4Me sendet solche Meldungen nicht automatisch.

## Interner Ablauf

1. Eingang einer vertraulichen Meldung oder internen Feststellung dokumentieren.
2. Anwendbarkeit und Betroffenheit der ausgelieferten Versionen bewerten.
3. Zeitpunkt der Kenntnisnahme unveränderbar festhalten.
4. Technische Evidenz sichern: Version, Commit/Build, betroffene Komponente,
   Reproduktionsschritte, Logauszüge ohne Secrets sowie bekannte Auswirkungen.
5. Eindämmung, Fix, Regressionstest und Release-/Upgrade-Pfad dokumentieren.
6. Falls CRA anwendbar ist, 24-h- und 72-h-Fristen durch die benannte
   verantwortliche Stelle verfolgen.
7. Advisory, Release-Nachweis und SBOM-Bezug nach Abschluss aktualisieren.

## Mindestdaten für einen internen Incident-Datensatz

- interne Incident-ID
- Zeitpunkt der ersten Kenntnisnahme
- betroffene Produktversionen und Builds
- betroffene Komponente(n)
- Ausnutzungsstatus: unbekannt / vermutet / bestätigt
- technische Auswirkungen
- Vertraulichkeitseinstufung
- Eindämmungs- und Abhilfemaßnahmen
- zugehörige Tests und Fix-Commits
- zuständige Person/Funktion
- CRA-Anwendbarkeit und Meldeentscheidung
- Zeitpunkte von Frühwarnung, 72-h-Meldung und Abschlussbericht, falls relevant

Keine Passwörter, Tokens, personenbezogenen Produktionsdaten oder vollständigen
Angriffsdaten in öffentliche Issues, PRs oder CI-Logs übernehmen.

## Wiederholbarer Nachweis

Die CI prüft über `python tools/cra_check.py`, dass dieser Runbook-Nachweis,
die Security Policy, die Release-Checkliste, SBOM-Erzeugung und
Dependency-Audits weiterhin Bestandteil des Repositorys sind.
