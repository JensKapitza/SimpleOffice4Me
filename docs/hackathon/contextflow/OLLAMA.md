# Lokale Meta-Modelle für die ContextFlow-Demo (Ollama)

> **Wettbewerbsregel:** ContextFlow akzeptiert zur Laufzeit ausschließlich
> Meta-Modelle. Die nachfolgenden Qwen-Live-Logs sind historische Diagnose- und
> Entwicklungsbelege von vor dem Meta-only-Gate; sie beschreiben keinen
> zulässigen oder unterstützten Inferenzpfad.

## Einrichten

1. Ollama über den Hersteller installieren und starten
   (Standard: `http://127.0.0.1:11434`).
2. Für lokale Bildanalyse ein Meta-Vision-Modell laden:
   `ollama pull llama3.2-vision` (11B, benötigt laut Ollama mindestens
   8 GB VRAM) oder `ollama pull muse-glimmer` (30B, multimodal).
   Für reine Textaufgaben wie Tag-Vorschläge genügt
   `ollama pull llama3.2:1b`; dieses Modell kann keine Bilder analysieren.
3. Demo mit Modell starten (Repo-Root, Projekt-venv):
   `$env:CONTEXTFLOW_OLLAMA_MODEL="llama3.2-vision"` (PowerShell),
   dann `.venv/Scripts/python.exe tools/contextflow_demo.py --port 5091`

Optional: `CONTEXTFLOW_OLLAMA_URL` (Default `http://127.0.0.1:11434`),
`CONTEXTFLOW_OLLAMA_TIMEOUT` in Sekunden (Default `180`).

## Verhalten

- Ein freigegebenes Meta-Modell gesetzt → Provider `local`: Fotoanalyse und
  Befundentwürfe laufen über das lokale Ollama-Modell. `llama3.2:1b` ist nur
  für Text-Aufgaben zugelassen; Bildrouten verweigern es vor dem Aufruf.
  Hinweis: der erste Aufruf nach Modellstart (Kaltstart) kann auf CPU
  deutlich über 60 s dauern — einfach warten bzw. erneut versuchen;
  danach antwortet das warme Modell schneller.
- MODEL leer/fehlend → Provider `mock`: nur Befunddemo;
  `/contextflow/inventory-suggest` antwortet 503 mit deutschem Hinweis.

Im lokalen Provider bleiben Bild und Text auf 127.0.0.1. Nicht-Meta-Modelle
werden bereits bei der Konfiguration abgewiesen. Für den getrennten, nur
synthetischen Meta-Cloud-Demo-Pfad siehe `README.md`.

## Anfrage-Format (Streaming, /api/chat, kein think)

- Jede lokale Chat-Anfrage sendet `messages: [{role: user,
  content: prompt, images: [base64] nur Vision}]`, `stream: true`,
  `format: "json"`, `options: {num_predict: 2048}` — das Feld
  `think` wird vollständig weggelassen (weder top-level noch in
  `options`), damit das ausgewählte Meta-Modell seinen unterstützten
  Standardpfad verwendet. 2048 Tokens lassen Raum für kurze JSON-Antwort
  im 4096-Fenster (Prompts begrenzt: Text ≤4000 Zeichen).
- Geparst wird inkrementell NDJSON: nur `message.content` wird
  akkumuliert, `message.thinking` ignoriert; `done: true` plus
  nichtleerer Inhalt sind Pflicht. Caps: 256 KiB Stream-Bytes,
  32K Zeichen Inhalt. Transport ohne Proxy (direkter Loopback,
  keine Dokument-Bytes über Proxy).
- Fehler deutsch: Timeout → 503 + `"timeout": true`; leer →
  „leer geantwortet" (400); unvollständig/zu lang → 503;
  beschädigt → 400. Exakte Body-/Parser-Tests.
- Meta-Model-Gate vor jeder Inferenz: ausschließlich die explizite
  Ollama-Allowlist (Text: Llama 3.2 1B/3B; Bild: Llama 3.2 Vision
  oder Muse Glimmer) ist zulässig. Ein text-only Modell wird auf
  Bildrouten vor dem Netzwerkaufruf abgewiesen. `/api/show` prüft nur,
  ob das Modell installiert ist; `404` gibt eine `ollama pull`-Anleitung,
  andere Probe-Fehler fallen auf den freigegebenen Modellnamen zurück.

