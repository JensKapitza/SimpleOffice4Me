# Version 3.0: Funktionen und Betrieb

Stand der Dokumentationsprüfung: 2026-10-05, Codebasis
`2abc3ef6a02c173304d6c23352f48497730b7068`. Die Paketversion in
[`pyproject.toml`](../pyproject.toml) ist 2.0.0. „3.0“ bezeichnet hier die
additiven Erweiterungen; die Release-Freigabe ist noch offen.

## Aktivieren und Berechtigungen

Die zentrale [Capability-Registry](../app/v3_capabilities.py) enthält die
folgenden 16 Schalter. Alle haben den Standard **aus**. Aktivierung erfolgt
unter **Administration → Capabilities 3.0** (persistente `v3`-Sektion der
Einstellungsdatei, ein Schalter je Capability). Nicht gesetzte Schalter bleiben
aus; unbekannte Capability-Schlüssel sind nicht verfügbar. Die früheren
`SIMPLEOFFICE_V3_*_ENABLED`-Umgebungsvariablen sind entfernt; der Job-Worker
liest dieselbe Datei über sein `--root`-Verzeichnis.

| Capability | Einstellung (`v3`-Sektion) | Funktion / Anleitung |
|---|---|---|
| `v3.sample` | `v3.sample` | Architektur-Smoke-Capability, kein eigenes Fachmodul; [Vertrag](V3_EVOLUTION_CONTRACT.md) |
| `v3.relations` | `v3.relations` | Modulübergreifende Referenzen und Beziehungen; [Relations](V3_RELATIONS.md) |
| `v3.activity` | `v3.activity` | Domain Events und Activity Stream; [Activity](V3_ACTIVITY.md) |
| `v3.jobs` | `v3.jobs` | Persistente Jobs, Leases, Wiederholungen; [Jobs](V3_JOBS.md) |
| `v3.policy` | `v3.policy` | Fassade über bestehende Berechtigungen; [Policy](V3_POLICY.md) |
| `v3.search` | `v3.search` | Globale Suche und Command Palette; [Suche](V3_SEARCH.md) |
| `v3.entity_context` | `v3.entity_context` | Gemeinsamer Kontext für bestehende Objekte; [Entity-Kontext](V3_ENTITY_CONTEXT.md) |
| `v3.inbox` | `v3.inbox` | Dokumenteingang mit nachvollziehbarem Import; [Inbox](V3_INBOX.md) |
| `v3.automation` | `v3.automation` | Deklarative Regeln und kontrollierte Aktionen; [Automation](V3_AUTOMATION.md) |
| `v3.workboard` | `v3.workboard` | Gemeinsame Aufgaben-/Kalenderansicht; [Workboard](V3_WORKBOARD.md) |
| `v3.crm` | `v3.crm` | Kundenarbeitsfläche auf bestehenden Kontakten; [CRM](V3_CRM.md) |
| `v3.finance` | `v3.finance` | Geschäftsdokument-Lifecycle; [Finance](V3_FINANCE.md) |
| `v3.extensions` | `v3.extensions` | Versionierte Integrationsschnittstelle; [Extensions](V3_EXTENSIONS.md) |
| `v3.health` | `v3.health` | Health-Dashboard und begrenzte Diagnose; [Health](V3_HEALTH.md) |
| `v3.android_offline` | `v3.android_offline` | Kontrollierter APK-Offline-Arbeitsbereich; [Android](ANDROID.md) |
| `v3.federation` | `v3.federation` | Versionierter Transfer auf bestehendem Trust; [Federation](V3_FEDERATION.md) |

Eine aktivierte Capability erteilt keine Benutzer-, Rollen-, Objekt- oder
Peer-Rechte. Die vorhandenen serverseitigen Fachberechtigungen bleiben
maßgeblich. Die Anzeige in **Administration → Inventar** enthält
Capability-Zustände, keine geheimen Umgebungswerte.

## Zusammenspiel und Fallback

- Die Command Palette ergänzt die vorhandene Navigation/Suche und öffnet mit
  **Strg/Cmd+K**; ausgeschaltet bleiben die bisherigen Suchwege nutzbar.
- Relations, Activity und Entity-Kontext verwenden Referenzen auf die
  bestehenden Fachobjekte. Sie ersetzen keine Kontakt-, Dokument-, Kalender-
  oder Aufgabenverwaltung.
- Jobs benötigen einen separat laufenden Worker, wenn sie tatsächlich
  abgearbeitet werden sollen. Die Webanwendung startet auch ohne Worker;
  wartende Jobs allein sind kein Nachweis erfolgreicher Verarbeitung.
