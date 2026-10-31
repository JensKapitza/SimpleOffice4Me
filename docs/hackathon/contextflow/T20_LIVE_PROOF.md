# T20 Live-Nachweis — Runbook (Eigentümer-Schritt)

Ziel (update2.md §10): **T20 Muse Spark Sandbox/Tool-Loop** —
echte Modellkennung, 2+ Lese-Tools, validierte Belege. Einziger
Wettbewerbs-Schritt mit echten Meta-API-Aufrufen; alles andere ist
bereits per Tests/Gates belegt.

## Budget (vorab lesen)

Ein Probe-Lauf kostet genau:

- 1× `GET /v1/models` (frei, nur Modellliste, keine Inferenz)
- höchstens 5× `POST /v1/responses` (Server-Schrittbudget, typisch 2–3)

Nur synthetische Demo-Daten verlassen den Rechner (Contributor-Modus:
Inhalte können zum Training künftiger Meta-Modelle verwendet werden —
darum verweigert der Server jede reale Wurzel).

## Voraussetzungen

- `MODEL_API_KEY` in `../.env` gefüllt (Stand 31.10.: gefüllt;
  Wert nie ausgeben, loggen oder committen).
- Branch `hackathon/contextflow-mvp` aktuell (enthält
  `tools/t20_live_probe.py` + Antwort-Modell-Erfassung).
- Freier Loopback-Port (unten: 5101), Internet-Zugang.

## Schritt 1 — Demo mit Meta-Provider starten

In `cmd.exe` im Repo-Ordner (zwei Befehle, Server läuft weiter):

```bat
set CONTEXTFLOW_VISION_PROVIDER=meta
start.bat demo --port 5101 --root %TEMP%\contextflow-t20-20261031 --fresh
```

Erwartete Banner-Zeilen:

- `Vision:   Meta Muse Spark 1.3 Contributor (Meta Model API)`
- `Daten:    ausschließlich synthetischer Demo-Root + Einwilligung`
- `Fall:     <link_id> (abgeschlossen ...)`

Abbruch: Fehlt die Vision-Zeile oder bricht der Start mit
`MODEL_API_KEY is missing` ab, `.env`-Pfad prüfen (direkt neben
dem Repo-Ordner, Key `MODEL_API_KEY=...`) und neu starten.
Kein Meta-Aufruf ist bis hierhin passiert.

## Schritt 2 — Probe starten (genau einmal klicken/laufen lassen)

Zweites Fenster, gleicher Ordner:

```bat
.venv\Scripts\python.exe tools\t20_live_probe.py --base http://127.0.0.1:5101
```

Die Probe meldet sich als Demo-Nutzer an, prüft
`GET /contextflow/ml-status` (Provider `meta`, Schlüssel da,
Serverfreigabe an, Modell gelistet), liest das Agenten-Formular
(Pills + bestätigter Fall) und startet **genau einen**
be-willigten Agenten-Lauf. Dauer: meist unter 3 Minuten
(Timeout 7 Minuten).

Erwartete Ausgabe (Beispielwerte):

```text
[1/5] login as demo ...
[2/5] ml-status preflight (one free models lookup) ...
[3/5] agent form (pills + case) ...
[4/5] ONE consented agent run (link ab12cd34..., up to 7 min) ...
[5/5] evidence written to C:\...\t20-evidence-20261031-....json
T20 PASS: steps=2 ok_tools=4 evidence=2 model=muse-spark-1.3-contributor response=muse-spark-1.3-contributor tokens=1200/502 seconds=10.2
```

## Schritt 3 — Beleg sichern

- Evidenz-Datei aus `[5/5]` aufheben (enthält keine Secrets:
  kein Key, kein Passwort, keine Cookies — Link-ID ist synthetisch).
- Browser-Gegenprobe (gleiche Sitzung): Ergebnis-Seite zeigt
  `Modell ... (Antwort: muse-spark-1.3-contributor)`, Werkzeugliste
  mit ✔, Feststellungen, Beleg-IDs und „Nur ein Vorschlag“.
- Werte an Muse zurückmelden: `steps`, `ok_tools`, `evidence`,
  `model`, `response`, `tokens`, `seconds` → STATUS-Nachtrag
  (Textvorschlag unten), T20 gilt erst dann als geschlossen.

## Abbruch- und Fehlercodes

| Ausgabe | Bedeutung | Nächster Schritt |
|---|---|---|
| `PROBE FAIL: ... 429 ...` | Server-Throttle (5/300 s) | Einmalig warten, Probe einmal wiederholen |
| `PROBE FAIL: ... 409 ...` | Lauf bereits aktiv (Single-Flight) | Abwarten, einmal wiederholen |
| `T20 FAIL: ... tool calls ...` | Modell rief < 2 Tools ok | Einmal wiederholen (Modell-Varianz); bei 2. FAIL melden |
| `T20 FAIL: ... response model ...` | Antwort-Modell fehlt/weicht ab | Melden (kein weiterer Lauf ohne Absprache) |
| `502` auf der Ergebnis-Seite | Meta-Fehler (z. B. 429/500 der API) | Einmal wiederholen; Datei + Uhrzeit notieren |
| `403` auf der Ergebnis-Seite | Gate (Einwilligung/Root/Key) | Probe bricht vorher ab — Ablauf melden |

Grundsatz: höchstens **zwei** Läufe pro Freigabe. Jeder weitere
Lauf braucht eine neue ausdrückliche Freigabe (Budget).

## Nach PASS: STATUS-Nachtrag (vorbereitet, erst nach Beleg einpflegen)

```text
T20 Meta-Live-Anteil erledigt (t20_live_probe, <Datum>):
1 bewilligter Lauf, <steps> Schritte, <ok_tools> ok-Tools
(<Namen>), <evidence> Beleg-IDs, Modell + Antwort-Modell
muse-spark-1.3-contributor, Tokens <in>/<out>, <s> s.
Evidenz: t20-evidence-<Stempel>.json (eigentümerseitig).
G Jury-Stufe-1-Gate damit per Beleg erfüllt.
```