## Historische Live-Belege (28.10., Qwen vor dem Meta-only-Gate)

- 60-s-Budget: 503 Zeitlimit nach 60,2 s, ok:false.
- 180-s-Budget: 503 „Remote end closed connection without
  response" nach 120,2 s, ok:false.
- Diagnose: /api/ps leer vor und nach den Proben (Modell lädt
  nie), /api/tags „context canceled", Modell per /api/show
  vorhanden (Ollama 0.40.12).
- Kein erfolgreiches Live-Tagging; UI meldet Timeout/Fehler ehrlich.

## Diagnose 28.10. (Host-Blocker, kein App-Fehler)

- Direkt: /api/show 200 in 0,05 s (thinking-Default true);
  Mini-Generate (think:false, num_predict 64) → Serverabbruch
  ohne Antwort nach 120,0 s.
- App-Retest (korrigierter Code): 503 Zeitlimit nach 60,2 s,
  ok:false, timeout:true.
- Log: 200er möglich (6,67 s warm, 1m10s, 54,17 s); 500er exakt
  an Client-Timeouts (1m0s/2m0s/59,95s); heutige Versuche ohne
  Generate-Zeile; /api/ps leer; GPU-Discovery-Fehler →
  CPU-Fallback (erwartet).
- Kein stabiler App-Fix möglich. Nächste Schritte (Eigentümer):
  1) Dienst über offizielle App neu starten; 2) Modell vorwärmen
  (`ollama run qwen3-vl:2b`); 3) RAM-Last prüfen; 4) ggf.
  kleineres Textmodell für Tags; 5) danach App-Probe +
  Browser-Check. Kein Upgrade, keine Security-Änderung.

## Warmstart-Versuch 28.10. (ab Sandbox nicht möglich)

- Beabsichtigter Befehl (PowerShell, ≤180 s):
  `& 'C:\Users\Jens Kapitza\AppData\Local\Programs\Ollama\ollama.exe' run qwen3-vl:2b 'Reply with only: OK'`
- Ergebnis: sofort „Zugriff verweigert" — die Sandbox blockiert
  das Ausführen von ollama.exe aus der Muse-Shell (wie zuvor).
  Kein Warmstart erfolgt, keine Antwort, keine Wiederholung ab hier.
- Nächster Schritt (Eigentümer, eigene PowerShell): obigen Befehl
  dort ausführen (Kaltstart dauert Minuten), `ollama ps` muss den
  Runner zeigen, danach App-Probe + Browser-Check wiederholen.
  Scheitert auch das, erst dann Neuinstallation per offiziellem
  Installer erwägen.

## Warm-Retry 28.10. (Runner geladen, Generate verhungert)

