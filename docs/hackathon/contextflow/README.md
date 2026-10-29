# ContextFlow – README (Hackathon-Arbeit, Branch `hackathon/contextflow-mvp`)

**Stand:** 28.10.2026 — Architektur-Gate auf Meta-only-Laufzeit umgestellt;
Meta Model API und Meta-Ollama-Allowlist in Code und Demo-Starter verankert.
Qwen-Live-Läufe in älteren Notizen sind historische, nicht mehr zulässige
Ergebnisse und kein unterstützter Runtime-Pfad. Testergebnisse zum neuen Gate
stehen weiter unten.

## Hackathon-Laufzeitregel (verbindlich)

- Coding-Werkzeuge und Entwicklungsmodelle sind frei wählbar.
- Der ContextFlow-Wettbewerbspfad ruft ausschließlich Meta-Modelle auf. Die Laufzeit
  verweigert beliebige Ollama-Modellnamen sowie fremde Cloud-Endpunkte.
- Für lokale Textaufgaben ist `llama3.2:1b` (oder `llama3.2:3b`) via Ollama
  zugelassen. Diese Llama-3.2-Modelle sind text-only. Bildanalyse braucht
  `llama3.2-vision` oder `muse-glimmer`.
- Die sichtbare Meta-Cloud-Demo ruft `muse-spark-1.3-contributor` über den offiziellen Responses-Endpunkt
  Meta Model API-Endpunkt auf. Sie braucht `MODEL_API_KEY`, die ausdrückliche
  Einwilligung im Formular und den isolierten synthetischen Demo-Root.
  Echtdaten werden für diesen Pfad abgelehnt.
- Contributor-Modus (`muse-spark-1.3-contributor`): Inhalte können laut
  Meta-Doku zum Training künftiger Meta-Modelle verwendet werden. Die UI
  kennzeichnet das direkt an der Einwilligung; Cloud-Läufe sind auf
  synthetische Demo-Daten beschränkt, echte Kontaktdaten erreichen
  diesen Pfad nie.
- Das verwendete Modell steht im Startprotokoll und im Ergebnis der Bildanalyse.
  Die Meta-AI-Tooling-Wertung (25 %) wird nur mit einem tatsächlichen Meta-Aufruf
  belegt; der Mock ist ein Bedien-Dummy und zählt nicht als KI-Nachweis.
