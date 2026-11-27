# Abgeschlossene Kopien als Automationsereignisse

Wenn `v3.activity` aktiviert ist, schreibt SimpleOffice ein Ereignis in die
bestehende Aktivitätsdatenbank im Dokumentstamm. Eine Browser-Kopie kann zusätzlich
eine optionale Meldung für das eigene Konto erzeugen; ein vollständig erfolgreicher
geplanter Lauf kann ein ausdrücklich benanntes Konto informieren. Der Kopiervorgang wartet
nicht auf eine Gerätezustellung.

| Ereignis | Quelle | Wann es aufgezeichnet wird |
| --- | --- | --- |
| `document.copy_completed` | `browser.document_copy` | Die angemeldete Browseraktion hat eine erfolgreiche Rückmeldung vom Speicher für die neue Kopie bekommen. |
| `document.copy_job_completed` | `replication.batch` | Bei `flask run-replications` sind alle aktiven Spiegelungsregeln einschließlich Manifest und Abschlussprotokoll erfolgreich und mindestens eine Datei wurde wirklich kopiert. Ein fehlgeschlagener Teillauf erzeugt kein Ereignis. |
| `document.copy_job_completed` | `replication.rule` | Eine separat gestartete einzelne Spiegelungsregel hat erfolgreich mindestens eine Datei kopiert. Innerhalb eines Gesamtlaufs wird kein Einzelereignis aufgezeichnet. |

Die Aktivität enthält die technische Quellkennung, Akteur, Zeitpunkt und eine
stabile Ereigniskennung. Neben der Kopie-ID bzw. Laufkennung enthält sie nur
bei Jobs die Zahl kopierter Dateien und abgeschlossener Regeln; keine Pfade,
Dateinamen, Hashes der Datei, Inhalte oder Gerätegeheimnisse. `--run-id` ist
eine technische ID (keine Zugangsdaten), die ein Zeitplan einmal pro Lauf
festlegt und nur für Wiederholungen dieses Laufs beibehält. Damit wird dieselbe
Abschlussmeldung pro Akteur und Lauf höchstens einmal gespeichert. Kein
Ereignis bei bloßer Kontrolle unveränderter Dateien. Browser-COPY umfasst
nicht WebDAV, Dateisystembeobachtung, Sicherungen, restic oder fremde Planner.

Die Aktivität ist eine nachgelagerte, optionale SQLite-Aufzeichnung, keine
atomare Outbox mit der Dateiablage. Wenn der Prozess nach der Kopie und vor
dem Eintrag abbricht oder die Aktivitätsdatenbank nicht verfügbar ist, kann
der Eintrag fehlen. Ein kompletter Ausfall darf dadurch nicht als erfolgreiche
Kopie gemeldet werden; auch ein bereits bestätigtes Kopierergebnis wird durch
einen optionalen Aufzeichnungsfehler nicht rückgängig gemacht. Geänderte
Dateien aus einem Teillauf werden nicht zurückgerollt (bestehendes Verhalten).
Ein Wiederholungslauf, der nichts mehr kopiert, wird nicht als neue Kopie
behauptet.

## Lokale Kopiermeldung

Sind `v3.activity` und `v3.automation` aktiv, lassen sich **Browser-Kopien** und **Geplante Kopierjobs** in **Automation 3.0 → Lokale Kopiermeldungen** getrennt einschalten und in der Regelliste wieder abschalten. Die Meldungen enthalten keinen Dateinamen oder Inhalt. Bei der Wiederholung desselben bestätigten Laufs bleibt der ursprüngliche Auslösezeitpunkt erhalten, und jedes berechtigte Konto erhält höchstens eine Meldung pro Ereignis.

Für geplante Kopierjobs gibt der Betreiber das genaue, aktive lokale Empfängerkonto an. Beispiel für einen einzelnen, eindeutig benannten Lauf:

```sh
flask --app app run-replications --run-id spiegelung-2026-11-27T1800Z --notify-account mein-konto
```