- Eigentümer-Warmstart per CLI (10,4 s, „OK") lud qwen3-vl:2b
  (expires 16:47:38, llamacpp, CPU, kein VRAM).
- App-Retest im Warm-Fenster: 503 „Remote end closed connection
  without response" nach 120,2 s, ok:false — expires_at blieb
  unverändert: Anfrage erreichte den Runner nie.
- Fenster danach geschlossen (16:48:02 /api/ps leer).
- Befund: /api/generate verhungert server-intern (keine Log-Zeile,
  Runner ungenutzt), während /api/chat (CLI) antwortet. Kein
  App-Fehler (Format per Tests verifiziert, Fehlerpfad ehrlich).
- Nächster Schritt (Eigentümer): Ollama-Dienst über die offizielle
  App neu starten (Scheduler-Wedge lösen), sofort Modell vorwärmen,
  dann App-Probe; prüfen, ob /api/generate direkt nach Neustart
  antwortet.

## Protokoll-Fix 28.10. (/api/generate → /api/chat)

- Begründung: installierte CLI antwortet über Chat (10,4 s „OK"),
  während /api/generate hängt und der Server nach ~120 s ohne
  Antwort schließt (Runner teils geladen, aber ungenutzt).
- Umgestellt: lokale Vision- (Inventar) und Text-/Bild-Tag-Pfade
  (Smart Folder) auf /api/chat; Meta-Cloud-Pfad unverändert.
  Kein Cloud-Fallback, keine Nutzerdaten, lokale Loopback nur.
- Verifiziert per Mock-Tests (kein Live-Erfolg behauptet):
  Vision+Tags 56/56, Discover 187 OK + 1 Skip, Gates grün.
- Nächster Schritt: Eigentümer wärmt (`ollama run qwen3-vl:2b`
  per explizitem Pfad), dann synthetische App-Live-Probe im
  nächsten Muse-Turn.

## Chat-Live-Probe 28.10. (Fehlschlag, kein Erfolg)

- Runner per CLI-Warmstart geladen (10,2 s „OK").
- App-Probe /api/chat-Pfad (synthetisch, 150-s-Budget): 503
  „Remote end closed connection without response" nach 120,2 s,
  ok:false — identische Signatur wie /api/generate zuvor.
- Runner danach weiter geladen (expires 17:14:48, llamacpp, CPU).
- Hypothese: kein Endpunkt-Unterschied, sondern
  Streaming-Unterschied — CLI streamt (Antwort in 10,2 s),
  App puffert (stream:false) und stirbt am ~120-s-Abbruch.
  Offen; Probe-Loop bewusst gestoppt.
- Nächster Schritt: gezielt genau EINE Variante prüfen
  (stream:true-Verarbeitung oder Minimal-Chat ohne format:json),
  dann entscheiden; kein Blind-Retry.

## Streaming-Diagnose 28.10. (Fehlschlag, Host-Blocker)

- Direkt-Probe /api/chat stream:true (think:false, format:json,
  num_predict 64, synthetisch): Server schloss ohne Antwort nach
  120,1 s, 0 Chunks, kein Content — identisch zu stream:false.
  Streaming-Hypothese damit widerlegt.
- server.log enthält KEINE /api/chat-Zeile (auch nicht für die
  erfolgreichen CLI-Läufe); HTTP-Inferenz hinterlässt
  serverseitig keine Spur, Meta-Endpunkte antworten sofort.
- Stand: CLI (`ollama run`, 10,2–10,4 s OK) vs. alle
  HTTP-Inferenzaufrufe (Hang + Abbruch ~120 s) — Mismatch
  ungeklärt. Keine Codeänderung (Streaming brächte nichts),
  keine Retries, keine Nutzerdaten.
- Nächste Schritte (Eigentümer): 1) OLLAMA_HOST prüfen + ob zwei
  Ollama-Instanzen laufen (Task-Manager/Dienste); 2) Dienst über
  offizielle App neu starten; 3) sofort erneut CLI, dann EINEN
  HTTP-Generate/Chat; 4) bei Fortbestand: offizielles
  Ollama-Forum/Issue mit Version 0.40.12 + qwen3-vl:2b.

## Warm-Streaming-Probe 28.10. (Fehlschlag, Host-Blocker)

