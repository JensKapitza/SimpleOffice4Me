# ContextFlow – README (Hackathon-Arbeit, Branch `hackathon/contextflow-mvp`)

**Stand:** 28.10.2026 — ContextFlow 114 OK + 1 Skip, Inventar 55 OK,
Gates OK (Eigentümer-Lauf nach Timeout-Code); lokale Inferenz
(Ollama qwen3-vl:2b) live auf 127.0.0.1:5091 verifiziert;
Scan-zu-Foto-UX implementiert, Lauf ausstehend. Kein
Meta-Runtime-Einsatz, keine Cloud.

## Produktziel und Abnahmekriterien (geteiltes Ziel, aus update2.md)

- Zielsatz: SimpleOffice4Me erkennt offene Arbeitsschritte über
  E-Mail, Kunden, Ortstermin, Befunde, Aufgaben, Rechnung und
  Kundenkommunikation hinweg selbst, belegt sie, spricht sie über
  den Büroengel freundlich an und lässt sie nach menschlicher
  Freigabe bearbeiten — ohne Cloud-Zwang im Normalbetrieb.
- Architektur: SimpleOffice4Me (Fach-Wahrheit) + ContextFlow
  (Verbindungsschicht) + Büroengel (sichtbare Figur) + lokale
  Ollama-KI (Texte/Klassifikation) + Meta-Muse-Spark-Agent
  (echt, mehrstufig, nur synthetisch-isoliert).
- P0-Umfang: verknüpfter Servicefall (A), 5-Tage-Follow-up (B),
  Engel-Hinweis mit Quellen (C), Aufgabe-genau-einmal +
  Rechnungseditor-Link (D), Mail-Entwurf ohne Autoversand (E),
  kostenlose lokale Betriebsart (F), echter Meta-Agent (G),
  Security/QA/Alt-Neu (H); danach 3-Minuten-Jury-Demo.
- Abnahme: P0-A–H je umgesetzt, getestet und in der Demo
  nachgewiesen (nur synthetische Daten, nur neue Arbeit); keine
  erfundenen Fakten, keine Autonomie bei Geld/Externem.
  Umsetzungsstand: Matrix in STATUS.md.
- Erweiterung (Inventar-Agent, 28.10., update2.md §13, Stand: TEILWEISE):
  umgesetzt und per Tests verifiziert (161/79 + Gates OK, kein
  Auto-Speichern): Serien-Queue/UI, lokale Pro-Foto-Vorschläge,
  Pro-Kandidat-Kennung, Exakt-Match, GTIN-Härtung,
  KI-ISBN-Schema/Prompt/Validierung, Foto-Anhang und Serien-Abschluss
  (JSON-Einzelspeichern, Idempotenz, Regal-Retain, Checkliste,
  Buch/Gegenstand-Weiche, Reset; 18 + 2 Tests). Nicht live-verifiziert:
  reale ISBN-Erkennungsqualität und Kamera-Check. Zusätzlich:
  Freund-Bestandscheck 15/15 verifiziert (Muse-Lauf, inkl.
  Sync-State + ISBN-Prefilter). Ollama-Streaming + Model-Guard
  umgesetzt + verifiziert (Instruct-Sibling/Refusal, nie Download;
  Vision 42/42, Guard 8/8, Discover 205 OK + 1 Skip;
  Details OLLAMA.md). Matrix in STATUS.md.
- Erweiterung (Import-Tag-Engel, 28.10., update2.md §14, Stand: TEILWEISE):
  umgesetzt und per Tests verifiziert: lokale Tag-Vorschläge mit
  Begründung aus Metadaten + Extraktion, Bildbeschreibung/
  Objekthinweise, Bestätigungs-UI in der Smart-Folder-Ansicht,
  keine Auto-Übernahme. Doc-Tags 46 Tests OK + 1 Skip
  (nur fokussiertes Modul, Muse-Lauf); Vision 42/42,
  Guard 8/8, Discover 222 OK + 2 Skips (Vorrunden).
  Live VOR den neuesten Filtern: Text A 400/26,56 s
  (keine gültigen Tags), Text B 200/18,82 s („aus" und
  „notizen" durchgerutscht), Bild 200/63,1 s (uneindeutig,
  Objekte leer, Metadaten ok) → Qualitätsfilter (200-leer,
  Stoppwörter, Dateinamen-Stämme, Teile/Material/Attribut
  weg; ein Modellaufruf, lokal) per Tests verifiziert.
  Finale Live-Abnahme: Text 200/29,67 s nur [Rechnung,
  2024, Euro]; Bild 200/62,76 s leer/Metadaten ok,
  warmer Retry 200/5,31 s [Bank] behalten; diff-check
  Exit 0 — enge Route live-verifiziert (unsichere Bilder
  können leer bleiben). Bild-Mismatch live
  (Holzbrett-Satz/[Bank]) → Label-Abgleich per Tests
  (47 OK/1 Skip, diff-check offen); Konsistenz-Abnahme:
  Text 200/34,39 s [Rechnung, 2024], Bank 200/55,38 s
  [Stuhl] + 200/60,06 s [Bank] — enge Route verifiziert
  (Mehrdeutigkeit: anderes/kein Label). Browser-Abnahme
  offen (Automation gestoppt, Neustart abgelehnt).
  UI meldet ehrlich. Details OLLAMA.md.
  Nicht live-verifiziert: Browser-Check. Matrix in STATUS.md.

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
- `app/contextflow/vision.py` + `templates/contextflow/suggest.html`
  (Foto→Entwurf/Vorschlag, lokal/Mock/Meta-Gates)