Der Job läuft standardmäßig als Audit-Akteur `system`. Ein `--actor` ist keine Empfängerfreigabe und eine Anmeldung im Browser legt keinen Job-Empfänger fest. Ohne `--notify-account` bleiben Kopierjobs unverändert und versenden keine Meldung. Ein unbekanntes oder gesperrtes Konto fällt vor dem Kopieren auf; zusätzlich wird die Kontofreigabe beim Zustellen geprüft. Auch bei gültigem Ziel muss das Konto die Job-Meldungen selbst aktiviert haben. Teilläufe, Läufe ohne Änderungen und einzeln aus der Oberfläche gestartete Spiegelungsregeln lösen die Job-Meldung nicht aus. Die optionale Meldung wird nach der bestätigten Kopie verarbeitet; sie ist keine atomare Zustellgarantie bei Prozessabbruch.

Die Aktion `app.copy_notice` akzeptiert keine Parameter oder Zielgeräte. Der
Einstieg führt keine anderen oder gemischten Aktionen aus. Bei aktivierten
Hintergrundjobs kannst du „Sofort“, „Nach 30 Sekunden“ oder „Nach 5 Minuten“
wählen. Die zwei verzögerten Varianten verfallen fünf Minuten nach ihrer
Fälligkeit. Die Auswahl erscheint beim Aktivieren einer Regel; zum Umstellen
die Regel deaktivieren und mit dem gewünschten Wert wieder aktivieren.

Der Betreiber startet den vorhandenen V3-Hintergrunddienst mit demselben
Dokumentstamm und der gleichen Kontodatenbank wie die Webanwendung. Beispiel
für einen gewöhnlichen Dienstprozess bei aktivierter Capability `v3.jobs`:

```sh
export SIMPLEOFFICE_DOCUMENT_ROOT=/pfad/zum/dokumentbestand
python -m tools.v3_job_worker --root "$SIMPLEOFFICE_DOCUMENT_ROOT"
```

Verzögerte Meldungen verwenden eine dauerhafte Warteschlange. Regel,
Empfängerkonto und die gespeicherte UTC-Frist werden beim Zustellen erneut
geprüft. Eine inzwischen geänderte oder deaktivierte Regel oder ein nicht
mehr autorisiertes Konto verhindert die Meldung. Nach einem Neustart werden
fällige, gültige Aufträge weiter verarbeitet; abgelaufene bekommen
„Zeitfenster verpasst“. Ein fehlender oder nicht aktivierter Worker führt
nicht zu harter Echtzeit oder zu einer sofortigen Ersatzmeldung. Die
Zeitplanung unterstützt hier relative Verzögerungen mit Ablauf, keine
geplanten lokalen Zeitslots und keine Gerätezustellung.

Unter **Kommunikation → Automation 3.0** erscheinen die letzten zehn Meldungen
dieses Kontos. Die Liste aktualisiert sich, solange der Tab sichtbar und
online ist, normalerweise alle fünf Sekunden. Ein Hintergrund-Tab startet
keine Anfragen. Bei vorübergehenden Fehlern bleibt die letzte sichtbare Liste
erhalten; nach Ende der Sitzung oder Abschalten der Funktion stoppt der Abruf.
Das ist eine Aktualisierung dieser Seite, kein Push außerhalb der App, Ton
oder föderiertes Ziel.

## Erste Zustellung zwischen zwei verifizierten Instanzen

Der Betreiber kann eine **bereits bestätigte** Browser-Kopie oder erfolgreiche
Spiegelung ausdrücklich an eine gekoppelte Instanz melden. Beide Seiten
benötigen `v3.federation`, `v3.automation` und `v3.activity`. Die normalen
Schritte für verifiziertes, direktes Peer-Pairing gelten weiterhin; die Peer-
Adresse muss ein HTTPS-Ursprung mit überprüfbarem Zertifikat sein. Eine
lokale Entwicklungs-CA kann ausdrücklich als vertrauenswürdig konfiguriert
werden; TLS-Prüfung abschalten ist nicht vorgesehen.

In der Peer-Policy steht auf der Senderseite
`"data_classes":{"workflow_notices":{"send":true}}`,
auf der Empfängerseite entsprechend `"receive":true`. Bestehende andere
Policy-Einträge bleiben erhalten. Das Empfängerkonto erlaubt die verifizierte
Instanz zusätzlich in **Kommunikation → Automation 3.0 → Kopiermeldungen
gekoppelter Instanzen** und gibt die dortige Zielkennung dem Betreiber der
Senderinstanz. Das gleiche Konto kann den Empfang jederzeit widerrufen.