- Runner geladen (CLI 13,0 s „OK", expires 17:25:27, ps-vorher OK).
- Direkt-Probe /api/chat stream:true (think:false, format:json,
  64 Tokens, synthetisch): Serverabbruch ohne Antwort nach
  120,0 s, 0 Chunks, kein Content.
- ps-nachher: expires_at UNVERÄNDERT — Anfrage erreichte den
  Runner trotz geladenem Modell nie.
- Befund: Host-Protokoll-/Laufzeitproblem — CLI-Pfad funktioniert,
  JEDER HTTP-Inferenzaufruf (generate/chat, stream an/aus,
  kalt/warm) verhungert identisch. Keine Codeänderung, keine
  Retries, keine Nutzerdaten.
- Nächste Schritte (Eigentümer): Dienst-Neustart, OLLAMA_HOST +
  Doppelinstanz prüfen, dann je EIN CLI- + HTTP-Versuch; bei
  Fortbestand offizielles Ollama-Issue (0.40.12 + qwen3-vl:2b).

## Proxy-Diskriminator 28.10. (Sandbox-Artefakt — Host-Blocker revidiert)

- Umgebung: Muse-Shell HAT Proxy-Variablen (nur Namen
  geprüft, keine Werte: HTTP_PROXY, HTTPS_PROXY, ALL_PROXY;
  Eigentümer-Shell: keine). getproxies-Schemata: all, ftp,
  http, https (+node_use_env); proxy_bypass('127.0.0.1') =
  False und proxy_bypass('localhost') = False → Loopback
  lief NICHT direkt, sondern durch den Sandbox-Proxy.
- Genau EIN No-Proxy-Request (expliziter
  ProxyHandler({})-Opener, POST /api/chat, stream:true,
  think:false, format:json, num_predict 64, synthetisch):
  SOFORT URLError WinError 10013 nach 0,0 s, 0 Chunks —
  direkte Sockets sind aus der Sandbox blockiert (ps-vorher
  und ps-nachher scheiterten identisch sofort).
- Deutung: ALLE bisherigen ~120-s-Hangs mit „Remote end
  closed connection without response" liefen durch den
  Sandbox-Egress-Proxy (kleine Meta-GETs ok, lange
  Inferenz-Streams sterben am Proxy-Timeout; Runner nie
  berührt, Log-Zeilen fehlen). CLI in der Eigentümer-Shell
  (kein Proxy, direkt) antwortet in 10–13 s. Die
  „Host-Blocker"-Diagnose ist damit als Sandbox-Messartefakt
  REVIDIERT — kein Ollama-/Host-Defekt nachgewiesen.
- Konsequenz: KEIN App-Fix (Bedingung „No-Proxy erfolgreich"
  nicht erfüllt); der App-Code (urllib-Default, Loopback)
  ist in proxy-freier Umgebung korrekt. Keine Retries,
  keine Nutzerdaten, kein Cloud-Fallback.
- Nächster Schritt (Eigentümer, EIGENE Shell ohne Proxy):
  Demo-App dort starten und EINE synthetische App-Probe
  (z. B. /doc-tag-suggest) ausführen — aus der Muse-Sandbox
  ist Live-Inferenz grundsätzlich unerreichbar (direkt
  blockiert, via Proxy hängt). Danach Browser-Check.

## Streaming-Protokoll 28.10. (Tests verifiziert, App-Probe offen)

- Eigentümer-Beleg (warme direkte HTTP-Tests, qwen3-vl:2b):
  /api/chat stream:true MIT think:false hängt; OHNE think-Feld
  antwortet explizites No-Proxy-urllib mit HTTP 200 + done:true
  in 4,6 s — aber num_predict 64/768 lässt message.content
  leer, weil alle Tokens in message.thinking fließen.
- Umsetzung (nur lokaler Pfad, Meta unverändert): stream:true,
  think nirgendwo, num_predict 2048, inkrementeller
  NDJSON-Parser (nur content, thinking ignoriert,
  done:true + Inhalt Pflicht, Byte-/Inhalts-Caps), deutsche
  Timeout-/Leer-/Unvollständig-Fehler, No-Proxy-Opener als
  ein Seam (`vision.local_urlopen`) für Vision + Doc-Tags.
- Verifiziert: Vision 42/42 OK, Doc-Tags 20/20 OK
  (Eigentümer-Läufe nach Seam-Fix); Discover 193 OK + 1 Skip
  (272,6 s, Muse-Lauf); compileall/Policy/File-/Function-Size
  grün; git diff --check sauber (Eigentümer). Kein Live-Aufruf
  in dieser Runde — der finale synthetische App-Erfolg durch
  die Route ist weiter UNVERIFIZIERT und folgt nach
  Eigentümer-Warmstart als genau eine Probe.

## Model-Guard 28.10. (Thinking-503 beobachtet, Fix verifiziert)

- Beobachtet (Eigentümer, synthetischer App-POST
  /contextflow/doc-tag-suggest): 503 nach 177,64 s — der
  256-KiB-Stream-Cap griff VOR message.content, Thinking-Tokens
  fraßen den Stream. Installiert war nur qwen3-vl:2b; dessen
  /api/show meldet TEMPLATE {{ .Prompt }} und
  RENDERER/PARSER qwen3-vl-thinking. Offizielle Tags kennen
  qwen3-vl:2b-instruct (ca. 1,9 GB, isThinking=false).
- Fix (nur lokaler Pfad, Cloud unverändert): `resolve_local_model`
  prüft vor jeder Inferenz /api/show (Thinking per Capability
  exakt + Renderer/Template-Fallback, ≤10 s). Thinking erkannt →
  installierter `-instruct`-Sibling aus /api/tags wird verwendet
  (kein Download, je ≤10 s); fehlt er, schnelle deutsche 503 mit
  `ollama pull`- + CONTEXTFLOW_OLLAMA_MODEL-Anleitung — das
  Thinking-Modell läuft nie. Show-404 → Installations-Hinweis;
  Probefehler → Fail-open (Inferenz bleibt selbst begrenzt).
  Limits geprüft und behalten (2048/256 KiB/32K als sichere
  Obergrenzen, Timeouts unverändert); Antworten melden das
  effektive Modell im Feld `model`. Proben senden nur den
  Modellnamen über direkten Loopback (kein /api/pull je).
- Verifiziert (Muse-Lauf, Sandbox-Kopie): Guard 8/8, Vision
  42/42, Doc-Tags 24/24 (fokussiert 74 OK, 61,7 s); Discover
  205 OK + 1 Skip (288,9 s); compileall/Policy/File-/Function-
  Size grün. Kein Live-Ollama aus der Sandbox möglich
  (direkter No-Proxy-Loopback blockiert, Proxy-Route hängt) —
  kein Live-Erfolg behauptet. Offen: `ollama pull
  qwen3-vl:2b-instruct` + Modell setzen (Eigentümer), danach
  genau eine synthetische App-Probe.

## Bild-Integration 28.10. (Text live OK, Bild-Fix per Tests)

- Live-Erfolg Text (Eigentümer-Shell): qwen3-vl:2b-instruct
  installiert (/api/show: qwen3-vl-instruct), synthetischer
  JSON-Chat in 9,38 s; echter Flask-POST
  /contextflow/doc-tag-suggest → HTTP 200 in 23,66 s,
  model=qwen3-vl:2b-instruct, „Lokale KI", mit Tag-Vorschlägen.
  Erster App-Level-Live-Erfolg der Text-Route.
- Live-Befund Bild (Eigentümer): /contextflow/doc-image-suggest
  mit synthetischem PNG als Smart-Folder-Datei → HTTP 400 „Die
  Bilddatei ist derzeit nicht verfügbar." in 0,47 s, VOR
  Inferenz. Ursache: `document_image_bytes` las nur aus V2-
  Storage, Smart-Folder-Importe liegen aber als Dateien unter
  DOCUMENT_ROOT (V2-stat meldete not-ok).
- Fix (nur doc_tags, Cloud unverändert): V2 bleibt
  vorrangig/autoritativ; fehlt das V2-Objekt, liest der
  Fallback die echte Datei über `resolve_file_under` unter
  DOCUMENT_ROOT (Traversal/Symlink-Escape/Nicht-Datei
  abgewiesen, >5 MiB vor dem Puffern verweigert, Bytes per
  PIL validiert). Nichts wird gespeichert.
- Verifiziert (Muse-Lauf, Sandbox-Kopie): Doc-Tags 31/31
  (7 neu: echte Datei bei fehlendem V2 + Unverändert-Nachweis,
  ungepatchter Storage, Oversize-/Gelöscht-Route, Traversal-,
  Verzeichnis-, Symlink-Schutz); fokussiert 81 OK + 1 Skip
  (73,7 s); Discover 212 OK + 2 Skips (328,9 s);
  compileall/Policy/File-/Function-Size grün. Kein Live-Ollama
  aus der Sandbox möglich — Bild-App-Erfolg weiter offen:
  Eigentümer führt die synthetische Bild-Route live aus.

## Prompt-Qualität 28.10. (Bild live OK, Vorschläge geschärft)

- Live-Erfolg Bild (Eigentümer): echter Flask-POST
  /contextflow/doc-image-suggest mit synthetischem
  handgezeichnetem Parkbank-PNG über den Datei-Fallback →
  HTTP 200 in 82,38 s (qwen3-vl:2b-instruct, nur synthetisch,
  Metadaten unverändert). Antwort: Beschreibung „Ein
  stilisiertes, flaches Symbol eines einfachen Holzstuhls oder
  einer Bank auf einem neutralen Hintergrund.", Objekte
  [Stuhl, Holz, Sitzfläche, Höhe, Fußstütze, Sitzkante,
  Sitzstütze] — zu viele, darunter Teile/Material/Maße.
  Frühere Bild-400 bleibt als behobener Befund bestehen.
- Schärfung (nur doc_tags, ein Modellaufruf, lokal):
  Bild-Prompt höchstens 4 ganze, eigenständige Objekte (keine
  Teile/Materialien/Maße/Farben/Synonyme, Unsicheres weg);
  Text-Prompt höchstens 5 hochwertige Tags ohne Varianten
  plus `evidence`-Zitat (max. 160 Zeichen). Server-Caps 4/5;
  Evidence wird normalisiert (Groß/Klein-, Leer-, Anführungs-
  blind) gegen Dateiname + Text geprüft — erfundenes Zitat
  verwirft den Eintrag, fehlendes bleibt kompatibel. Antwort
  je Vorschlag plus `evidence`-Feld; UI liest nur
  tag/reason/already und bestätigt Tag-Strings (kompatibel,
  keine JS-Änderung).
- Verifiziert (Muse-Lauf, Sandbox-Kopie): Doc-Tags 39/39
  (8 neu: Prompt-Wortlaut, Evidence-Match/Erfunden/Kanten,
  Caps, 2 Routen); fokussiert 89 OK + 1 Skip (87,7 s);
  Discover 220 OK + 2 Skips (303,0 s);
  compileall/Policy/File-/Function-Size grün; diff-check
  ausstehend (Sandbox). Kein Live-Ollama aus der Sandbox —
  Qualitätswirkung live Folgelauf (Eigentümer).

## Grounding-Pflicht 28.10. (Evidence zu schwach, Fix per Tests)

- Live-Retest (Eigentümer): Text-Route 200 in 22,36 s mit
  Tags [echnung_2024, inkommen_2024, usgaben_2024,
  kostenrechnung, inkommensausgabe] bei passendem Zitat
  „Rechnung über 120 Euro aus dem Jahr 2024." — Einkommen-,
  Ausgaben-, Kosten-Begriffe stehen NICHT in der Quelle;
  Bild-Route 200 in 64,64 s mit Objekten [Stuhl, Sitzfläche,
  Stützen, Gestaltung] trotz Teile-Verbots. Evidence allein
  trägt keine Tags, das Max-4-Label wird ignoriert.
- Härtung (nur doc_tags, ein Modellaufruf, lokal): Text verlangt
  jetzt Zitat UND wörtlich vorkommende Tag-Phrase (Groß/Klein-,
  Trenn-, Satzzeichen-blind, zusammenhängend); breites Zitat
  rettet keinen unbelegten Tag, fehlendes Zitat verwirft;
  weniger/keine Tags akzeptiert. Bild liefert höchstens EIN
  Hauptobjekt-Label (erstes = primär) oder keines, Satz bleibt;
  kein Confidence-Feld (Kategoriefehler, kein Kalibrierungs-
  problem; Schema/UI unverändert kompatibel).
- Verifiziert (Muse-Lauf, Sandbox-Kopie): Doc-Tags 41/41
  (2 neu: Phrasen-Grounding inkl. Trennzeichen/nicht
  zusammenhängend, gemischte Route); fokussiert 91 OK + 1 Skip
  (75,7 s); Discover 222 OK + 2 Skips (281,1 s);
  compileall/Policy/File-/Function-Size grün; diff-check
  ausstehend (Sandbox). Qualität NICHT abgeschlossen —
  Eigentümer wiederholt beide Routen-Proben live; deren
  Ergebnis wird danach protokolliert.

## Qualitätsfilter 28.10. (Live vor Filtern, Fix per Tests)

- Live-Proben VOR den neuesten Filtern (Eigentümer,
  qwen3-vl:2b-instruct, nur synthetisch): Text-Lauf A →
  HTTP 400 in 26,56 s (keine gültigen grounded Tags);
  Text-Lauf B → HTTP 200 in 18,82 s, Rohmodell lieferte
  [Rechnung, 2024, Euro, aus, notizen] — Grounding
  verwarf Unbelegtes, aber Füllwort „aus" und generischer
  Dateiname-Stamm „notizen" kamen durch; Bild → HTTP 200
  in 63,1 s, Beschreibung uneindeutig (stilisierter
  Holzstuhl oder -tisch), Objekte leer, Metadaten
  unverändert.
- Fix (nur doc_tags + eine JS-Statuszeile, ein
  Modellaufruf, lokal): gültiges JSON ohne überlebende
  Vorschläge antwortet 200 + ok + leere Liste (statt
  400), damit die UI „keine belegbaren Vorschläge" sagen
  kann; Stoppwort-Filter (Funktionswörter, min. 2
  Zeichen) und Dateinamen-Stamm-Filter (nur ohne
  Text-Beleg — echter Text-Beleg behält selbst generische
  Wörter); Bild-Labels für Teile/Materialien/Attribute/
  Hintergrund fallen weg, sodass MAX_OBJECTS 1 nur ein
  ganzes Objekt liefert (Alltags-Objektnomen wie Glas,
  Papier oder Tür bleiben erhalten). Fehlgeformtes JSON
  bleibt 400. Nichts wird gespeichert.
- Verifiziert (Muse-Lauf, Sandbox-Kopie, nur fokussiertes
  Modul): Doc-Tags 46 Tests OK (5 neu: Stoppwörter,
  Stamm-Filter inkl. Text-belegt, Teile-voran/ganzes
  Objekt/leer-unsicher, schwache Route, Teile-Route;
  200-leer-Route umgestellt; 1 vorbestehender
  Symlink-Skip; 94,1 s). Whitespace-Eigenprüfung der 3
  geänderten Dateien sauber; git diff --check blockiert
  (Sandbox, .git unsichtbar — Eigentümer-Bestätigung
  offen). Qualität NICHT abgeschlossen — eine frische
  Live-Probe je Route steht aus.

## Live-Abnahme Tag/Bild 28.10. (Filter live-verifiziert)

- Finale Proben NACH den Filtern (Eigentümer, lokal,
  nur synthetisch): Text-Route → HTTP 200 in 29,67 s
  mit nur [Rechnung, 2024, Euro] — Füllwort „aus" und
  Dateiname-Stamm „notizen" fehlen wie beabsichtigt;
  Bild-Route (synthetische Bank-Illustration) → HTTP 200
  in 62,76 s, Bank beschrieben, Objekte leer, Metadaten
  unverändert; warmer Retry derselben Bild-Route →
  HTTP 200 in 5,31 s, Roh-JSON schlug Objekte [Bank]
  vor, `validate_image_description` behielt exakt
  [Bank]. Ende-zu-Ende-Ganzobjekt-Vorschlag lokal
  bestätigt; Ausgabe ist stochastisch und bleibt bei
  Unsicherheit leer.
- `git diff --check` eigentümerseitig Exit 0.
- Fazit: enge Tag/Bild-Route live-verifiziert.
  Uneindeutige Bilder können weiter kein Objekt liefern;
  breitere Integration/Browser-Abnahme bleibt offen.

## Bild-Label-Abgleich 28.10. (Mismatch live, Fix per Tests)

- Live-Mismatch (Eigentümer, warme Bild-Route): Satz
  „Ein stilisiertes, einfaches Holzbrett mit vier
  Beinen." bei Objekten [Bank] — der Tag ist vom
  eigenen Satz nicht gedeckt (früherer Lauf: Bank
  beschrieben, Objekte leer). Unbestätigter Tag.
- Fix (nur doc_tags, kein Modellaufruf): behaltenes
  Label muss zusätzlich wörtlich (Groß/Klein-,
  Trennzeichen-blind) im Satz vorkommen, sonst fällt
  es — nichts wird aus dem Satz erschlossen, leere
  unsichere Ausgabe bleibt gültig. Prompt verlangt
  Wiederholung des klar benannten Hauptobjekts.
- Verifiziert (Muse-Lauf, Sandbox-Kopie, nur fokussiertes
  Modul): Doc-Tags 47 Tests OK (1 neu: Match behalten,
  Live-Mismatch verworfen, Teile-voran, leer gültig;
  1 vorbestehender Symlink-Skip; 92,2 s).
  Whitespace-Eigenprüfung sauber; git diff --check
  blockiert (Eigentümer-Bestätigung für den neuen
  Stand offen). Bild-Qualität offen bis finale
  Live-Routen-Probe.

## Konsistenz-Abnahme 28.10. (Label-Satz-Abgleich live OK)

- Final NACH dem Abgleich (Eigentümer, lokal, nur
  synthetisch): Text-Route → HTTP 200 in 34,39 s mit
  nur [Rechnung, 2024], Dokument-Tags unverändert;
  kompakte stilisierte Bank → HTTP 200 in 55,38 s,
  Satz nennt Stuhl, Objekte [Stuhl] passend zum Satz,
  Metadaten unverändert; freie Parkbank → HTTP 200 in
  60,06 s, Satz nennt Bank, Objekte exakt [Bank],
  Metadaten unverändert. Früherer Mismatch
  (Holzbrett-Satz/[Bank]) wird per deterministischem
  Test abgewiesen.
- Fazit: enge lokale Route für Text und klares
  Bank-Bild verifiziert; Mehrdeutigkeit kann ein
  anderes Ganzobjekt-Label oder keines liefern.
  Browser-Abnahme offen (UI-Automatisierung stoppte
  vor Interaktion, Server-Neustart per Policy
  abgelehnt) — nicht als browser-verifiziert melden.
