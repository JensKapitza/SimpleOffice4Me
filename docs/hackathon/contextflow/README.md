# ContextFlow – README (Hackathon-Arbeit, Branch `hackathon/contextflow-mvp`)

**Stand:** 27.10.2026 — Batch 0–3 implementiert. Batch 1: 11/11 grün (Codex-verifiziert).
Batch 2: 30/31 gemeldet, Korrekturlauf eingebaut. UI-/Aktionstests: Ausführung durch
Codex ausstehend. Keine fertige Hackathon-Demo, kein Meta-Runtime-Einsatz.

## Was ist neu (nur diese Dateien)

- `app/contextflow/`: `contracts.py` (Evidence-Vertrag), `adapters.py`
  (actor-scoped Reads), `case_links.py` (bestätigte Zuordnungen),
  `evaluator.py` (5-Tage-Follow-up, deterministisch), `actions.py`
  (freigegebene Einmal-Aufgabe), `hints.py` (Snooze/Dismiss),
  `web.py` (Blueprint `/contextflow`), `demo.py` (synthetisches Seeding)
- `templates/contextflow/index.html`, `static/contextflow.css`
- `tools/contextflow_demo.py` (lokaler Demo-Starter)
- `tests/test_contextflow_*.py`, `tests/contextflow_fixtures.py`
- `docs/hackathon/contextflow/STATUS.md` (Alt-/Neu-Nachweis)
- `app/__init__.py` + `pyproject.toml`: nur Registrierung/Paket (kein Altcode geändert)

## Startanleitung (lokal, synthetisch)

Voraussetzung: Projekt-`.venv` mit installierten Dependencies (siehe `pyproject.toml`).

```bat
.venv\Scripts\python.exe tools\contextflow_demo.py --port 5088
```

- Legt `.runtime-tools/contextflow-demo/` an (mit Demo-Marker, sonst verweigert
  das Skript fremde/nicht-leere Roots und löscht nichts).
- Seeder erstellt Kontakt, Mail-Vorgang, abgeschlossenen Ortstermin mit zwei
  offenen Befunden, Rechnungsentwurf und bestätigten Link (5 Tage zurück).
- Eigene Demo-User-DB im Demo-Root, Benutzer `demo` (Zufallspasswort wird
  ausgegeben, nur Demo-Zugang, nur `127.0.0.1`).
- Danach: `http://127.0.0.1:5088/contextflow/` — Standard **aus** ohne Flag;
  das Skript setzt `CONTEXTFLOW_ENABLED=1`. Produktiv bleibt das Flag aus,
  alle Routen antworten dann 404.

## Tests

```bat
set TEMP=C:\Users\Jens Kapitza\AppData\Local\Temp
set TMP=C:\Users\Jens Kapitza\AppData\Local\Temp
.venv\Scripts\python.exe -m unittest tests.test_contextflow_adapters tests.test_contextflow_links tests.test_contextflow_evaluator tests.test_contextflow_actions tests.test_contextflow_web -v
.venv\Scripts\python.exe -m compileall -q app tools
.venv\Scripts\python.exe tools/check_project_policy.py .
.venv\Scripts\python.exe tools/check_file_size.py . --limit 1000
.venv\Scripts\python.exe tools/check_function_size.py app tools --limit 300
```

## Grenzen (ehrlich)

- Nur lokale Regel-Engine, kein Modell, kein Ollama, kein `MODEL_API_KEY` im
  Environment, kein API-Key aus Muse-Auth. Meta/Multimodal/LocalProvider erst
  nach stabilem lokalem P0.
- Der Büroengel ist ein Emoji/CSS-Platzhalter; echte Assets folgen nach dem MVP.
- Demo zeigt ausschließlich neue Hackathon-Arbeit (Baseline `af5a600b`).