```sh
flask --app app list-workflow-copy-events --actor system
flask --app app send-workflow-copy-notice \
  --peer verifizierter-peer --destination ZIELKENNUNG \
  --actor system --event-id EREIGNISKENNUNG
```

Für Browser-Kopien ist der Audit-Akteur das angemeldete Konto, nicht
`system`. Das Paket enthält einen festen Meldungstyp, eine opaque ID,
die Zielkennung und das unveränderte UTC-Zeitfenster (der Pilot verwendet
UTC als Ursprungszone und maximal zehn Minuten ab dem Kopierereignis).
Keine Benutzernamen, Dateinamen, Texte, Inhalte, URLs oder Befehle im Paket.
Der Empfänger verifiziert Peer, Richtung, Freigabe, gültiges Konto, Frist
und früheste Zeit vor dem Schreiben. Eine Wiederholung erzeugt keine
zweite Meldung. Das Resultat `delivered` bedeutet, dass die Anzeige im
Empfängerkonto bereitsteht, nicht dass das Endgerät sie gesehen hat.

Auf der Senderseite mit `v3.jobs` in demselben Bereich **Automatische
Kopiermeldungen senden** öffnen. Zielinstanz und Zielkennung wählen, als
Auslöser „Dokument kopiert“ oder „Kopierjob abgeschlossen“ setzen und
die vorausgewählte Absenderzeitzone prüfen (z. B. `Europe/Berlin`). Die Frist beträgt wahlweise
10, 120 oder 600 Sekunden **ab Kopie** oder bis zum Ende des ursprünglichen
Tages in der Absenderzeitzone. Die Frist rollt nicht auf den nächsten Tag,
auch nicht nach Zeitzonenwechsel, Neustart oder Sommerzeitumstellung.
Bei längeren Fristen sind 30 Sekunden Wartezeit möglich. Für „Tagesende“
müssen beide Instanzen diese Funktion unterstützen; mit älteren Empfängern
verwende die kurze Frist. Kopierjobs benötigen weiterhin den expliziten
`--notify-account` beim geplanten Aufruf. Der Hintergrunddienst von oben
muss auf der Senderseite laufen. Das erneute Speichern derselben Kombination
aus Auslöser, Peer und Zielkennung aktualisiert die vorhandene Regel.

Das Kopieren selbst wartet nicht auf Netzwerk oder Empfänger. Ein
Hintergrundauftrag speichert die ursprüngliche UTC-Zeit, IANA-Absenderzone
und absoluten Grenzen. Neustart, kurze Ausfälle und erneute Versuche ändern
diese Zeiten nicht. Regelversion, Kontorechte, Vertrauen und Empfangsfreigabe
werden erneut geprüft. Unter „Zustellstatus für gekoppelte Kopiermeldungen“
stehen die aktuellen Ergebnisse der letzten 50 Hintergrundaufträge,
gruppiert nach Kopie: je Regel ein Ergebnis mit Frist in der Absenderzeitzone
und Anzahl der Versuche. Ein geplanter Wiederholungsversuch nach Ablauf wird
als „Kein weiterer Versuch vor Fristablauf“ ausgewiesen. Nach Ablauf erscheint
„Zeitfenster verpasst“ auch, wenn der Hintergrunddienst noch nicht wieder
läuft. „Bereitgestellt“ bestätigt einen Eintrag im Zielkonto, nicht das
Öffnen am Gerät. Unter „Ausführungsverlauf“ bleiben die einzelnen technischen
Zwischenstände verfügbar. Kein
Hintergrund-Push, Ton, harte Echtzeit oder Fernsteuerung.

Der reproduzierbare Test mit zwei gewöhnlichen, isolierten HTTPS-Instanzen
nutzt echtes Pairing, Webformular, Kopierjob, mehrfach neu gestarteten
Worker und Zielserver sowie den Widerruf:

```sh
python -m pip install -e '.[test]'
python -m tools.smoke_workflow_peer_notices
```

## Nächster Anschluss