- `docs/hackathon/contextflow/OLLAMA.md` (lokale Modell-Einrichtung)
- `tests/test_contextflow_demo_vision.py` (Demo-Startkonfiguration)
- Inventar-UI geändert: `templates/inventory/index.html`,
  `static/js/inventory.js` (Foto-Buttons, Vorschlag-Box, Non-ISBN-Ablauf)
- Inventar-Mitarbeit: `/inventory` bietet (nur Flag + `documents`-Recht)
  Foto-Buttons (Aufnehmen/Auswählen) + Fotoanalyse mit Vorschlag-Box
  (explizit Übernehmen/Verwerfen, kein Auto-Speichern);
  `POST /contextflow/inventory-suggest` liefert JSON nur aus echten
  lokalen Modellen (Mock→503, Cloud verweigert); Speichern unverändert
  über das Formular; Dashboard kennt weiter `?origin=inventory`

## Startanleitung (lokal, synthetisch)

Voraussetzung: Projekt-`.venv` mit installierten Dependencies (siehe `pyproject.toml`).

```bat
start.bat demo --port 5088
```

(Alternativ direkt: `.venv\Scripts\python.exe tools\contextflow_demo.py --port 5088`)

- Legt `.runtime-tools/contextflow-demo/` an (mit Demo-Marker, sonst verweigert
  das Skript fremde/nicht-leere Roots und löscht nichts).
- Seeder erstellt Kontakt, Mail-Vorgang, abgeschlossenen Ortstermin mit zwei
  offenen Befunden, Rechnungsentwurf und bestätigten Link (5 Tage zurück).
- Eigene Demo-User-DB im Demo-Root, Benutzer `demo` (nicht `admin`),
  festes Demo-Passwort `demo-contextflow-1` (nur Demo-Zugang, nur `127.0.0.1`).
  Bei Anmelde-Sperre kurz warten oder mit `--fresh` neu starten.
- Danach: `http://127.0.0.1:5088/contextflow/` — Standard **aus** ohne Flag;
  das Skript setzt `CONTEXTFLOW_ENABLED=1`. Produktiv bleibt das Flag aus,
  alle Routen antworten dann 404.
- Für echte lokale Fotoanalyse: Ollama + Modell einrichten, siehe
  `docs/hackathon/contextflow/OLLAMA.md` (`CONTEXTFLOW_OLLAMA_MODEL`;
  ohne Modell nur Mock-Befunddemo, Inventar antwortet 503).

## Tests

```bat
set TEMP=C:\Users\Jens Kapitza\AppData\Local\Temp
set TMP=C:\Users\Jens Kapitza\AppData\Local\Temp
.venv\Scripts\python.exe -m unittest tests.test_contextflow_adapters tests.test_contextflow_links tests.test_contextflow_evaluator tests.test_contextflow_actions tests.test_contextflow_web tests.test_contextflow_device_prefill tests.test_contextflow_vision tests.test_contextflow_demo_reset tests.test_contextflow_demo_vision tests.test_contextflow_inventory_entry -v
.venv\Scripts\python.exe -m unittest tests.test_auth_login_qr tests.test_invoice_relations -v
.venv\Scripts\python.exe -m compileall -q app tools
.venv\Scripts\python.exe tools/check_project_policy.py .
.venv\Scripts\python.exe tools/check_file_size.py . --limit 1000
.venv\Scripts\python.exe tools/check_function_size.py app tools --limit 300
```

## Grenzen (ehrlich)

- Lokale Inferenz via Ollama auf Loopback (live-verifiziert mit
  qwen3-vl:2b, siehe OLLAMA.md); kein `MODEL_API_KEY`, kein API-Key
  aus Muse-Auth, kein Cloud-Pfad in der Demo. Meta-Runtime nicht live.
- Der Büroengel nutzt einen SVG-Platzhalter; echte Assets folgen nach dem MVP.
- Demo zeigt ausschließlich neue Hackathon-Arbeit (Baseline `af5a600b`).
