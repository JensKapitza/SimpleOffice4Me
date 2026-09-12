# Neuerungen: Fehlerreporting

Diese Datei beschreibt die fachlichen und technischen Neuerungen des zentralen Fehlerreportings sowie die Gründe für die gewählte Architektur. Konkrete Betriebsparameter stehen in `docs/GITHUB_ERROR_REPORTING.md`.

## Ausgangsproblem

Fehler in verteilten Installationen waren bislang hauptsächlich lokal sichtbar. Für die Analyse mussten passende Logstellen manuell gesucht und anschließend in ein Issue übertragen werden. Das kostet Zeit und führt leicht zu unvollständigen oder mehrfach angelegten Fehlermeldungen.

## Ziel

SimpleOffice4Me soll Fehler automatisch wiedererkennen und zentral nachvollziehbar machen, ohne dafür vollständige Logs oder fachliche Nutzdaten übertragen zu müssen.

## Architektur

```text
SimpleOffice-Installation
        |
        | kleine technische Fehlerdiagnose
        v
Master oder Error-Relay
        |
        | Deduplizierung
        v
GitHub-Issue
```

Die bekannte Master-Adresse wird als stabiler Zielpunkt verwendet. Die Fehlerübertragung bleibt dabei fachlich von der normalen Federation getrennt.

## Was neu ist

- automatische Erfassung unbehandelter Anwendungsfehler
- stabiler Fehler-Fingerprint für wiederkehrende Fehler
- zentrale Weiterleitung über Master oder optionalen Relay
- Wiederverwendung bestehender Issues statt unkontrollierter Duplikate
- Health-Endpunkt zur Betriebsüberwachung
- manueller Fallback auf der Fehlerseite
- Unterstützung der mobilen und Desktop-Hüllen für den externen Fallback
- harte Größen- und Feldgrenzen für Fehlerberichte
- Rate-Limits und Begrenzung paralleler Weiterleitungen
- eigener eingeschränkter Docker-Betriebsmodus für den Relay
- CI-Smoke-Test für diesen Relay-Betrieb

## Warum nur eine kleine technische Diagnose übertragen wird

Vollständige Logs können Dateipfade, Dokumenttitel, E-Mail-Adressen oder andere betriebliche Informationen enthalten. Eine nachträgliche Filterung wäre deutlich schwerer zuverlässig zu prüfen als eine von Anfang an kleine Feldliste.

Deshalb enthält die automatische Meldung nur technische Merkmale, die für Wiedererkennung und Fehlerortung notwendig sind. Inhalte der eigentlichen Arbeit mit SimpleOffice4Me bleiben lokal.

## Warum die Fehlerübertragung nicht an eine bestehende Federation-Kopplung gebunden ist

Fehler treten häufig gerade dann auf, wenn eine Installation nur teilweise eingerichtet ist. Würde die Meldung eine vollständig funktionierende Federation voraussetzen, könnte ausgerechnet der Diagnoseweg ebenfalls ausfallen.

Darum wird lediglich die bekannte Master-Adresse als Zielpunkt genutzt. Die Fehler-API bleibt ein eigenständiger technischer Dienst.

## Warum Deduplizierung wichtig ist

Ein einzelner Programmfehler kann auf vielen Installationen und wiederholt auftreten. Ohne Fingerprint würde daraus eine große Zahl gleichartiger Issues entstehen. Der Fingerprint fasst technisch gleiche Fehler zusammen und macht Häufigkeit und Wiederholung erkennbar.

## Fehlerverhalten

Die Anwendung selbst muss weiter funktionieren, auch wenn die externe Fehlerweiterleitung nicht verfügbar ist. Deshalb bleibt der lokale Fehlerdatensatz erhalten. Ein Fehler des Reporters darf keinen zweiten Anwendungsfehler auslösen.

Ungültige oder zu große Fehlerberichte werden dagegen abgewiesen. Die Diagnosefunktion soll niemals zu einer unkontrollierten allgemeinen Datenübertragung werden.

## Docker-Relay

Der optionale Relay läuft mit einer bewusst kleineren öffentlichen Oberfläche als eine normale SimpleOffice4Me-Instanz. Im Relay-Modus sind normale Anwendungsseiten nicht erreichbar. Das reduziert die Zahl der öffentlich erreichbaren Komponenten auf das, was für den Fehlertransport tatsächlich benötigt wird.

## Manueller Fallback

Kann ein Fehler nicht automatisch weitergegeben werden, bietet die 500-Seite einen manuellen Weg an. Auch dabei werden nur die bereits vorgesehenen technischen Diagnosedaten verwendet. Der Fallback ist kein Ersatz für einen automatischen Log-Upload.

## Auswirkungen für Betreiber

Für normale Clients entsteht kein zusätzlicher Einrichtungsaufwand, wenn ein globaler Master verwendet wird. Betreiber können die Funktion dennoch abschalten oder einen eigenen Collector konfigurieren. Die konkreten Optionen sind in `docs/GITHUB_ERROR_REPORTING.md` beschrieben.

## Auswirkungen für Entwickler

Bei Änderungen an diesem Bereich müssen insbesondere folgende Eigenschaften erhalten bleiben:

- kleine feste Datenoberfläche
- stabile Deduplizierung
- kein Abhängigkeitszwang zur normalen Federation
- lokale Fehlerbehandlung funktioniert auch ohne externen Dienst
- Relay veröffentlicht keine normalen Anwendungsseiten
- Unit-, Security- und Container-Smoke-Tests bleiben grün

## CI-Regressionsschutz

Der CI-Pfad prüft den eingeschränkten Relay-Betrieb zusätzlich zu den normalen Python-, Security- und Dependency-Prüfungen.

Bei der Integration wurde außerdem ein Fehler im Navigations-Smoke-Test gefunden: Der Test rief zuerst den Logout-Endpunkt auf und prüfte danach weitere Seiten im ausgeloggten Zustand. Dieser Test wurde korrigiert, damit zustandsverändernde GET-Endpunkte nicht als normale Navigationsseiten behandelt werden.

## Entscheidungen und Gründe

| Entscheidung | Warum |
| --- | --- |
| zentrale Fehlerweiterleitung | weniger Konfiguration auf Clients |
| kleine technische Feldliste | reduziert das Risiko unnötiger Datenübertragung |
| stabiler Fingerprint | gleiche Fehler werden zusammengeführt |
| Master-Adresse als Default | vorhandener stabiler Bezugspunkt |
| Federation nicht erforderlich | Diagnose soll auch bei unvollständiger Kopplung funktionieren |
| optionaler Relay-Betrieb | öffentliche Oberfläche kann weiter verkleinert werden |
| Rate- und Parallelitätsgrenzen | schützt den zentralen Dienst vor Überlastung |
| lokaler Fehlerdatensatz bleibt erhalten | Analyse bleibt auch bei externem Ausfall möglich |