Die vorhandene V3-Automation speichert benutzerbezogene Regeln mit Ereignis,
Bedingungen und geordneter Liste typisierter Aktionen; unterstützt wird
bereits `task.create`. Ein zukünftiger Verbraucher soll die vertrauenswürdigen
ActivityStore-Einträge mit Ereignis-, Akteur- und Korrelations-ID verarbeiten
und den Cursor sowie Quittungen pro Aktion dauerhaft speichern. Regelrecht
und Zielzustimmung müssen vor jeder echten Aktion erneut geprüft werden;
Ziel-IDs nur aus lokal bekannten oder ausdrücklich freigegebenen gekoppelten
Clients. Ein Test zeigt geordnete Auswertung als Dry-Run ohne Zustellung.
Ein generischer Worker für weitere föderierte Aktionen, Gerätesignale im Hintergrund sowie Adapter für Lampen und
Aktionen am Betriebssystem fehlen in diesem Stand. Kein generischer Skript-
oder HTTP-Aufruf, keine Umgehung der Nicht-stören-Einstellung. Explizites
Ein-/Ausschalten ist besser wiederholbar als Umschalten; befristete Aktionen
brauchen eine dauerhafte, widerrufbare Abschaltung.

[Die offizielle FRITZ!-Schnittstellendokumentation](https://fritz.com/pages/schnittstellen/)
ordnet die aktuelle Smart-Home-REST-API ein. Sie steuert im FRITZ!-Heimnetz
angemeldete und unterstützte Geräte; die ältere AHA-Schnittstelle bleibt
verfügbar, wird aber nicht weiterentwickelt. Eine beliebige WLAN-Leuchte ist
allein durch WLAN nicht steuerbar. Für den späteren Adapter müssen Modell,
FRITZ!OS, Anmeldung und tatsächlich unterstützte Schaltfunktion feststehen.

## Zeitfenster je Aktion

Eine Regel darf pro Aktion ein `timing`-Objekt angeben. Beispiele:

- `{"within_seconds": 10}`: spätestens zehn Sekunden nach dem Auslöser.
- `{"delay_seconds": 5, "within_seconds": 120}`: frühestens nach fünf
  Sekunden, spätestens nach zwei Minuten.
- `{"end_of_day": true, "timezone": "Europe/Berlin"}`: bis Mitternacht am
  Tag des Auslösers am gewählten Ort.
- `{"slot_start":"17:00","slot_end":"19:00","timezone":"Europe/Berlin"}`:
  nur in diesem Zeitfenster am Tag des Auslösers.

Ein gültiger Auslöser übergibt seinen ursprünglichen, zeitzonenbehafteten
`occurred_at`-Zeitpunkt. Das Intervall ist am Ende exklusiv. Eine
Wiederholung verschiebt die Frist nicht. Nicht vorhandene lokale Uhrzeiten
beim Wechsel auf Sommerzeit werden abgelehnt. Bei einer doppelt vorkommenden
Uhrzeit wird die Grenze so gewählt, dass ein Fenster nicht versehentlich
erweitert wird. Aktionsstatus `window_missed` bedeutet „Zeitfenster verpasst“;
`waiting_for_window` bedeutet, dass es noch nicht begonnen hat.

Der bisherige unmittelbare Regel-Executor prüft die Frist vor der Ausführung
und ruft verspätete Aktionen nicht auf. Für künftige Aktionen und
Föderationsziele fehlt noch die dauerhafte Zustellung mit separaten
Aktionsbelegen. Ein eingetragener Timer allein startet daher keinen Worker.
Die Funktion verspricht weder harte Echtzeit über das Netzwerk noch eine
pünktliche Ausführung auf einem ausgeschalteten Gerät.

Der transportierbare Zeitdatensatz enthält `requested_at` (ursprünglicher
Auslöser), `origin_timezone` (IANA-Zone), `not_before` und `expires_at`
(alle Zeitpunkte fest in UTC). Ein Server in Berlin und ein Handy in New York
beurteilen dieselbe Grenze. Die Ziel-Zeitzone ändert lediglich die Darstellung,
nicht die Frist. Authentifizierung und Ziel-Grants müssen vor Auswertung durch
den späteren Transport geprüft werden. Bei gemessener Uhrunsicherheit wird
nicht über die Grenze hinaus ausgeführt; ohne verlässliche Uhr keine kurze
Frist versprechen.