- Automation kann den bestehenden synchronen Ausführer ohne Job-Worker
  verwenden. Das Einschalten von Jobs startet nicht automatisch den Worker.
  Regeln erlauben registrierte Aktionen, kein freies Python/JavaScript/Shell.
- Workboard und CRM respektieren weiterhin die Rechte ihrer Fachmodule.
  Finance ist ein Geschäftsdokument-Lifecycle; die vorhandene verpflichtende
  Rechnungsvalidierung bleibt bestehen.
- Das bestehende [S3-Overlay](S3_OVERLAY.md) hat eigene Freigaben. Es wird nicht
  automatisch durch `v3.inbox` oder sämtliche 3.0-Schalter eingeschaltet.
- APK-Offline unterstützt derzeit bewusst ausgewählte **Aufgaben** und als
  Schreiboperation deren **Status**. Kalender, Kontakte, Mail und Federation
  bleiben online-only; PWA und APK haben unterschiedliche Cache-Verträge.
- Federation v3 ersetzt weder Peer-Trust noch die noch benötigten v1-Endpunkte.
  Health/Extensions benötigen für externe Prüfungen eine passende Integration;
  ein aktivierter Schalter beweist keinen funktionierenden externen Dienst.

Den Worker mit dem installierten Projektinterpreter aus dem Projektverzeichnis
als eigenem Prozess starten. Der Datenpfad muss zur Webinstallation passen:

```bash
.venv/bin/python -m tools.v3_job_worker --root /pfad/zum/dokumentbestand --once
```

`--once` führt einen Worker-Durchlauf aus, nicht die gesamte Warteschlange.
Ohne `--once` läuft der Worker mit konfigurierbarem `--poll-seconds` dauerhaft.
Das Beispiel ist für Linux/macOS; den Pfad durch den bestehenden Dokumentbestand
ersetzen. Der Worker liest den `v3.jobs`-Schalter aus derselben
Einstellungsdatei (zuvor unter Administration → Capabilities 3.0 einschalten).
Dienste müssen dieselbe Konfiguration erhalten und nach Änderungen kontrolliert
neu gestartet werden.

## Upgrade, Deaktivieren und Release-Nachweise

Der [Evolution Contract](V3_EVOLUTION_CONTRACT.md) gilt unverändert:
**expand → backfill → parallele Kompatibilität → geprüfter cutover → separates
cleanup**. Vor einem Upgrade einen überprüften Backup-/Recovery-Stand erzeugen.
Neue Schemas werden additiv eingeführt; keine Altbestände für eine vermeintlich
saubere Neuinstallation löschen.

Schalter einzeln deaktivieren, betroffene Web-/Worker-Prozesse kontrolliert neu
starten und die vorhandenen Fachwege sowie Daten und Rechte prüfen. Rules,
Jobs und neue Daten nicht zur Deaktivierung löschen. Ein laufender Worker muss
separat kontrolliert beendet werden; ein geänderter Webprozess-Schalter beendet
ihn nicht automatisch. Altprotokolle und Rollback-Daten bleiben bis zum
gesondert geprüften Cleanup erhalten.

Die [Release-Matrix](V3_RELEASE_GATE.md) nennt vorhandene Repository-Nachweise
und ausstehende Installations-, Upgrade-, Client-, Geräte-, Last- und
Wiederanlaufprüfungen. Historische Testzahlen gelten für ihre jeweiligen Commits,
nicht automatisch für einen neueren Stand. [#505](https://github.com/JensKapitza/SimpleOffice4Me/issues/505)
und [#506](https://github.com/JensKapitza/SimpleOffice4Me/issues/506) bleiben offen.

## Bekannte Korrekturen und geplante Optionen

Die [Dokumentationsprüfung](DOKUMENTATIONSPRUEFUNG_3_0.md) und die betroffenen
Fachanleitungen nennen IMAP-Checkpoint-/MIME-Suchgrenzen (#587/#588). Weder eine
korrekte Mail-Vorschau noch ein später fehlerfreier Archivlauf beweisen, dass
alle ursprünglichen Nachrichten verarbeitet oder vollständig indexiert wurden.

Zusätzliche LiteLLM-/MCP-Betriebsintegration wird in
[#586](https://github.com/JensKapitza/SimpleOffice4Me/issues/586) geplant.
Der [bestehende MCP-Server](CHATGPT_MCP.md) ist davon zu unterscheiden; der
zusätzliche Infrastrukturumfang ist kein bereits abgenommenes 3.0-Feature. KI-Ausgaben und
KI-generierte Illustrationen ersetzen keine Funktions-, Sicherheits- oder
Release-Prüfung. Weitere Betriebsoptionen stehen im
[Konfigurationsregister](KONFIGURATIONSREGISTER.md).
