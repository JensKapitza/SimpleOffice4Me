# ContextFlow Hinweis-Klarheit — 30.10.2026 (Audit-Folgeauftrag)

Eigener Arbeitsstand aus dem Muse-Worktree `contextflow-hint-ux-20261030`.
Gemeinsame WORKLOG-/STATUS-Dateien wurden nicht angefasst. Kein Commit,
kein Push, kein Rebase (Eigentümer testet und committet geprüft).

## Lese-Phase (zuerst, Meta-only eingehalten)

- 204 Markdown-Dateien gelistet. Vollständig gelesen: `AGENTS.md`
  (Session-Kontext), `docs/hackathon/contextflow/README.md`,
  `OLLAMA.md`, `STATUS.md` (Kopf + Fundstellen),
  `LIVE_VERIFICATION_2026-10-30.md`, `architecture.svg`,
  Root-`README.md` (Büroengel-Abschnitt), `WORKLOG_2026-10-26.md`
  (Fundstellen), `tests/contextflow_fixtures.py`, `app/file_lock.py`,
  alle geänderten Code-/Test-Regionen. Rest per Dateiliste +
  Begriffs-Scan (`Ruhezeit`, `Tageslimit`, `Sammelliste`,
  `Umschreibung`, `Schreibpfad`, `ContextFlow`):
  keine weiteren normativen Aussagen zur Änderungsfläche.
- Laufzeit bleibt Meta-only; keine fremden Modelle, keine externen
  API-Aufrufe in dieser Arbeit.

## Blocker 1: Tageslimit (behoben)

- Ursache: `HintBudget.allow()` prüfte den Stand vor der Schleife,
  `record()` schrieb erst danach — erster GET mit mehr Links als das
  Limit zeigte alle.
- Fix `app/contextflow/hints.py`: `reserve()` prüft und schreibt
  atomar unter File-Lock + Prozess-Lock; Route
  (`app/contextflow/web.py`, Dashboard) reserviert erst nach
  erfolgreicher Evaluation/Kartenerstellung; reservierte IDs bleiben
  bei Reload sichtbar. `allow()`/`record()` unverändert erhalten.
- Tests: Store (`reserve`-Limit/Stabilität/Isolation/Garbage/Threads)
  + Route (erster GET zeigt max. Limit, Reload stabil).

## Blocker 2: partielle Stores (behoben)

- Ursache: `_read()` akzeptierte `{}`, danach KeyError in
  `set`/`record`. Gleicher Fehler zusätzlich in `HintStates`
  gefunden und mitbehoben.
- Fix: alle drei `_read` normalisieren fehlende/falsche
  Collection-Keys auf `[]`; `record`/`reserve` teilen sich
  `_mine`/`_prune`/`_shown_of` (defensive `shown`-Werte).
- Tests: neue Klasse `StoreRobustnessTests` (je Store 5
  beschädigte/partielle Objekte).

## Blocker 3: Gruß-Umschreibung (entfernt, Entscheidung korrigiert)

- Frühere Notiz (Umschreibung behalten) ist überholt: Review misst
  bis 8 s Blockade pro Dashboard-GET; Status „bereit" log bei
  Ollama-Ausfall.
- Dashboard zeigt nur noch statischen `RULE_GREETING` (jetzt in
  `web.py`), Pille statisch „Lokaler Regelbetrieb · Regeltext, keine
  KI-Abfrage" (keine Modellprobe). Gelöscht: `local_rewrite.py`,
  `_greeting_status`, `RewriteTests`, `GreetingStatusTests`,
  modellabhängige Routen-Tests, `greeting_status`-Plumbing.
  Lokale Meta-Inferenz bleibt bei Foto-/Dokument-/Inventaraktionen
  (eigene Routen mit `_vision_settings`, unverändert).
- Tests belegen: Dashboard mit blockiertem `local_urlopen` antwortet
  200 mit Regeltext (keine Inferenz möglich).

## architecture.svg (behauptung begrenzt)

- „Freigabe = einziger Schreibpfad" + „Sammelliste bei Limit" →
  „Meta-Agent: nur Vorschlag, Freigabe vor Ausführung" +
  „Limit: Hinweise zurückgestellt". „Gruß-Umschreibung" →
  „Regeltext-Gruß (statisch)". Die pauschale Aussage in Zeile 5
  („alle Pfeile lesend außer Freigabe") wurde ebenfalls eingegrenzt:
  „ContextFlow: Agent-Aktionen erst nach Freigabe".

## Verifikation

- Im Muse-Sandboxlauf fehlte Python. Anschließend in der Projekt-`.venv`
  ausgeführt: `tests.test_contextflow_hint_prefs` **30/30 bestanden**.
  Abgedeckt sind Budget, Store-Robustheit, Ruhezeiten, Einstellungen
  und Dashboard-Routen.
- `git diff --check` im Haupt-Checkout: sauber. Alle geänderten Dateien
  sind UTF-8 ohne BOM und mit LF; keine toten Refs (`local_rewrite`, `rewrite_hint_lead`,
  `greeting_status`, `complete_text` in Tests); `local_rewrite.py`
  gelöscht bestätigt; alle Edit-Regionen per Re-Read geprüft.
- Eigenes Missgeschick offengelegt: Testdatei durch fehlgeschlagenen
  BOM-Strip kurz 0 Bytes; vollständig aus verifizierten Ständen
  rekonstruiert (387 Zeilen, 18516 Bytes, exakt 2× `ü`, kein BOM).
- Kein Commit (beauftragt: Eigentümer committet geprüft) — keine SHA.
- Geteilte Doku (`STATUS.md`, `WORKLOG`, ContextFlow-`README.md`,
  `OLLAMA.md`) beschreibt teils noch Umschreibung/Sammelliste —
  bewusst nicht angefasst (nicht beauftragt); bei Bedarf nachziehen.