- Modellnachweise: [Meta Model API](https://dev.meta.ai/docs/overview),
  [Llama 3.2 Textmodelle](https://ollama.com/library/llama3.2),
  [Llama 3.2 Vision](https://registry.ollama.com/blog/llama3.2-vision),
  [Muse Glimmer](https://registry.ollama.com/library/muse-glimmer).

## OCR/ML-Betrieb, Status und Demo-Dateien (29.10.)

- OCR bleibt lokal: RapidOCR läuft über das venv-Extra (`.\.venv\Scripts\python.exe
  -m pip install .[ocr]`, falls `pip` fehlt vorher `python -m ensurepip`),
  Tesseract über das UB-Mannheim-Setup plus `PATH`. Fehlt eine Engine, nennt die
  Fehlermeldung den exakten Installationsschritt; `ocr_availability()` meldet
  beide Backends als Bools.
- Status: `GET /contextflow/ml-status` liefert Provider, Ollama-Erreichbarkeit
  plus installiertes Modell, Meta-Key nur als Vorhanden-Bool und OCR-Bools —
  nie Schlüssel oder Tracebacks. Ein dezentes Badge oben beim vCard-Export
  zeigt den Stand an.
- Bildanalyse: Detailseite (Bilder) und freigegebene EML-Anhangsbilder nutzen
  `/doc-image-suggest` (echt, lokal, Mock→503); die Antwort zeigt Provider,
  Modell, Beschreibung und Objekte. Unreleased Anhänge bleiben bis zur Freigabe
  gesperrt (Quarantäne-Grenze, kein Scan-Bypass).
- Demo-Dateien: `seed_demo_documents` importiert PNG/TXT/MD/PDF mit bekanntem
  synthetischem Inhalt idempotent nur in Demo-Roots und erzeugt Vorschauen
  vorab, weil der Demo-Modus keinen Index-Worker startet. Der Starter druckt
  die Detail-URLs mit Vorschau-Status.
- Ehrlichkeit: Ohne Modell/Schlüssel antworten ML-Routen 503 mit Grund; Mock
  gilt nie als echte Analyse. Echte-Inferenz- und Browser-Abnahme sind offen
  und laufen auf dem Host des Eigentümers.

## Produktziel und Abnahmekriterien (geteiltes Ziel, aus update2.md)

- Zielsatz: SimpleOffice4Me erkennt offene Arbeitsschritte über
  E-Mail, Kunden, Ortstermin, Befunde, Aufgaben, Rechnung und
  Kundenkommunikation hinweg selbst, belegt sie, spricht sie über
  den Büroengel freundlich an und lässt sie nach menschlicher
  Freigabe bearbeiten — ohne Cloud-Zwang im Normalbetrieb.
- Architektur: SimpleOffice4Me (Fach-Wahrheit) + ContextFlow
  (Verbindungsschicht) + Büroengel (sichtbare Figur) + Meta-Modelle lokal via
  Ollama oder Muse Spark über Meta Model API (nur synthetisch-isoliert).
- P0-Umfang: verknüpfter Servicefall (A), 5-Tage-Follow-up (B),
  Engel-Hinweis mit Quellen (C), Aufgabe-genau-einmal +
  Rechnungseditor-Link (D), Mail-Entwurf ohne Autoversand (E),
  kostenlose lokale Betriebsart (F), echter Meta-Modell-Aufruf im
  Foto-Befundfluss (G; mehrstufige Werkzeugnutzung bleibt offen),
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

### 3-Minuten-Jury-Demo: tatsächliche Meta-Inferenz zeigen

Die Meta-AI-Tooling-Wertung beträgt 25 %. Für den sichtbaren Cloud-Aufruf
ein neues PowerShell-Terminal verwenden und den API-Schlüssel nur als
Prozess-Umgebungsvariable setzen (nicht in Dateien oder Git speichern):

```powershell
$env:CONTEXTFLOW_VISION_PROVIDER = "meta"
$env:MODEL_API_KEY = "<Meta-Model-API-Schlüssel>"
.venv\Scripts\python.exe tools\contextflow_demo.py --port 5088 --fresh
```

Der Start muss `Meta Muse Spark 1.3 Contributor (Meta Model API)` ausgeben. Dann im
Browser unter `http://127.0.0.1:5088/contextflow/` anmelden (`demo` /
`demo-contextflow-1`) und in drei Minuten zeigen:

1. **0:00–0:30:** Startausgabe und UI-Modellkennung `Meta Muse Spark 1.3 Contributor`
   zeigen. Demo-Root und Beispieldaten sind synthetisch.
2. **0:30–1:30:** Im Foto-Formular einen synthetischen Ortstermin wählen,
   ein unkritisches Demo-Bild auswählen, Meta-Cloud-Einwilligung markieren
   und den Vorschlag starten. Das Ergebnis nennt als Provider
   `Meta Muse Spark 1.3 Contributor (Meta Model API)`.
3. **1:30–2:00:** Titel, Befundart und Sicherheit prüfen; ohne Bestätigung
   wird nichts gespeichert. Den Entwurf danach ausdrücklich übernehmen.
4. **2:00–3:00:** Die bestätigte Arbeit mit Quellen im Büroengel zeigen,
   eine freigegebene Aufgabe anlegen und die einmalige Ausführung erklären.
   Geld- oder externe Aktionen werden nicht automatisch ausgelöst.

Ohne Meta-API-Schlüssel darf die Jury-Demo nicht als echte Meta-Inferenz
bezeichnet werden. Als lokale Alternative für die Bildaufgabe kann `muse-glimmer`
oder `llama3.2-vision` über Ollama laufen; der Mock ist nur für UI- und
Workflow-Durchläufe.

## Tests

```bat
set TEMP=C:\Users\Jens Kapitza\AppData\Local\Temp
set TMP=C:\Users\Jens Kapitza\AppData\Local\Temp
.venv\Scripts\python.exe -m unittest tests.test_contextflow_adapters tests.test_contextflow_links tests.test_contextflow_evaluator tests.test_contextflow_actions tests.test_contextflow_web tests.test_contextflow_device_prefill tests.test_contextflow_vision tests.test_contextflow_model_guard tests.test_contextflow_doc_tags tests.test_contextflow_demo_reset tests.test_contextflow_demo_vision tests.test_contextflow_inventory_entry -v
.venv\Scripts\python.exe -m unittest tests.test_auth_login_qr tests.test_invoice_relations -v
.venv\Scripts\python.exe -m compileall -q app tools
.venv\Scripts\python.exe tools/check_project_policy.py .
.venv\Scripts\python.exe tools/check_file_size.py . --limit 1000
.venv\Scripts\python.exe tools/check_function_size.py app tools --limit 300
```

## Kontaktvorschlag (Text + bis zu 3 Fotos, Stand 28.10.2026)

- `/documents/contacts` zeigt einen Vorschlagsbereich: getippter Text plus
  bis zu 3 Fotos erzeugen einzelne, namentlich bestätigte Feldvorschläge.
  Fotos werden nur lokal per OCR gelesen; an Meta geht ausschließlich
  Text/OCR-Text, niemals Bildbytes.
- Meta-Pfad nur mit globaler Serverfreigabe UND nicht vorangekreuzter
  Einwilligungs-Checkbox pro Anfrage; Backend entweder lokale
  Meta-Ollama-Alternative oder externe gepinnte Meta-API
  (`muse-spark-1.3-contributor` über Meta Model API). Speichern bleibt Sache des
  normalen Kontaktformulars.
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
- Kein Meta-API-Schlüssel vorhanden: kein echter Modellaufruf.
  Der Mock zählt nicht als Meta-Modellnachweis (vgl. Laufzeitregel oben).

## Grenzen (ehrlich)

- Der neue Meta-Pfad ist durch feste Endpoint-/Modell-Gates und
  transportgemockte Tests abgesichert. Ein Live-Aufruf mit echtem
  `MODEL_API_KEY` ist in dieser Arbeitsrunde nicht erfolgt.
- Der Meta-Aufruf ist derzeit eine einzelne Fotoanalyse, die einen
  überprüfbaren Befundentwurf liefert. Ein allgemeiner, mehrstufiger
  Meta-Agent mit Tool-Aufrufen ist damit noch nicht nachgewiesen.
- Llama 3.2 1B/3B sind nur Textmodelle; lokale Bildanalyse braucht ein
  Vision-Modell. Meta-Cloud-Bildanalyse ist nur im isolierten synthetischen
  Demo-Root nach Einwilligung freigegeben.
- Der Büroengel nutzt einen SVG-Platzhalter; echte Assets folgen nach dem MVP.
- Demo zeigt ausschließlich neue Hackathon-Arbeit (Baseline `af5a600b`).
