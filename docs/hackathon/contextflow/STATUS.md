# ContextFlow – STATUS (Neu-gegen-Alt-Nachweis)

**Baseline `origin/main`:** `af5a600bae1f526e61198edbec4d9022e742f228` (26.10.2026, Merge PR #868)
**Hackathon-Branch:** `hackathon/contextflow-mvp` (ab Baseline, `main` unberührt)
**Spezifikation (maßgeblich):** `../../../../update2.md` im Elternordner — keine Kopie im Repo.
**Stand (29.10., OCR/ML/Vorschau):** OCR-Fehler nennen Windows-Installschritte
(venv-Extra, ensurepip, Tesseract/PATH) + `ocr_availability`-Helper (4 Tests OK);
`GET /contextflow/ml-status` (Provider, Ollama-Erreichbarkeit/Modell, Meta-Key
nur Bool, OCR-Bools) + Badge beim vCard-Export (8 Tests OK, node sauber);
Bildanalyse auf Detailseite und freigegebenen Anhangsbildern mit
Provider-/Modell-Anzeige, Mock→503, Unreleased ehrlich gesperrt (7 Tests OK);
Demo-Seeding: 4 synthetische Dateien + Vorschau-Vorabgenerierung, idempotent
(2 Tests OK); Peer-Test auf `__file__`-Pfad robuster gemacht. Live-Check
(Test-Client, synthetisch): 4× Detail/Preview 200, ML-Routen ehrlich 503 ohne
Backend/Key; Browser/Kamera und echte Inferenz bleiben Eigentümer-Schritte.
Nachbarn: 47 OK; mail-privacy V2 12 Errors umgebungsbedingt (Sandbox-Scratch
ohne instance/), 1 vorbestehender ClamAV-Pfadfehler. Gates grün.
## Arbeitsplan: Issues auf null + Meta-API (29.10., laufend)

- **Phase 1 – Stabilisieren (Regressionen grün 29.10.):** Baum sauber bis auf
  diese Liste + `.t/`-Fremdschrott; 86 Tests (neu + Kontakt + Peer + Nachbarn)
  mit nur 1 ClamAV-Pfadfehler (29.10. plattformgerecht korrigiert, Rerun grün);
  Live-Check 4×200 + ehrliche 503 aus Vorrunde gültig. Nachweis: phase1.log,
  phase2b.log. Blocker: keiner.
  Scratch `.t/` und Nutzerdaten nie committen.
- **Phase 2 – Meta-API + Ollama (Code fertig 29.10., Smoke offen):** Responses-API
  (`POST /v1/responses`, `muse-spark-1.3-contributor`, `GET /v1/models`-Check,
  Timeouts,
  Redirect-Stopp, keine Secrets in Fehlern/Logs) + Labels/Tests umgestellt;
  `.env`-Loader (Elternordner, Allowlist, nie Override, still) mit Hooks in
  Launcher/Demo; lokal nur echte Meta-Gewichte via Ollama; ContextFlow nutzt
  kein LiteLLM (direkter Pfad bevorzugt). Nachweis: 104 Tests OK (Testtransport),
  Gates grün. Blocker: Live-Smoke erst nach Key-Eintrag in `../.env`.
  Schlusskorrektur 29.10.: Testname auf
  `test_pins_point_at_responses_and_spark_contributor` umbenannt, finaler
  Rerun (final-128.log) grün.
- **Phase 3 – Abgleich (Triage-Liste 29.10. fertig, korrigiert):** 29 API-Einträge:
  genau 26 eindeutige Issues + 3 PRs (#913, #893, #814; PRs via pull_request-Feld
  erkannt). Buckets überschneidungsfrei: Hackathon-Scope (#767, #658, #813, #825,
  #736), manuelle Gates (#823, #873, #874, #506, #811, #859, #770, #483),
  allgemeines Repo (#801, #788, #759, #746, #741, #738, #737, #679, #675, #505,
  #474, #471, #330). Einzelabgleich mit Akzeptanzkriterien folgt. Nachweis: pro
  Issue Evidenz oder Blocker mit Nutzer-Aktion. GitHub-Connector kann
  lesen/schreiben; Schließungen nur mit Nachweis durch den Nutzer.
- **Phase 4 – Lücken schließen (offen):** fehlende priorisierte
  Funktionen/UI/Workflow schrittweise implementieren. Abhängigkeit: Phase 3.
- **Phase 5 – Abschluss (offen):** volle relevante Tests, Live-Demo,
  Issue-Evidenz, Commit + Push auf verifizierten Branch.
  Abhängigkeit: Phasen 1–4.

**Stand (28.10.):** Import-Tag-Engel 46 Tests OK (1 vorbestehender
Symlink-Skip, 94,1 s, nur fokussiertes Modul, Muse-Lauf);
Discover 222 OK + 2 Skips = Stand der Vorrunde (auftragsgemäß
nicht wiederholt); Guard 8/8, Vision 42/42, Peer 15/15,
Inventar 79 OK (Vorrunden); Whitespace-Eigenprüfung der 3
geänderten Dateien sauber (`git diff --check` blockiert, .git
für Sandbox unsichtbar — Eigentümer-Bestätigung offen). Live
VOR den neuesten Filtern: Text A 400/26,56 s ohne gültige
Tags, Text B 200/18,82 s mit „aus"/„notizen" durchgerutscht,
Bild 200/63,1 s uneindeutig, Objekte leer, Metadaten ok →
Filter (200-leer, Stoppwörter, Dateinamen-Stämme,
Teile/Material/Attribut-Filter), per Tests verifiziert.
Finale Live-Abnahme NACH Filtern: Text 200/29,67 s nur
[Rechnung, 2024, Euro]; Bild 200/62,76 s leer/Metadaten ok,
warmer Retry 200/5,31 s mit [Bank] behalten — enge
Tag/Bild-Route live-verifiziert (`git diff --check`
eigentümerseitig Exit 0). Bild-Mismatch live
(Holzbrett-Satz/[Bank]) → Label-Abgleich per Tests
(47 OK/1 Skip, diff-check offen); finale Konsistenz-
Abnahme: Text 200/34,39 s [Rechnung, 2024], Bank
kompakt 200/55,38 s [Stuhl] satztreu, Parkbank
200/60,06 s exakt [Bank], je Metadaten ok — enge
Route für Text + klares Bank-Bild verifiziert
(Mehrdeutigkeit: anderes Label oder keines).
Browser-Abnahme offen (Automation gestoppt, Neustart
abgelehnt). Demo 5091 verifiziert („180s-Kaltstart").
161er-/114er-/97er-/74er-Meilensteine unten bleiben Historie.
Historie: 27.10. Batch 0–2 (31 Tests: 11 Adapter + 9 Links + 11 Evaluator),
danach Batch 3, UI-Integration, Replay-/Security-Fixes, Testuhr-Patch,
Reset-Suite — alle Zwischenstände unten sind im finalen 74er-Lauf enthalten.

## Neu: verpflichtendes Meta-only-Laufzeit-Gate

Die alte Ollama-Vorgabe nutzte Qwen und verletzte damit die verbindliche
Hackathon-Architektur. Das bleibt als historischer Entwicklungsbeleg in den
älteren Live-Logs erhalten, ist aber für neue Starts und Laufzeit-Aufrufe
gesperrt. Ollama akzeptiert nur explizite Meta-Modellkennungen; Bildrouten
verweigern text-only Llama 3.2 1B/3B, bevor Ollama kontaktiert wird. Der
optionale Cloudpfad ist auf Meta Muse Spark 1.3 Contributor und
`https://api.meta.ai/v1/responses` festgelegt, folgt keinen Redirects,
verlangt Einwilligung und einen synthetischen Demo-Root. Demo-Starter, UI-Label,
README mit 3-Minuten-Ablauf sowie die Vorgabe in `AGENTS.md` sind aktualisiert.

Verifikation dieser Änderung: ContextFlow-Regressionssatz 190 Tests OK,
2 umgebungsbedingte Symlink-Skips; `compileall` für `app tools tests`,
`check_project_policy.py`, File-Size-Limit 1000, Function-Size-Limit 300
für `app tools` und `git diff --check` OK. Ein realer Meta-API-Aufruf war
ohne persönlichen `MODEL_API_KEY` nicht möglich; der Transport und die
Modell-/Endpoint-Gates sind mit Testtransport geprüft. Zwei redundante
Tag-Testfälle wurden anschließend entfernt, damit die vorhandene Testdatei
unter dem CI-Dateigrößenlimit bleibt; Produktionscode blieb unverändert.

## Neue Hackathon-Dateien (Branch-Arbeit)

| Datei | Batch | Inhalt |
|---|---|---|
| `app/contextflow/__init__.py` | 1 | Paket-Export `ContextflowReads` |
| `app/contextflow/contracts.py` | 1 | Evidence-Vertrag, Summen-Typen, kanonische Routen |
| `app/contextflow/adapters.py` | 1 | Actor-scoped Read-Adapter (MailCase, SiteVisit, Kontakt, Todo, Kalender, Invoice) |
| `tests/contextflow_fixtures.py` | 1 | Synthetische Fixtures auf echten Stores (TempRoot); Invoice-Zeilen als dokumentierte Ausnahme direkt als JSON |
| `app/contextflow/case_links.py` | 2 | Persistente actor-scoped Fall-Links (Dedupe, Revision, frische Rechte, explizites completed_on) |
| `app/contextflow/evaluator.py` | 2 | Deterministischer 5-Tage-Followup (Europe/Berlin, kein Modell, keine Writes) |
| `tests/test_contextflow_links.py` | 2 | 10 Link-Tests: Dedupe, Rechte, Twins, Persistenz |
| `tests/test_contextflow_evaluator.py` | 2 | 11 Evaluator-Tests: 5-Tage-Matrix, DST, Fingerprint, Invoice |
| `tests/contextflow_fixtures.py` | 2 | Erweitert: completed-Visits, Finding-Status, confirm_link |
| `app/contextflow/actions.py` | 3 | Freigegebene Einmal-Aufgabe (deterministische CalDAV-Resource, TodoConflict als Bestand) |
| `app/contextflow/hints.py` | 3 | Actor-lokale Snooze/Dismiss-Persistenz |
| `app/contextflow/web.py` | 3 | Blueprint /contextflow (default OFF, 404), Login+has_feature, CSRF aktiv |
| `templates/contextflow/index.html` | 3 | Deutsche UI, Engel-Platzhalter, Quellen+max 2 Aktionen, reduced-motion |
| `static/contextflow.css` | 3 | Ruhige warme UI, mobil, Tastatur/Screenreader |
| `app/contextflow/demo.py` | 3 | Synthetisches P0-Seeding (kein Test-Import) |
| `tools/contextflow_demo.py` | 3 | Isolierter Starter (Marker, eigene User-DB, nur 127.0.0.1) |
| `start.bat` | 3 | Nur `demo`-Befehl ergänzt (15 Zeilen, 0 geändert); alle Original-Optionen erhalten |
| `tests/test_contextflow_actions.py` | 3 | Echte Task, Replay/Neustart, stale, Rechte, Freigabe |
| `tests/test_contextflow_web.py` | 3 | Happy Path im echten Test-Client, Flag off, CSRF, Snooze/Dismiss, Escaping |
| `app/__init__.py` | 3 | Nur Blueprint-Registrierung (kein Altcode geändert) |
| `tests/test_contextflow_adapters.py` | 1 | 9 Tests: Positiv, Fremdnutzer, Leer-Actor, Invoice unknown/Entwurf/Mismatch, Credentials-Schutz, Fehlfälle |
| `pyproject.toml` (`packages`) | 1 | `app.contextflow` ergänzt (ein Zeile, kein Code) |

Altbestand (MailCase/SiteVisit/Kontakt/Todo/Kalender/Rechnung, V3, MCP/LiteLLM) ist
unverändert und wird nicht als neue Leistung präsentiert.

## Verifikation

- Codex-Baseline (27.10.): `tools/check_project_policy.py`,
  `tools/check_file_size.py`, `tools/check_function_size.py`, `compileall` OK;
  14 Todo-, 27 MailCase-, 14 Kalender-, 3 Kontakt-ACL-, 58 Rechnungs-Tests
  (1 Skip) grün. Codex führt auch die neuen Tests aus (kein manueller Lauf nötig).
- Neue Tests, Codex-Lauf gemeldet 10/11 grün (Policy-/Größen-/Syntaxchecks und
  `git diff --check` erfolgreich). Zuvor rot waren: message_count-Erwartung,
  unbekannter MailCase (PermissionError statt KeyError), set_credentials ohne
  Flask-Kontext — alle per Review-Fix korrigiert.
- Sicherheits-Korrekturen umgesetzt und dokumentiert: Invoice-Post-Read-Check
  nach frischem Contact-Load, Revisionshash über kanonischen Datensatz,
  `case_link_confirmed` nur literal `True`.
- Letzter Restfehler nur Test-Erwartung: Bei Entzug aller Kontaktfreigaben meldet
  der echte Store korrekt `ValueError('contact is not shared with this user')`
  vor Inhaltsrückgabe; Adapter behält diese Domain-Semantik. Erwartung danach
  korrigiert (reine Leser ohne Manage-Recht erwarten weiter PermissionError).
  Finale Nachprüfung: im 74er-Lauf enthalten (unten).
- Vorbekannte, nicht von diesem Batch verursachte Baselinefehler:
  `test_contact_store` parallel_carddav (Resource-Deadlock-Avoid in
  `app/file_lock.py`, Windows); `test_site_visits` saved_report
  Download-Response blockiert `unlink` (ein Test übersprungen).

- Batch 2 (Links + Evaluator, 21 Tests): geschrieben, im finalen 74er-Lauf
  grün enthalten. Rechte-Revokation zusätzlich per Mock-Frischetest geprüft,
  weil Visits owner-only sind und kein echter Multi-User-Fluss möglich ist;
  echte Ablehnung fremder Actors und Link-Trennung laufen auf echten Stores.
- `read_visit`-Evidence nutzt jetzt den kanonischen Datensatz-Hash, damit
  Quelländerungen (z. B. neuer Befund) den Hinweis-Fingerprint ändern.
- Korrekturlauf: `confirm(..., now=None)` mit injizierbarer aware Uhr (Default
  echte Berlin-Uhr), Tests/Fixtures geben NOW explizit weiter (hostunabhängig).
  Store-`_read` strikt (fehlend=leer, kaputt/falsches Schema=Fehler, kein
  Überschreiben). get/list prüfen frische Domain-Rechte (entziehbar getestet).
  Dedupe ohne Invoice-Anteil (Hinzufügen aktualisiert Link mit Audit).
  Nur kanonisches YYYY-MM-DD. Revokations-Regression mit geteiltem Kontakt,
  Mail-Teilnehmer und eigenem Manager-Visit.

- Batch 3 (UI + sichere Aktion, 2 neue Testdateien): geschrieben, im finalen
  74er-Lauf grün enthalten. Kein Modell, kein Ollama, kein API-Key im Spiel.
- Login-Verifikation per isoliertem Probe-Lauf: Seeding OK, Login demo/
  demo-contextflow-1 per echter Route (302), Dashboard 200 mit Hinweis,
  Throttle 429 ab 6. Versuch. Dabei Dashboard-Bug gefunden+behoben
  (`g.user` ist sqlite-Row ohne `.get()`).
- 35/35 Batch-0–2 von Codex verifiziert (Policy/Größen/compileall/diff OK).

## Nächster einzelner Batch (historisch — Batches 0–3 abgeschlossen)

Batch-0–3-Läufe sind abgeschlossen (Messblöcke unten); aktueller Stand
und offene Punkte stehen im letzten Abschnitt („Scan-zu-Foto-UX").

## UI-Integration + Geräte-Prefill (im 74er-Lauf verifiziert)

- `documents/nav.html`: „Büroengel“ unter Arbeit → `/contextflow/dashboard`
  (Route bleibt default OFF/404).
- Dashboard in Host-Navigation eingebettet (`documents/nav.html`-Include),
  SVG-Engel statt Emoji, Statuszeile „Bestätigt, noch nichts erledigt“.
- `FindingSummary`/`VisitSummary` führen `asset_id`/`room_id`/`assets`
  (Credentials weiter gestrippt); Evaluator baut `device_context` nur aus
  wirklich referenzierten Visit-Assets (sonst leer — kein erfundenes Gerät).
- `approve_task(..., confirmed_asset_id, confirm_device)`: Prefill nur bei
  literal bestätigtem Gerät aus `device_context`, eigene Task-Resource,
  Gerätezeile in Aufgabenbeschreibung; ohne Tick keine Geräteübernahme.
- `demo.py` setzt Buchscanner-Asset + Befund mit `asset_id` (konkreter
  Prefill-Fall); Regressionstests
  `tests/test_contextflow_device_prefill.py` im 74er-Lauf grün;
  Live-Browser verifiziert, siehe Messblock unten.

## Follow-up-Fixes (im 74er-Lauf verifiziert)

- `add_asset`/`add_finding` geben It. Produktion den VOLLEN Visit-Datensatz
  zurück (via `_mutate` → `get`), nicht das angelegte Objekt. `demo.py` und
  alle Device-Tests lesen die Kennung jetzt aus
  `record["assets"][-1]["asset_id"]` bzw. `record["findings"][-1]` zurück;
  `seed_demo` liefert zusätzlich `asset_id`/`device_name`.
- Nav: „Büroengel“ nur noch mit `contextflow_enabled` (eigener
  `app_context_processor` auf derselben Flag-Logik wie die Routen, fail
  closed per `|default(false)`); default OFF → kein toter Menüeintrag.
- Dashboard: Karte zeigt Falltitel; Rechnungsstand wörtlich „unbekannt“
  (Testwort wiederhergestellt) bzw. „bestätigt zugeordnet“; `data-link-id`
  je Karte; Bestätigen-Dropdowns listen bereits verknüpfte Ids nicht mehr
  (kein stiller Doppel-Link in der UI; Server-Dedupe/Audit unverändert).
- Gerätewahl: Radio-Gruppe pro Karte („Ohne Gerätebezug“ default, ein Radio
  je belegtem Gerät) statt verstecktem Erstgerät; Übernahme nur mit
  zusätzlichem `confirm_device`-Häkchen; jede (Hinweis, Gerät)-Kombi hat
  eigene Task-Resource und bleibt replay-safe.
- `static/contextflow.css` vollständig redesignet: Hero-Panel, Kartenraster,
  Typ-Badges, Geräte-Fieldset, Aktionsleiste, Focus-Ringe, reduced-motion,
  mobil <640px, Bootstrap-Token (`--bs-*`), warme Engel-Identität ohne
  neue Abhängigkeiten.
- Hinweis zur Testuhr: Routen werten die echte Berlin-Uhr aus; die
  bestehenden Web-Tests fixieren NOW=29.10.2026 — Approve-/Hinweis-Pfade
  brauchen ein reales Datum ≥29.10. (5-Tage-Regel, keine Schwächung).
  Neue Web-Tests seeden relativ zu `date.today()` und sind tagesunabhängig.
- 3 Alt-Fehler (Replay-409/Dashboard-`unbekannt`/Snooze) und 6 Fehler
  (Asset-KeyError) aus dem 61er-Lauf sind mit diesen Fixes adressiert
  und im 74er-Lauf bestätigt.

## Replay-Fix (Hint-gebundene Idempotenz, im 74er-Lauf verifiziert)

- `approve_task` löst die Resource/UID stabil aus
  (Actor, Link, **erwarteter Hint**, explizites Gerät) — der Hint bindet
  bereits alle Lesefakten. Ein bereits erstellter Task für exakt diese
  Freigabe wird per actor-scoped Lookup + UID-Identitätsprüfung
  zurückgegeben, **ohne** erneute Frischeprüfung. Nur ohne erstellten Task
  läuft die Evaluation; dann gilt weiter 409 bei verändertem Hinweis.
- Race-sicher bleibt `create_only=True`; ein belegter Konflikt mit
  fremdem Inhalt meldet laut 400 statt still zurückzugeben. Aufgaben aus
  der alten Pending-Digest-Benennung werden per UID-geprüftem
  Legacy-Lookup wiederverwendet (kein Duplikat nach Update).
- Fremd-Actor sieht über den Pre-Lookup nichts (None bei fehlend und bei
  unsichtbar — kein Existenz-Orakel); Rechteprüfung der Evaluation für
  jede Erst-Erstellung unverändert.
- Neue Tests in `test_contextflow_device_prefill.py`: Replay nach
  Evidenzänderung → 302 gleiche Aufgabe; stale vor Erst-Erstellung →
  ApprovalStale + keine Aufgabe; zwei Geräte → zwei Aufgaben, je
  einzeln replay-sicher (14 Tests in der Datei, inkl. Revoke-Denied unten).
- Keine Testuhr-Änderung nötig: Route nutzt weiter die
  echte Berlin-Uhr mit unveränderter 5-Tage-Regel.

## Security-Review: Rechte-Nachprüfung bei jedem Replay (im 74er-Lauf verifiziert)

- `approve_task` ruft vor jeder committed- oder legacy-Rückgabe
  `CaseLinkStore(root).get(actor, link_id)` auf (delegiert an `recheck`):
  gelöschter Link oder entzogene Mail-/Kontakt-/Visit-/Rechnungsrechte
  verweigern auch das Replay — es wird nichts ohne aktuelle
  Quellenberechtigung ausgeliefert. Erst-Erstellung weiter mit voller
  Evaluation (409 bei stale), Gerätebindung bleibt Hint-Resource-Identität
  (kein Ableiten aus geänderter Evidenz).
- Regressionstest `test_replay_after_rights_revoked_denied` (Muster aus
  `test_revoked_source_rights_hide_link`): Freigabe → benignes Replay
  löst auf (Rechte intakt) → Kontakt-/Mail-Rechte entziehen → gleiches
  Replay verweigert (`PermissionError`/`ValueError`), Aufgabe bleibt
  genau eine. Daneben: Replay nach Evidenzänderung → 302 gleiche Aufgabe;
  stale vor Erst-Erstellung → 409 (alle im 74er-Lauf grün).

## Testuhr-Fix (NUR Tests/Doku, im 74er-Lauf verifiziert)

- 68er-Lauf: einziger Fehler `test_approve_happy_and_replay` schon beim
  ersten POST — `completed_on=NOW-5` mit fixem NOW=29.10., Route mit
  echtem `_now()` am 28.10. → 4 Tage → korrekterweise kein Hinweis.
  Produktions-`FOLLOWUP_DAYS` und Frischeprüfung unverändert.
- Fix in `tests/test_contextflow_web.py` (`setUp`): `unittest.mock.patch`
  auf `app.contextflow.web._now` mit `return_value=NOW`, Stopp per
  `addCleanup`. Damit laufen Happy/Replay/Stale/Snooze/CSRF-Tests alle
  auf derselben Fünf-Tage-Grenze, tagesunabhängig. Andere `test_client`-
  Dateien treffen keine Contextflow-Routen; `DevicePrefillWebTest`
  seedet relativ zu `date.today()` und braucht keinen Patch.
- Damit ist der 68er-Erstfehler (erster POST 409 bei Real-Datum < 29.10.)
  behoben; Bestätigung im 74er-Lauf (Messblock unten).

## Unabhängige Verifikation (Eigentümer-Lauf, 28.10.): 67+1 — überholt

- ContextFlow-Suite **67 bestanden, 1 Fehler**
  (`test_approve_happy_and_replay`, erster POST: Testuhr NOW=29.10. gegen
  echte Route am 28.10. → 4 Tage statt 5); `compileall`, Projekt-Policy,
  Datei-/Funktionsgrößen, `git diff --check` bestanden. Dieser
  Zwischenstand ist durch den Testuhr-Patch behoben und vom finalen
  Messblock unten abgelöst.
- Live-Demo isoliert auf `127.0.0.1:5088`: Chromium Desktop + 390px
  Mobil ohne horizontalen Overflow und ohne Seitenfehler; synthetische
  Buchscanner-Auswahl + Bestätigung erzeugte eine echte Aufgabe,
  `/tasks/` 200 mit Aufgabentext und Buchscanner-Text sichtbar.
  Screenshots liegen nur ignoriert unter
  `.runtime-tools/contextflow-demo-live-{desktop,mobile,task}.png`
  (nicht committet).
- Launch-Pfad-Fixes danach (im 74er-Lauf verifiziert: Help-Exits + 6 Reset-Tests;
  kein `--fresh`-Serverneustart live-getestet, nur Reset-Unit-Tests + Help):
  `start.bat --help` (auch hinter Optionen) beendet sich per
  `HELP_REQUESTED`-Flag vor Venv-Bootstrap/Setup; alle
  Originaloptionen und Laufzeitverhalten unverändert,
  `demo --help` läuft weiter sauber über argparse.
  `tools/contextflow_demo.py --fresh` nutzt jetzt
  `reset_demo_root` aus `app/contextflow/demo.py`: marker-verifizierter
  Reset (Inhalt `synthetic:true` + Tool-Tag, keine Links), Schutz für
  Dateisystem-/Repo-Root, Junction-/Mount-Verweigerung vor jeder
  Löschung, Symlinks werden entfernt nie gefolgt, Read-only-Dateien
  (Windows-Git-Objekte) via Schreibretry auf 3.11–3.14
  (`onexc`/`onerror`-Kompatibilität). Neue Tests
  `tests/test_contextflow_demo_reset.py` (Marker-Reject, Fresh-Reset
  inkl. Read-only, Symlink-Nichtfolge, Protected-Roots).
- Ehrlich offen: Meta-/lokale Inferenz (Muse Spark, Ollama) ist weiter
  **nicht implementiert/live**; ContextFlow bleibt default OFF
  (404 ohne Flag), Demo-Daten strikt synthetisch und lokal.

## Finale Messung (Codex-Lauf, gemeldet): 73 OK + 1 Skip (74 run) + Live-Demo + Help-Exits

- `python -m unittest discover -s tests -p 'test_contextflow*.py' -q`
  → **Ran 74 tests, OK = 73 bestanden, 1 skipped** (Skip: Symlink-Test,
  dieser Windows-User kann keine Symlinks anlegen). Die 6 Reset-Safety-Tests
  (`test_contextflow_demo_reset.py`) sind in den 74 enthalten.
- `compileall`, Projekt-Policy, Datei-Limit 1000, Funktions-Limit 300,
  `git diff --check`: alle Exit 0.
- `start.bat --help`, `start.bat --port 5088 --help`,
  `start.bat demo --help`: alle Exit 0, kein pip-Fehler.
- Live-Demo isoliert, Chromium: Login, Dashboard 200,
  Buchscanner-Beleg/Radio sichtbar, explizite Gerätebestätigung erzeugte
  echte Aufgabe, `/tasks/` 200 zeigt Aufgabe + Buchscanner-Beschreibung;
  Desktop 1440 und Mobil 390 ohne horizontalen Overflow; keine
  JS-Seitenfehler. Screenshots nur ignoriert unter
  `.runtime-tools/contextflow-demo-live-desktop.png`, `-mobile.png`,
  `-task.png` (nicht committet).
- Live-Synthetic-Server läuft weiter:
  `http://127.0.0.1:5088/contextflow/` (Login demo/demo-contextflow-1).
- Abgrenzung: Ein tatsächlicher `--fresh`-Serverneustart wurde NICHT
  live-getestet — nur Reset-Unit-Tests + Help-Exits. Meta-/Multimodal-
  Runtime und lokale Inferenz sind NICHT implementiert/live.
  Feature-Default bleibt OFF.
- Nicht zu diesen ContextFlow-Ergebnissen zählen die bekannten
  Vollrepo-Baselinefehler (separat, unverändert): `test_contact_store`
  parallel_carddav-Kontention (`app/file_lock.py`, Windows) und
  `test_site_visits`-Teardown (`saved_report`-Download blockiert `unlink`).

## Vision-Slice (Bild→Befund-Entwurf, Mock-verifiziert, Inferenz NICHT live)

- Neu `app/contextflow/vision.py` + Routen `POST /suggest`,
  `POST /suggest/confirm`, Formular im Dashboard, Draft-Seite
  `templates/contextflow/suggest.html`: Foto + explizit gewählter
  Visit/Gerät (echte Asset-IDs, Paarung server-seitig geprüft) →
  Entwurf (nichts gespeichert, Bild nur im RAM) → explizite
  Bestätigung mit Fingerprint-Bindung → echte Finding via
  `SiteVisitStore.add_finding` (`unreviewed`), Weiterleitung auf die
  bestehende Visit-Seite. Modell schreibt nie Datensätze.
- Provider: lokal (Default, nur Loopback-HTTP-Ollama), Mock (nur
  explizit konfiguriert, UI-gelabelt „keine echte Analyse“), Meta nur
  mit Checkbox-Einwilligung + Server-Flag + Key + verifiziertem
  Demo-Root — echte Bürodaten verlassen nie die Maschine. Bild: nur
  JPEG/PNG/WebP, 5-MB-Cap, PIL-Dekodierprüfung; Vorschlag-JSON strikt
  validiert (Titel, Evidence-Klasse, Confidence).
- Demo setzte damals `CONTEXTFLOW_VISION_PROVIDER=mock` (gelabelt);
  heute wählt der Starter den Provider per `CONTEXTFLOW_OLLAMA_MODEL`
  (Modell → lokal, sonst Mock, siehe OLLAMA.md). Default lokal.
- Tests `tests/test_contextflow_vision.py` (Mock, Validierung, Rechte,
  Fingerprint, Meta-Gates, Local-Guards, Web-Happy-Path, kein
  Bild-Rückstand): im finalen 92er-Lauf bestanden (Details unten).
- Ehrlich NICHT live verifiziert: kein Ollama, kein MODEL_API_KEY,
  kein Netzaufruf in dieser Umgebung — weder lokale noch Meta-Inferenz
  je ausgeführt. Feature-Flag bleibt default OFF.

## Visit-Integration (Foto-Formular in Geräte-Karten, Lauf bestanden)

- `templates/site_visits/detail.html`: Jede Geräte-Karte enthält jetzt —
  nur mit `contextflow_enabled` — ein kompaktes Formular (Bootstrap-
  konsistent), das multipart an `contextflow.suggest` postet: CSRF,
  Hidden `visit_id` + karten-eigene `asset_id`, kamera-freundliches
  `accept="image/jpeg,image/png,image/webp" capture="environment"`,
  CTA „KI-Entwurf anfragen – Befund erst nach deiner Bestätigung“.
  Der bestehende „Foto / Beleg“-Upload ist unverändert.
- Web-Tests in `tests/test_contextflow_vision.py`: Visit-Detail zeigt
  bei aktivem Flag das Formular mit vorbelegter Buchscanner-`asset_id`,
  bei inaktivem Flag ist es abwesend (Upload bleibt). Im finalen
  92er-Lauf bestanden (der `self.scanner_id`→`self.asset_id`-Fix ist
  darin enthalten).

## Finale Messung (Codex-Lauf, gemeldet): 91 OK + 1 Skip (92 run) + Live-Demo

- `python -m unittest discover -s tests -p 'test_contextflow*.py' -q`
  → **92 gelaufen: 91 bestanden, 1 skipped** (Skip: Symlink-Test,
  Windows-User ohne Symlink-Privileg). Der ältere 74er-Meilenstein
  (73 OK + 1 Skip) bleibt als Historie bestehen.
- `git diff --check` und
  `python -m compileall -q app/contextflow tools/contextflow_demo.py`
  bestanden.
- Live-Browser isolierte Synthetic-Demo `localhost:5089`: Login,
  ContextFlow-Fall, verlinkten Ortstermin öffnen, Buchscanner-Karte
  zeigt kamera-freundliches Foto-Formular mit exakt vorbelegtem
  Visit/Asset, lokales synthetisches PNG hochladen, POST erzeugt
  gelabelte Mock-Draft-Seite, die sichtbar „nichts gespeichert / kein
  Befund" sagt; Provider-Label sagt keine echte Analyse.
- Unit-Tests bestätigen separat: CSRF/Auth, Confirm erzeugt
  `unreviewed`-Finding, Bild nicht persistiert, keine Cloud ohne Gates.
- Echte Live-Inferenz ist NICHT verifiziert (kein MODEL_API_KEY,
  kein Ollama). Feature-Default bleibt OFF; Meta-/Multimodal-Runtime
  nicht live.

## Login-QR-Disclosure (verifiziert, separater UI-Fix)

- QR-/Serveradress-Panel auf `/auth/login` ist per nativem
  `details`/`summary` standardmäßig geschlossen; Login bleibt primär
  (Formular zuerst, Benutzername mit Autofocus), Karussell
  (Pfeile/Zähler/Wisch, Slide-Sichtbarkeit) unverändert erhalten.
- Styles mit Tastatur-Fokus-Affordance (`:focus-visible`-Ring,
  auf/zu-Indikator, Dark-Mode mitgetragen); keine Animation.
- 5 gezielte Tests bestanden (`tests/test_auth_login_qr.py`: zu per
  Default, Login-Fokus, Karussell intakt, kein Panel ohne Targets,
  Panel an Login-Karte angeheftet).
- Live-Browser verifiziert: zugeklappt beim Laden, Klick und Leertaste
  schalten um, QR-/Adress-Steuerung sichtbar wenn offen.
- Test-Demo auf `localhost:5090` ist synthetisch; keine Aussage über
  Produktions-Integration oder Inferenz.
- Verfeinerung: Zusammenfassung liegt jetzt optisch an der Login-Karte an
  (Stack mit dezenter geteilter Kante); 5 gezielte Tests bestehen; frische
  synthetische Live-Vorschau auf `http://127.0.0.1:5091/auth/login`
  bestätigt zugeklappt per Default.

## Inventar-Einstieg (historischer Zwischenstand — erledigt, siehe „180s-Kaltstart verifiziert")

- Defekt 1 (behoben): `/inventory` hatte keinen eigenen Büroengel-Einstieg
  (nur Nav-Dropdown). Neu: Engel-Karte mit CTA „Büroengel öffnen“, nur mit
  Flag + `documents`-Recht (gleiche Gates wie Dashboard, kein toter Link);
  reiner GET-Link mit `origin=inventory` — keine Schreibaktion, keine
  Inventardaten an irgendeine KI. Objekt-Kontext entfällt bewusst:
  Inventar-Objekte sind keine Visit/Asset-IDs, nichts wird erfunden.
- Defekt 2 (behoben): keine Seiten-Kontext-Übergabe. Dashboard akzeptiert
  `?origin=inventory` (Allowlist, sonst ignoriert) und zeigt „Zurück zum
  Inventar“ (feste interne URL, kein Open Redirect).
- Playwright-Meldung („nicht installiert") geprüft: gehört zum separaten
  Web-to-PDF-Export (`app/web_export.py`), auf dem Inventar-Pfad ohne
  Referenz — für diesen Auftrag **nicht relevant**, keine Abhängigkeit nötig.
- Live-Befund 5091 (kein Trigger im Accessibility-Tree) nach Code-Lage
  ein Deployment-State-Verdacht: Code enthält Nav-Eintrag + Karte;
  Prozess läuft vermutlich mit älterem Stand oder Flag aus — Neustart
  aus aktuellem Checkout + Flag-Prüfung durch Eigentümer (historisch,
  erledigt — Endstand siehe „180s-Kaltstart verifiziert").
- Tests `tests/test_contextflow_inventory_entry.py` (5, neu): Einstieg
  sichtbar/versteckt (Flag/Recht), Back-Link, Fremd-Origin ignoriert —
  im finalen Lauf bestanden (Details unten).

## Nachlauf 97er-Codex-Lauf (1 Skip, 2 FAIL — Ursachen behoben, bestätigt)

- Gemeldet: 97 Tests, 1 Skip, 2 FAIL — beide in neuem Code, keine
  Sicherheitsgates gelockert. Rerun nach Fixes bestanden (finaler Messblock).
- FAIL 1 `test_dashboard_ignores_foreign_origin`: reines Test-Artefakt —
  `layout.html` gibt im versteckten Web-Export-Formular die aktuelle
  Same-Origin-Ziel-URL inkl. Query aus. Produktseitig verifiziert:
  Back-Link bleibt fest `url_for('inventory.index')`, nie eine externe
  URL. Test jetzt auf fehlenden externen href/Redirect ausgerichtet
  statt auf String-Abwesenheit.
- FAIL 2 `test_entry_omitted_without_documents_feature`: korrektes 403
  der bestehenden Feature-Grenze (`auth.enforce_feature_permission`,
  `FEATURE_BLUEPRINTS["inventory"]="documents"`); Test erwartet jetzt
  403 + kein Engel-Markup. Route/Rechte unverändert.
- `check_file_size`-FAIL: `app/business_documents.py` 1025 Zeilen →
  `_display_zugferd` unverändert nach `app/invoice_display.py`
  ausgelagert + importiert (erwartet ≤1000, gemessen danach 1003 —
  Zählung hiermit korrigiert, siehe nächsten Abschnitt); Invoice-Tests bleiben.

## File-Size-Nachlauf (Fix umgesetzt — bestätigt)

- Gemeldet (Codex): ContextFlow 97 Tests OK + 1 Skip, Invoice-Relations
  16 Tests OK; `check_project_policy`, `check_function_size`,
  `compileall`, `git diff --check` OK; File-Size-Gate weiter FAIL
  (`app/business_documents.py` 1003 Zeilen, alte ≤1000-Notiz war falsch).
- Fix: `_managed_invoice` (10 echte Zeilen, Loader + Manage-Guard)
  unverändert nach neuem `app/invoice_access.py` ausgelagert, in
  `business_documents.py` per Re-Export-Import erhalten
  (`invoice_relation_routes` nutzt weiter `documents._managed_invoice`);
  keine Verhaltensänderung, keine Leerzeilen-Tricks.
- File-Size-Check nach Fix: bestanden (finaler Messblock unten).

## Finale Bestätigung (Codex-Lauf, gemeldet): 97 OK + 1 Skip + 16 Invoice + Live-Demo

- ContextFlow: **97 Tests OK + 1 Skip** (Symlink-Test ohne
  Windows-Privileg — kein Fehler); Invoice-Relations: **16 Tests OK**.
- `check_project_policy`, `check_file_size --limit 1000`,
  `check_function_size --limit 300`, `compileall`, `git diff --check`:
  alle bestanden.
- Live-Demo 5091 nach Neustart aus aktualisiertem Checkout, Demo-Flag
  an: `/inventory` zeigt Karte „Büroengel öffnen“; CTA öffnet
  `/contextflow/?origin=inventory`; feste Rückverknüpfung „Zurück zum
  Inventar“ funktioniert. Demo-Daten synthetisch.
- Playwright-Exportmeldung besteht weiter, ist für Engel-Trigger /
  ContextFlow-Validierung nicht relevant; nichts nachinstalliert.
- Zusätzlich: `python -m unittest discover -s tests -p 'test_inventory*.py' -q`
  → 55 Tests OK. Die Ausgabe enthielt den erwarteten Fehlerlog eines Tests,
  der einen Shelf-Store-OSError simuliert; der Test bestand.

## Inventar-Mitarbeit aktiv (historischer Zwischenstand — erledigt, siehe „180s-Kaltstart verifiziert")

- Foto→Objekt-Vorschlag: neue Route `POST /contextflow/inventory-suggest`
  (Flag/Login/`documents`/CSRF, JSON, keine Writes, Bild nur im RAM);
  `vision.py` um Objekt-Schema + `suggest_inventory_object` ergänzt
  (lokal Default, Mock gelabelt, Cloud auf diesem Pfad immer verweigert).
- Inventar-UI: Button „Foto analysieren“, Vorschlag-Box mit explizitem
  Übernehmen/Verwerfen (füllt nur das Formular; Speichern weiter über den
  normalen Speicherweg nach Prüfung); ISBN-im-Titel-Hinweis mit
  Übernahme-Button; Non-ISBN-Scan verweist auf die Fotoanalyse.
- 8 neue Tests in `tests/test_contextflow_vision.py` (Backend 4, Web 4);
  historischer Zwischenstand („Ausführung hier unmöglich",
  „Codex-Lauf ausstehend") — erledigt, siehe „180s-Kaltstart
  verifiziert". JS-Verhalten und echte Inferenz wurden per
  Browser/Ollama verifiziert.

## Inventar-Korrekturen (Befunde 1–3, historischer Zwischenstand — erledigt, siehe „180s-Kaltstart verifiziert")

- Gemeldet (Eigentümer/Codex, Stand vor Korrekturen): ContextFlow 105
  Tests OK + 1 Skip, Inventar 55 OK, Policy/Größen/compileall/diff OK;
  Ollama fehlt, localhost:11434 nicht erreichbar; 5091 zeigt alte Version.
- Befund 1: passive CTA aus `/inventory` entfernt; Mock im Inventar
  gesperrt (`MockBlockedError` → 503 mit deutschem Hinweis auf fehlendes
  lokales Modell, kein „Hammer“-Platzhalter); Mock bleibt für
  ContextFlow-Befund-Demo erlaubt. Tests verbieten Mock-Hammer im
  Inventar (Backend raises + 503-Response ohne „Hammer").
- Befund 2: Non-ISBN-Ablauf aktiv — Kennung bleibt im Formular, Foto wird
  klar angefordert, Auswahl startet Analyse automatisch, Vorschlag nur
  per Klick übernehmbar (kein Auto-Speichern); 503/Fehler zeigen klare
  deutsche Meldung; ISBN-Scan holt weiter automatisch Buchdaten;
  `capture=environment` war bereits gesetzt; Foto nur lokal, kein Cloud.
- Historischer Zwischenstand („Browser-Re-Check ausstehend", „keine
  Ausführung möglich", „nicht bestanden") — erledigt, siehe
  „180s-Kaltstart verifiziert".

## Lokale Inferenz live verifiziert + Demo-Kaltstart 180s

- Verifiziert (Eigentümer, Live-Test 127.0.0.1:5091): Ollama 0.40.12,
  qwen3-vl:2b (1,9 GB) lokal auf 16-GB-CPU-Rechner; Foto wurde warm als
  „Hammer"/tool vorgeschlagen, nur per Klick übernommen, kein Datensatz
  gespeichert (No-save geprüft).
- Kaltstart-Befund: erster Aufruf nach Modellstart überschritt 60 s
  („lokale Inferenz nicht erreichbar (timed out)"); warmer Retry OK.
- Fix (diese Sitzung, nur Demo-Starter): `CONTEXTFLOW_OLLAMA_TIMEOUT`
  in `vision_config_from_env`, Default 180 s bei lokalem Modell
  (Env-überschreibbar, ungültig → Default); App-Default außerhalb der
  Demo bleibt 60 s; kein Modell-/Cloudwechsel. Neue Tests: 180s-Default,
  Override, ungültige Werte.
- Stände (Eigentümer): ContextFlow 111 OK + 1 Skip, Inventar 55 OK,
  5 Demo-Konfigurationstests OK, Gates OK.
- Erledigt (Eigentümer): Rerun + 180s-Kaltstart verifiziert — siehe
  nächsten Abschnitt.

## 180s-Kaltstart verifiziert (Live-Test 127.0.0.1:5091)

- Demo lokal mit Ollama qwen3-vl:2b; nach `ollama stop` Kaltstart
  erneut geprüft: Fotoanalyse endete innerhalb des konfigurierten
  180-Sekunden-Limits mit Vorschlag „Hammer" + lokalem Providerlabel.
- Klick auf „Als Objektnamen übernehmen": Objektname=Hammer,
  Objekttyp=tool im Formular; NICHT gespeichert —
  „Noch keine Inventarobjekte vorhanden" blieb sichtbar.
- Testbild: eigens erzeugte synthetische Hammer-Illustration,
  keine Nutzerdaten.
- Manuelle ISBN-Suche 9780131103627 live OK: „The C Programming
  Language", Brian W. Kernighan + Dennis MacAlistair Ritchie, Typ book.
- Voller Lauf nach Timeout-Code: ContextFlow 114 OK + 1 Skip,
  Inventar 55 OK; Policy-/File-Size-/Function-Size-/compileall-/
  diff-check-Gates bestanden. Kein Commit/Push.
- Offen: physische Kamera-/Barcode-Hardware (nicht geprüft).

## Scan-zu-Foto-UX (implementiert, Lauf ausstehend)

- Zwei Buttons direkt unter den Scanner-Steuerelementen, im selben
  Gate wie die KI-Vorschläge: „Foto aufnehmen" (setzt vor dem Öffnen
  capture=environment, Handy-Rückkamera) und „Foto auswählen" (ohne
  capture, vorhandenes Bild); beide öffnen denselben #photo-Input,
  die Datei bleibt am Inventar-Formular.
- Abbruch-sicher (kein Change-Event → Auswahl bleibt erhalten),
  wiederholt nutzbar; Auswahl behält die Auto-Analyse; Vorschlag
  scrollt ins Bild, Übernehmen/Verwerfen weiter explizit, kein
  Auto-Speichern. Non-ISBN-Scan fokussiert die Foto-Aktionen.
- ISBN-Lookup, Security-Gates, lokale Inferenz und Speicherweg
  unverändert.
- Neue Regressionstests: Sichtbarkeit/Platzierung/type=button
  (1 Test) + Gate-Asserts in 2 bestehenden Tests; Ausführung und
  Browser-/Kamera-Check ausstehend (Eigentümer, hier kein
  Shell-/Browser-Zugriff).

## P0-Umsetzungsmatrix (Ziel aus update2.md — umgesetzt/teilweise/fehlend)

| P0 | Stand | Beleg |
|---|---|---|
| A verknüpfter Servicefall | umgesetzt | bestätigte Links, actor-scoped Adapter, keine Namensraterei; Tests grün |
| B 5-Tage-Follow-up | umgesetzt | Berlin-Evaluator, DST, unknown-Rechnung, injizierbare Uhr; Tests grün |
| C Engel-Hinweis | teilweise | Karte + Quellen + Später/Nicht-mehr umgesetzt; Ruhezeiten/Hinweise-pro-Tag fehlen |
| D sichere Aktion | umgesetzt | Aufgabe genau einmal (replay-sicher); Editor-Link mit Manage-Gate; nichts erfunden |
| E Mail-Entwurf | FEHLT | keine Entwurfs-Erstellung aus Hinweis (Code-Read 28.10.) |
| F lokale Betriebsart | teilweise | Regeln ohne KI; Ollama live für Foto-Klassifikation; Hinweis-Umschreibung fehlt |
| G Meta-Runtime-Agent | teilweise | Provider + Doppel-Gates umgesetzt/getestet; echter Agent + Live-Call fehlen |
| H Security/QA/Alt-Neu | umgesetzt | Rechte/CSRF/Idempotenz/Revision, Tests + Gates grün (Zahlen im Kopf), Alt/Neu-Doku |
| Jury-Demo (3 Min) | FEHLT | Plan vorhanden, Video nicht produziert |
| P1/P2 | offen per Plan | erst nach stabilem P0 / nach Hackathon |
| Nicht-P0-Grenzen | eingehalten | kein Chatbot, kein Zweit-Store, kein Autoversand, kein Modell-Dateizugriff |
| Zusatz: Inventar-Fotos | umgesetzt | Suggest-Route, Foto-Buttons, Klick-Übernahme; live-verifiziert (180s-Abschnitt) |
| Erw.: Foto-Serien-Agent | TEILWEISE | Queue/UI + Pro-Foto-Vorschläge + Pro-Kandidat-Kennung + Exakt-Match/Anhang + GTIN-Härtung + KI-ISBN-Schema/Prompt + Serien-Abschluss (JSON-Einzelsave, Idempotenz, Regal-Retain, Checkliste, Buch/Gegenstand-Weiche, Reset) + Freund-Bestandscheck (15/15 inkl. Sync-State + ISBN-Prefilter verifiziert) + Ollama-Streaming + Model-Guard (Sibling/Refusal, Vision 42/42, Guard 8/8, Discover 205 OK + 1 Skip); fehlen: Instruct-Install + finale App-Live-Probe, Live-Handy/Browser-Kameratest, reale ISBN-Erkennungsqualität |
| Erw.: Import-Tag-Engel | TEILWEISE | Routen/UI + lokale Tag-Vorschläge mit Begründung + Bildbeschreibung/Objekte + Bestätigungs-Übernahme + Bild-Route entkoppelt + Ollama-Streaming + Model-Guard + Bild-Fallback + Grounding-Pflicht + Qualitätsfilter (46 Tests OK/1 Skip; Live VOR Filtern: Text A 400/26,56 s, Text B 200/18,82 s mit „aus"/„notizen", Bild 200/63,1 s leer → 200-leer, Stoppwort-/Stamm-/Teile-Filter); finale Abnahme: Text 200/29,67 s [Rechnung, 2024, Euro], Bild 200/62,76 s leer + warmer Retry 200/5,31 s [Bank] behalten, diff-check Exit 0 — enge Route live-verifiziert; Bild-Mismatch (Holzbrett-Satz/[Bank]) → Label-Abgleich per Tests (47 OK/1 Skip); UI meldet Timeout/Fehler/Leere ehrlich; Konsistenz-Abnahme: Text 200/34,39 s [Rechnung, 2024], Bank 200/55,38 s [Stuhl] + 200/60,06 s [Bank], Mismatch per Test abgewiesen — enge Route verifiziert (Mehrdeutigkeit: anderes/kein Label); fehlen: Browser-Abnahme (Automation gestoppt, Neustart abgelehnt), breitere Integration |
| Erw.: Freund-Check | FEHLT | Nachschub-Anfrage bei Freunden (Peers mit Datenteilung); keine Umsetzung |

- Bekannte T-Lücken (update2.md §10): T13 Ruhezeit (siehe C),
  T19 Canary-Test, T20 Meta-Live-Anteil. G ist Jury-Stufe-1-Gate
  (tatsächliche Meta-Nutzung, Pass/Fail).
- Matrix per Code-Read geprüft (28.10., Muse); Ausführung: Codex-Lauf nach Transfer-Korrektur (Isolation 3, Entry 8, Vision 26, Inventar 58 OK; node/policy/size/diff-Gates OK, kein Browser-Test) — Details im Arbeitslog. Erweiterung: update2.md §13.

## Match/Attach-Slice (implementiert, verifiziert)

- `POST /contextflow/inventory-match`: exakte Kennungssuche (kanonische
  ISBN-13, kanonische GTIN-8/12/13/14 mit Prüfziffer, sonst exakter
  Barcode-Abgleich, nie Titel); Längen-Gate 120, Malformed-GTIN → 400;
  Antwort unique/none/ambiguous als JSON, nur Lesen.
- `POST /contextflow/inventory-attach`: hängt genau ein Kandidatenfoto
  an genau ein Ziel; Server revalidiert Kennung→Eindeutigkeit→Ziel
  (404/409 bei Trefferfehlern), Sidecar via `save_photo`, keine neuen
  Objekte, keine externen Calls, kein Auto-Anhang.
- Karten-UI: „Im Inventar prüfen" + Trefferanzeige + „Foto anhängen"
  je Kandidat; Isolation und Transfer-Clearing unverändert.
- Verifiziert (Codex-Lauf nach GTIN-Härtung): Match/Attach 26 OK,
  ContextFlow 142 OK + 1 Skip, Inventar 58 OK (Mock-OSError erwartet),
  compileall/node/Policy/Größen/diff-Gates OK. Browser/Kamera-Check
  weiter offen (Versuch: Tab-Bindung schlug fehl, frischer Tab → Login;
  keine Kamera-Freigabe, keine Echtdaten geändert).

## ISBN-Extraktion (Schema/Tests verifiziert, Live-Bilder offen)

- Modell liefert optional `isbn` (nur exakt sichtbare Zeichen, Rate-
  Verbot im Prompt); Validierung per kanonischer ISBN-Prüfsumme,
  Ungültiges verworfen (nie Barcode), fehlendes Feld → leer.
- Serien-UI: ISBN-Vorschlag je Foto + explizite Übernahme in die
  karten-eigene Kennung; danach bestehender Check/Attach-Ablauf
  (serverseitig revalidiert), kein Auto-Speichern/Anhängen.
- Verifiziert (Codex-/Eigentümer-Läufe): Schema/Prompt/Prüfsummen-
  Validierung per Tests abgedeckt (Match-Datei 26 enthalten,
  Stand: ContextFlow 161 OK + 1 Skip, Inventar 79 OK inkl.
  Isolation/Serie/Reset, Simulations-OSError erwartet, Gates OK);
  echte lokale Foto-Extraktion nicht live-verifiziert,
  Browser/Kamera-Check weiter offen (Versuch: Tab-Bindung schlug
  fehl, frischer Tab → Login; keine Kamera-Freigabe, keine
  Echtdaten geändert).

## Serien-Abschluss (Tests verifiziert, Browser offen)

- Normales Formular + bestehende `POST /inventory/items`: mit
  `response_format=json` antwortet der Server strukturiert
  (gespeichert/Duplikat/Wiederholung/Fehler mit Feldhinweisen),
  Seite und Rest-Queue bleiben erhalten; ohne Flag unverändert
  Redirect (normales Verhalten erhalten).
- Pro-Kandidat-Token (`capture_request_id`, je Karte stabil): Retry
  nach verlorener Antwort trifft den Receipt (kein Duplikat);
  Duplikat meldet Bestand mit Link (nie fälschlich „gespeichert");
  Kopie nur mit frischer Kennung + allow_duplicate.
- Serien-Regal (ein Select, bleibt erhalten) + Fehlfeld-Checkliste
  je Karte (Titel/Typ, Kennung, Regal, Preis als Empfehlung);
  nach Save Reset der Kandidatenfelder, Regal bleibt (Währung EUR).
- 12 neue Tests (JSON-Save + Sidecar/Regal, Idempotenz, Validierung,
  Duplikat, Fremd-Actor, Normal-Redirect, Regal-Select); Ausführung
  durch Codex ausstehend; kein Browser/Kamera-Test.
- Fix: Save-/Checklisten-Code als eigene Sektion nach der
  Queue-Verdrahtung; statische Isolations-Testregion
  (Transfer→Attach, keine Formular-Reads) wieder unverändert grün,
  Testdatei unangetastet; per Lauf verifiziert (s. u.).
- Fix: Serien-Save unterscheidet Buch/Gegenstand (Typ book/buch
  oder gültige ISBN → Capture mit Regal; sonst Plain mit Token,
  ohne Regal-/Barcode-Zwang und ohne Buch-Umtypung); Server gibt
  Plain-Saves im JSON-Modus Receipt-Idempotenz (Retry/403/400);
  Checkliste markiert Kennung/Regal für Gegenstände optional;
  4 neue Objekt-Tests (16 in der Datei); per Lauf verifiziert (s. u.).
- Fix2: Titel-nur-Buch (Barcode-Pflicht nur im JSON-Modus gelockert,
  Titel+Regal weiter erforderlich; Normal-Capture unverändert),
  Objekt-Assertions auf Tool-Baseline (Regal-Objekt im Setup),
  Hammer-ohne-Barcode-Test, Reset löscht Kandidatenfelder
  (Preis/Standort/Tags/usw., Währung zurück auf EUR, Regal bleibt),
  echte PNG_1PX-Fixture; 18 Verhaltenstests + 2 statische
  Reset-Tests; per Lauf verifiziert (s. u.).
- Verifiziert (Eigentümer-Lauf 28.10.): Inventar 79 OK (59 Bestand
  + 18 Save + 2 Reset; OSError-Simulation erwartet), ContextFlow
  161 OK + 1 Skip, Gates (node/compileall/Policy/Größen/diff) OK.

## Freund-Bestandscheck (15/15 verifiziert)

- `POST /contextflow/peer-stock-check`: fragt geteilte Kataloge von
  Freunden lesend und lokal nach einer Kennung; Exakt-Match auf
  kanonischer ISBN/GTIN bzw. exakter Zeichenkette gegen Pfad/Tags/
  Dokument-ID; kein Netz, keine Inhaltsspeicherung, keine Weitergabe
  (der Katalog pflegt nur seine eigene Sync-Statuszeile).
- Freund-Definition (Trust≠Sharing, erhalten): Trust allein vergibt
  keine Datenrechte (docs/FEDERATION_DISCOVERY_TRUST.md).
  Freund zählt nur mit expliziter NORMAL/HIGH-Trust-Kante UND
  aktiviertem Peer-Datensatz UND literalem documents-receive-Opt-in
  (beide Policy-Formen); Nicht-Freunde werden nicht einmal gelesen;
  Treffer nur mit minimaler Referenz.
- Verifiziert (Muse-Lauf, Sandbox-Kopie): 15/15 OK in 13,7 s
  (zuvor 13/13 Eigentümerlauf nach Gate-Fix, davor 11/11).
- Sync-Ehrlichkeit verifiziert: je Freund ein State aus peer_state
  (ok nur nach abgeschlossenem Sync ohne Fehler); friends_checked
  zählt nur gültige Indizes, Rest in friends_unavailable; Einträge
  mit state ok/not_synced/error, nicht-valide nie als „kein
  Treffer". UI-Dreiteilung (Treffer / kein Treffer bei gültigem
  Index / nicht verfügbar). ISBN-Prefilter als Union (kanonisch +
  ungefiltert, Dedupe, Exakt-Match bleibt; Bindestrich-Pfad-
  Regression grün).
- Offen: Live-Peeraustausch (nur lokaler Katalog-Check umgesetzt).

## Import-Tag-Engel (Tests verifiziert, Live offen)

- `POST /contextflow/doc-tag-suggest`: Metadaten + lokal extrahierter
  Text (gespeichert, sonst verifiziert gelesen, max. 4000 Zeichen)
  an lokales Ollama-Modell (Loopback-only, Timeout, JSON);
  max. 8 Tag-Vorschläge mit Begründung, Duplikate markiert;
  Bilder optional Beschreibung + Objekthinweise (5-MiB-Cap,
  Fehler als Hinweis, Text bleibt). Speichert nichts.
- `POST /contextflow/doc-tag-confirm`: explizit gewählte Tags über
  normalen set_tags-Pfad (Merge, Duplikat-Skip, Rechte innen).
- Smart-Folder-UI: Engel-Panel am aktuellen Vorschaubild mit
  spielerischer Bestätigung; 503-Hinweis bei fehlendem Modell.
- Flags/Rechte: ContextFlow-Flag (default OFF, sonst 404),
  Login + documents-Feature, CSRF; Mock/Cloud ohne Consent
  verweigert (keine erfundenen Tags, kein Extern-Transfer).
- 15 Tests (Prompt-Inhalt, Loopback/Consent, Bilder, Bestätigung,
  Fehler/Gates, MIME-Regression); 15/15 per Eigentümer-Lauf
  verifiziert; kein Live-Ollama-/Browser-Test.
- Prüfergebnis (Eigentümer-Lauf): 13/14 OK; Fehler image=null durch
  leeren MIME (media_mimetype kennt nur Audio/Video). Fix in
  doc_tags (MIME aus validierter Endung) + Regressionstest (15);
  Re-Run 15/15 OK, Gates node/compileall/Policy/Größen/diff erneut OK.

## Ollama-Kurzantwort + Bild-Split (implementiert, Lauf ausstehend)

- Befund: echter lokaler Tag-Aufruf mit synthetischen Daten an
  127.0.0.1:11434/qwen3-vl:2b lief 2× je 120 s in einen Timeout,
  obwohl /api/ps das Modell geladen zeigte; /api/show meldet
  completion/vision/tools/thinking (Denk-Modell frisst das Budget).
- Fix: jede lokale Generate-Anfrage sendet harte options
  (num_predict 768 Text / 512 Vision) + think=false, sobald
  /api/show thinking anzeigt (Probe ≤10 s, Fehlschlag → ohne
  think weiter; Generate entscheidet Erreichbarkeit allein).
  Timeouts melden 503 mit deutschem Zeitlimit-Hinweis (kein Erfolg).
- Bild-Route entkoppelt: `POST /doc-image-suggest` (Bild-JSON bzw.
  {"image": null} für Nicht-Bilder ohne Modellaufruf, 400 für
  unbrauchbare Bilder, 503 bei Modellstörung); Text-Route berührt
  keine Bildbytes mehr; UI feuert beide parallel mit eigenem
  Zwischenstand. Bestätigungs-/Rechtewege unverändert.
- Verifiziert (Muse-Lauf, Sandbox-Kopie): doc_tags 22/22 OK
  (41,3 s), Vision 34/34 OK (13,5 s, opener/opened-Fix darin
  bestätigt), ContextFlow-Discover 187 OK + 1 Skip (256,7 s);
  Gates node/compileall/Policy/Größen grün. Zuvor: doc_tags
  21/21 und Vision 31 OK + 2 FAIL (Eigentümerlauf).
- Fix (diese Sitzung): think:false als Top-Level-Feld (zuvor
  fälschlich in options und von Ollama ignoriert); options nur
  num_predict; exakte Body-Tests für Text + Vision.
- Leere Modellantwort → klarer deutscher Fehler („leer
  geantwortet"); Timeouts → 503 mit timeout:true (3 Routen);
  Smart-Folder-UI zeigt bei Timeout „Modell lädt möglicherweise
  noch" statt Einrichtungs-Hinweis.
- Live-Proben (Muse, synthetisch durch die App, kein Erfolg):
  60-s-Budget → 503 Zeitlimit nach 60,2 s, ok:false; 180-s-Budget
  → 503 „Remote end closed connection without response" nach
  120,2 s, ok:false. Diagnose: /api/ps leer vor und nach den
  Proben (Modell lädt nie), /api/tags „context canceled",
  /api/show bestätigt thinking-Default true (Ollama 0.40.12).
  Kein erfolgreiches Live-Tagging behauptet.
- Diagnose 28.10. (Muse, Host-Blocker, kein App-Fehler):
  Direkt-Probe /api/show 200 in 0,05 s (Capabilities + thinking-
  Default true bestätigt); Mini-/api/generate (think:false
  top-level, num_predict 64, synthetisch) → Server schloss ohne
  Antwort nach 120,0 s. App-Retest (korrigierter Code, 60 s) →
  503 Zeitlimit nach 60,2 s, ok:false, timeout:true.
- server.log: historische 200er (6,67 s warm, 1m10s, 54,17 s mit
  955 Tokens) beweisen, dass Bedienen KANN; 500er exakt bei
  1m0s/2m0s/59,95s = Clients gaben zuerst auf; heutige Versuche
  ohne Generate-Zeile; /api/tags 500 „context canceled" im
  Wedge-Zustand; GPU-Discovery-0xc0000005 → CPU-Fallback (INFO,
  erwartet). /api/ps leer vor/nach (mehrfach). Kein stabiler
  App-Fix möglich (längere Timeouts/Retries helfen nicht gegen
  Server-Abbruch; Modellwahl ist Deployment).
- Nächste Schritte (Eigentümer, ohne Upgrade): 1) Ollama-Dienst
  über die offizielle App neu starten; 2) Modell vorwärmen
  (`ollama run qwen3-vl:2b`, kurze Frage), dann App-Probe;
  3) RAM-Last prüfen (16-GB-CPU-Box); 4) optional kleineres
  Textmodell für Tags (CONTEXTFLOW_OLLAMA_MODEL); 5) danach
  Browser-Check. Kein Downgrade/Upgrade, keine Security-Änderung.
- Protokoll-Fix 28.10. (Muse): lokale Text-/Vision-Pfade auf
  /api/chat umgestellt — CLI antwortet über Chat (10,4 s),
  Generate hängt mit Serverabbruch. Bodys: messages, stream:false,
  format:json, num_predict 768/512, think top-level; Parse
  message.content; Leer-/Timeout-Fehler und 503 + timeout:true
  erhalten; Meta-Pfad unverändert. Verifiziert: Vision+Tags
  56/56 (59,7 s), Discover 187 OK + 1 Skip (279,9 s), Gates grün.
  Kein Live-Erfolg behauptet; Live-Probe nach Warmstart offen.

## Streaming-Protokoll (Tests verifiziert, App-Live-Probe offen)

- Eigentümer-Beleg (warme direkte HTTP-Tests): think:false hängt;
  ohne think-Feld 200/done in 4,6 s; aber 64/768 Tokens füllen
  nur thinking, content bleibt leer. Alte 120-s-Hangs waren
  Sandbox-Proxy-Artefakt (Muse-Shell mit Proxy-Vars, direkter
  Socket WinError 10013) — kein Host-Defekt.
- Vertrag (nur lokaler Pfad): stream:true, think nirgendwo,
  num_predict 2048, NDJSON-Parser (nur content, done:true +
  Inhalt Pflicht, 256-KiB-/32K-Caps), No-Proxy-Seam
  `vision.local_urlopen` für Vision + Doc-Tags, deutsche
  Timeout-/Leer-/Unvollständig-Fehler. Meta-Pfad unverändert.
- Verifiziert: Vision 42/42 OK, Doc-Tags 20/20 OK
  (Eigentümer-Läufe nach Seam-Fix: Proxy-Invariant statt
  Handler-Zählung, ein Transport-Seam); Discover 193 OK +
  1 Skip (272,6 s, Muse-Lauf); compileall/Policy/Größen grün;
  diff sauber (Eigentümer). Kein Live-Aufruf in dieser Runde;
  finale synthetische App-Probe nach Warmstart offen.

## Model-Guard (Thinking-503 beobachtet, Fix per Tests verifiziert)

- Beobachtet (Eigentümer, synthetisch /doc-tag-suggest): 503 nach
  177,64 s — Thinking-Tokens fraßen den 256-KiB-Stream vor
  content. Nur qwen3-vl:2b installiert (/api/show: TEMPLATE
  {{ .Prompt }}, qwen3-vl-thinking); offiziell existiert
  qwen3-vl:2b-instruct (isThinking=false).
- Fix (nur lokal): `resolve_local_model` vor jeder Inferenz —
  Thinking erkannt → installierter Sibling oder schnelle 503 mit
  Pull-Anleitung (nie Download, nie Thinking-Lauf); Show-404 →
  Installations-Hinweis; Fail-open bei Probefehlern; effektives
  Modell im Antwortfeld `model`; Limits als Obergrenzen behalten.
- Verifiziert (Muse-Lauf): Guard 8/8, Vision 42/42, Doc-Tags
  24/24 (74 OK); Discover 205 OK + 1 Skip (288,9 s); Gates
  grün (File-Size per Test-Split eingehalten). Kein Live-Ollama
  aus Sandbox möglich; offen: Instruct-Install (Eigentümer) +
  genau eine App-Probe.

## Bild-Integration (Text live OK, Bild-Fallback per Tests)

- Live Text-Route ERFOLGREICH (Eigentümer-Shell, Instruct
  installiert, Renderer qwen3-vl-instruct, JSON-Chat 9,38 s):
  Flask-POST /doc-tag-suggest → 200 in 23,66 s,
  model=qwen3-vl:2b-instruct, „Lokale KI", mit Vorschlägen.
- Live-Befund Bild: /doc-image-suggest mit Smart-Folder-PNG →
  400 „nicht verfügbar" in 0,47 s vor Inferenz; Ursache
  V2-nur-Lesepfad (`storage_for`) statt DOCUMENT_ROOT-Dateien.
- Fix (nur doc_tags): V2 vorrangig, sonst Fallback über
  `resolve_file_under` (bounded 5 MiB, Traversal/Symlink-/
  Nicht-Datei-Schutz, PIL-validiert); nichts gespeichert.
- Verifiziert (Muse-Lauf): Doc-Tags 31/31 (7 neu inkl.
  Unverändert-Nachweis); fokussiert 81 OK + 1 Skip (73,7 s);
  Discover 212 OK + 2 Skips (328,9 s); Gates grün. Offen:
  synthetische Bild-Route live (Eigentümer).

## Prompt-Qualität (Bild live OK, Schärfung per Tests)

- Live Bild-Route ERFOLGREICH (Eigentümer): /doc-image-suggest
  mit synthetischem Parkbank-PNG → 200 in 82,38 s
  (qwen3-vl:2b-instruct, Datei-Fallback, Metadaten unverändert);
  Beschreibung gut, aber 7 Objekt-Labels inkl. Teile/Material
  (Sitzfläche, Holz, Höhe). Frühere Bild-400 bleibt behoben.
- Schärfung (nur doc_tags): Bild-Prompt ≤4 ganze Objekte (keine
  Teile/Materialien/Maße/Farben/Synonyme); Text-Prompt ≤5 Tags
  + `evidence`-Zitat (max. 160); Server-Caps 4/5; Evidence
  normalisiert gegen Dateiname + Text geprüft (erfunden →
  Eintrag verworfen, fehlend → kompatibel); Antwort plus
  `evidence`-Feld (UI unverändert kompatibel). Ein Modellaufruf,
  lokal, keine Mehrzeit.
- Verifiziert (Muse-Lauf): Doc-Tags 39/39 (8 neu: Wortlaut,
  Evidence-Match/Erfunden/Kanten, Caps, 2 Routen); fokussiert
  89 OK + 1 Skip (87,7 s); Discover 220 OK + 2 Skips (303,0 s);
  Gates grün; diff-check ausstehend. Offen: Qualitätswirkung
  live Folgelauf (Eigentümer).

## Grounding-Pflicht (Retest schwach, Härtung per Tests)

- Live-Retest (Eigentümer): Text 200/22,36 s, Tags mit
  Einkommen/Ausgaben/Kosten + Varianten trotz passenden Zitats
  (nicht in Quelle); Bild 200/64,64 s, Objekte [Stuhl,
  Sitzfläche, Stützen, Gestaltung] trotz Teile-Verbots.
- Härtung (nur doc_tags): Zitat Pflicht + Tag-Phrase muss
  wörtlich (normalisiert, zusammenhängend) in Dateiname/Text
  vorkommen; Bild ≤1 Hauptobjekt-Label (erstes = primär),
  Satz bleibt; kein Confidence-Feld (Schema/UI kompatibel).
  Weniger/keine Tags akzeptiert.
- Verifiziert (Muse-Lauf): Doc-Tags 41/41 (2 neu: Phrasen-
  Grounding, gemischte Route); fokussiert 91 OK + 1 Skip
  (75,7 s); Discover 222 OK + 2 Skips (281,1 s); Gates grün;
  diff-check ausstehend. Qualität NICHT abgeschlossen —
  Eigentümer-Proben beider Routen ausstehend, Ergebnis folgt.

## Qualitätsfilter (Live vor Filtern, Fix per Tests)

- Live VOR den neuesten Filtern (Eigentümer,
  qwen3-vl:2b-instruct, nur synthetisch): Text A →
  HTTP 400 in 26,56 s (keine gültigen grounded Tags);
  Text B → HTTP 200 in 18,82 s, Rohmodell [Rechnung,
  2024, Euro, aus, notizen] — „aus" (Füllwort) und
  „notizen" (generischer Dateiname-Stamm) kamen durch;
  Bild → HTTP 200 in 63,1 s, uneindeutig (stilisierter
  Holzstuhl oder -tisch), Objekte leer, Metadaten ok.
- Filter (nur doc_tags + eine JS-Statuszeile, ein
  Modellaufruf, lokal): gültiges JSON ohne überlebende
  Vorschläge → 200 + ok + leere Liste (statt 400);
  Stoppwort-Filter (Funktionswörter, min. 2 Zeichen);
  Dateinamen-Stämme nur ohne Text-Beleg (echter Beleg
  behält); Bild-Teile/Materialien/Attribute/Hintergrund
  fallen weg (MAX_OBJECTS 1 nur ganzes Objekt;
  Alltagsnomen wie Glas/Papier/Tür bleiben). Fehl-
  geformtes JSON bleibt 400. Nichts gespeichert.
- Verifiziert (Muse-Lauf, Sandbox-Kopie, nur fokussiertes
  Modul): Doc-Tags 46 Tests OK (5 neu: Stoppwörter,
  Stamm-Filter, Teile/Objekte, schwache Route,
  Teile-Route; 200-leer-Route umgestellt; 1
  vorbestehender Symlink-Skip; 94,1 s).
  Whitespace-Eigenprüfung der 3 Dateien sauber;
  git diff --check blockiert (Sandbox). Qualität NICHT
  abgeschlossen — eine frische Live-Probe je Route
  steht aus.

## Live-Abnahme Tag/Bild (Filter live-verifiziert)

- Final NACH Filtern (Eigentümer, lokal, synthetisch):
  Text 200/29,67 s nur [Rechnung, 2024, Euro] („aus",
  „notizen" weg); Bild 200/62,76 s leer/Metadaten ok;
  warmer Retry 200/5,31 s, Roh-JSON [Bank] → exakt
  [Bank] behalten. `git diff --check` Exit 0.
- Enge Tag/Bild-Route live-verifiziert. Uneindeutige
  Bilder können leer bleiben; breitere
  Integration/Browser-Abnahme offen.

## Bild-Label-Abgleich (Mismatch live, Fix per Tests)

- Live-Mismatch (Eigentümer, warme Bild-Route):
  Holzbrett-Satz bei Objekten [Bank] — Tag vom
  eigenen Satz nicht gedeckt (früher: Bank
  beschrieben, Objekte leer).
- Fix (nur doc_tags, kein Modellaufruf): Label muss
  wörtlich (normalisiert) im Satz vorkommen, sonst
  fällt es; nichts erschlossen, leer bleibt gültig.
  Prompt verlangt Wiederholung des Hauptobjekts.
- Verifiziert (Muse-Lauf): Doc-Tags 47 Tests OK
  (1 neu: Match/Mismatch/Teile-voran/leer; 1 Skip;
  92,2 s); Whitespace sauber; diff-check offen.
  Bild-Qualität offen bis finale Live-Routen-Probe.

## Konsistenz-Abnahme (Label-Satz-Abgleich live OK)

- Final NACH Abgleich (Eigentümer, lokal, synthetisch):
  Text 200/34,39 s [Rechnung, 2024], Tags unverändert;
  Bank kompakt 200/55,38 s [Stuhl] satztreu; Parkbank
  200/60,06 s exakt [Bank]; je Metadaten ok. Mismatch
  per Test abgewiesen.
- Enge Route für Text + klares Bank-Bild verifiziert;
  Mehrdeutigkeit: anderes Label oder keines. Browser-
  Abnahme offen (Automation gestoppt, Neustart
  abgelehnt) — nicht browser-verifiziert.

## Kontaktvorschlag (Text + bis 3 Fotos, 28.10.)

- Umfang: Vorschlagsbereich auf `/documents/contacts` aus getipptem Text
  plus bis zu 3 Fotos; Fotos werden lokal OCR-gelesen; an Meta geht nur
  Text/OCR-Text, keine Bildbytes. Belegte Vorschläge werden einzeln
  bestätigt; Speichern bleibt im normalen Kontaktformular.
- Meta-Pfad nur mit globaler Serverfreigabe UND nicht vorangekreuzter
  Anfrage-Einwilligung; lokal via Meta-Ollama oder extern via gepinnter
  Meta-API (`muse-spark-1.3-contributor`, Meta Model API).
- Tests (verifiziert im aufrufenden Supervisionslauf nach der letzten
  Provider-Änderung, nicht in dieser Doku-Session):
  `python -m unittest tests.test_contextflow_contact_suggest tests.test_contextflow_contact_suggest_meta -v`
  — 39 Tests, OK. Meta-Pfad mit gemocktem Transport.
- Gates (derselbe Supervisionslauf): `compileall`,
  `tools/check_project_policy.py .`, `tools/check_file_size.py . --limit 1000`,
  `tools/check_function_size.py app tools --limit 300` und `git diff --check`
  — alle Exit 0.
- Live (derselbe Supervisionslauf: `start.bat` auf synthetischem Root,
  Port 5099): Login POST 302, danach `/documents/contacts` 200; HTML enthält
  Vorschlagsbereich, Fotoauswahl und externe Meta-Checkbox.
- Kein Meta-API-Schlüssel vorhanden: kein echter Modellaufruf — kein
  Meta-Modellnachweis in dieser Runde. Kein Commit/Push; `.t/`
  unangetastet.
