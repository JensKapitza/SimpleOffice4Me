# ContextFlow – STATUS (Neu-gegen-Alt-Nachweis)

**Baseline `origin/main`:** `af5a600bae1f526e61198edbec4d9022e742f228` (26.10.2026, Merge PR #868)
**Hackathon-Branch:** `hackathon/contextflow-mvp` (ab Baseline, `main` unberührt)
**Spezifikation (maßgeblich):** `../../../../update2.md` im Elternordner — keine Kopie im Repo.
**Stand:** 27.10.2026 — Batch 0–2 implementiert; Codex-Lauf gemeldet 30/31
(gesamt 31: 11 Adapter + 9 Links + 11 Evaluator; neu in Batch 2: 20).
Korrekturlauf danach eingebaut, Ausführung durch Codex ausstehend.

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
  Finale Nachprüfung durch Codex ausstehend. Kein Ergebnis erfunden.
- Vorbekannte, nicht von diesem Batch verursachte Baselinefehler:
  `test_contact_store` parallel_carddav (Resource-Deadlock-Avoid in
  `app/file_lock.py`, Windows); `test_site_visits` saved_report
  Download-Response blockiert `unlink` (ein Test übersprungen).

- Batch 2 (Links + Evaluator, 21 Tests): geschrieben, Ausführung durch Codex
  ausstehend. Rechte-Revokation zusätzlich per Mock-Frischetest geprüft, weil
  Visits owner-only sind und kein echter Multi-User-Fluss möglich ist; echte
  Ablehnung fremder Actors und Link-Trennung laufen auf echten Stores.
- `read_visit`-Evidence nutzt jetzt den kanonischen Datensatz-Hash, damit
  Quelländerungen (z. B. neuer Befund) den Hinweis-Fingerprint ändern.
- Korrekturlauf: `confirm(..., now=None)` mit injizierbarer aware Uhr (Default
  echte Berlin-Uhr), Tests/Fixtures geben NOW explizit weiter (hostunabhängig).
  Store-`_read` strikt (fehlend=leer, kaputt/falsches Schema=Fehler, kein
  Überschreiben). get/list prüfen frische Domain-Rechte (entziehbar getestet).
  Dedupe ohne Invoice-Anteil (Hinzufügen aktualisiert Link mit Audit).
  Nur kanonisches YYYY-MM-DD. Revokations-Regression mit geteiltem Kontakt,
  Mail-Teilnehmer und eigenem Manager-Visit.

- Batch 3 (UI + sichere Aktion, 2 neue Testdateien): geschrieben, Ausführung
  durch Codex ausstehend. Kein Modell, kein Ollama, kein API-Key im Spiel.
- 35/35 Batch-0–2 von Codex verifiziert (Policy/Größen/compileall/diff OK).

## Nächster einzelner Batch

Grüner Batch-3-Lauf, dann sichtbarer Demo-Ablauf auf Video-Skript. Danach
Meta/Multimodal/LocalProvider — erst nach stabilem lokalem P0.
