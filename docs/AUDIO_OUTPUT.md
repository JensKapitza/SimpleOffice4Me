# Audio-Ausgabe und Durchsagen

## Zweck

Lokale Lautsprecher suchen, gruppieren und Signaltöne oder gesprochene Texte
abspielen. Der bestehende Webprozess besitzt den Wiedergabe-Worker. Es entsteht
kein zusätzlicher Netzwerkdienst. Live-Audio bleibt im Audio-Streamer.

## Voraussetzungen

Für automatisch erkannte PulseAudio-/PipeWire-Ausgänge werden die vorhandenen
Programme `pactl` und `paplay` benötigt. Manuell definierte lokale Ausgänge nutzen
die vorhandenen Alternativen `pw-play`, `aplay` oder `ffplay`. Piper plus das
lokale Modell aus `SIMPLEOFFICE_PIPER_MODEL` werden nur für Sprachausgabe benötigt.
Signaltöne entstehen mit Python-Standardbibliothek als WAV. Nichts wird durch
den Audio-Worker installiert oder heruntergeladen.

## Standardbetrieb

`./start.sh` startet den lokalen Wiedergabe-Worker mit. Unter **Mini Services →
Audio** werden Ausgänge automatisch gesucht. Einen Ausgang oder eine Gruppe
wählen, dann „Signalton einreihen“ oder „Text einreihen“. Ein neu gescannter
Ausgang erscheint nach der nächsten Statusaktualisierung in der Auswahl.

Der Dienst startet standardmäßig, nimmt aber kein Mikrofon auf. Ohne Aufträge
wird nichts abgespielt. Der explizite Stop bricht die aktuelle lokale Wiedergabe
ab; noch wartende Aufträge bleiben erhalten und können einzeln abgebrochen werden.
Restart verwendet dieselbe SQLite-Warteschlange. Ein exklusiver OS-Lock verhindert
zwei Verbraucher für dieselbe Instanz. Atomares Claim verhindert doppelte Abholung.

## Konfiguration

`audio/audio-output.sqlite3` neben `mini-services.json` enthält Ausgänge,
Gruppen, Aufträge und Einstellungen. Standardwerte: `enabled=true`,
`autostart=true`, `retry_limit=3` (erlaubt 0–6). Ausgangsname und Lautstärke
bleiben bei erneutem Scan erhalten. Automatisch erkannte Geräte erhalten Node
`local` und eine stabile `discovered-...`-ID.

Die Ausgangs-API akzeptiert `channels` als JSON-Ganzzahl von 1–16,
`volume` als JSON-Ganzzahl von 0–100 und `online` ausschließlich als JSON-Boolean.
Defaults sind 2 Kanäle, 100 Prozent und `true`. `device` ist ein Text mit höchstens
500 Zeichen ohne Steuerzeichen; ein leerer Text wählt den vorhandenen Standardpfad.
Ungültige Werte werden mit HTTP 400 abgewiesen, ohne bestehende Ausgänge zu ändern;
es gibt keine stillschweigende Begrenzung oder Umdeutung von `"false"` zu `true`.
Auftragsprioritäten müssen ebenfalls JSON-Ganzzahlen von 0–100 sein. Ungültige
Aufträge gelangen nicht in die Warteschlange.

Die Lautstärke (0–100 Prozent) gilt auch für manuell angelegte lokale Ausgänge.
Bei diesen Ausgängen wird PCM-WAV vor der Wiedergabe in begrenzten Blöcken mit
Python angepasst. Das Original und die Systemlautstärke bleiben unverändert;
private temporäre Dateien werden nach Ende/Abbruch entfernt. 100 Prozent benötigt
keine Kopie. Innerhalb eines Auftrags teilen Ausgänge mit gleicher Lautstärke
dieselbe Kopie. Die Verarbeitung ist abbrechbar und auf 32 MiB PCM begrenzt.
Automatisch erkannte PulseAudio-Ausgänge verwenden weiterhin die Player-Lautstärke.
Der FFplay-Fallback unterstützt nur den Systemstandard; eine konkrete abweichende
Geräteauswahl scheitert ausdrücklich, statt auf dem falschen Lautsprecher zu spielen.

## Discovery

Der Scan aktualisiert lokale PulseAudio-/PipeWire-Ausgänge spätestens alle 30
Sekunden. Nicht mehr gefundene Geräte werden offline. Remote-Definitionen werden
nicht überschrieben. Scanstatus enthält Zeitpunkt, Trefferzahl und Fehler.
Ein fehlgeschlagener Scan behauptet keine erfolgreiche Hardwareerkennung.

## Ports

Keine neuen Ports. Adminsteuerung verwendet den vorhandenen HTTP-Port. Lokale
Audioprogramme sprechen den vorhandenen Audio-Server der Benutzersitzung an.

## Security

Adminrolle und vorhandener CSRF-Schutz gelten für alle Bedienaktionen. Externe
Programme erhalten Argumentlisten ohne Shell. Text wird Piper über stdin
übergeben. Neue Datenbanken und temporäre Sprachdateien werden privat angelegt.
Der Worker protokolliert keine TTS-Texte oder Exception-Inhalte. Rendering ist
auf 45 Sekunden begrenzt und abbrechbar; Wiedergabe auf 300 Sekunden.

## Fehlerdiagnose und Recovery

- **Keine lokalen Ausgänge:** Benutzersitzung und `pactl` prüfen, erneut scannen.
- **Piper/Modell fehlt:** vorhandenes Modell konfigurieren; zunächst einen
  Signalton testen. Kein automatischer Modell-Download.
- **Externer Knoten:** Für manuell registrierte Remote-Nodes existiert hier kein
  Transport. Der Auftrag scheitert verständlich; er bleibt nicht endlos hängen.
  Für unterstützte Live-RTP-Verbindungen den Audio-Streamer verwenden.
- **Audio-Prozess abgebrochen:** Der Auftragsverlauf enthält `failed` und eine
  Handlungsbeschreibung. Vor erneutem Abspielen prüfen, ob ein Teil hörbar war.

Temporäre Fehler **vor** Wiedergabebeginn werden begrenzt mit Backoff wiederholt.
Ein möglicherweise teilweise abgespielter Auftrag wird nicht automatisch erneut
ausgegeben. Nach Prozessabsturz werden unterbrochene Aufträge als fehlgeschlagen
markiert. Maximal 1000 offene Aufträge, 1000 Einträge abgeschlossener Historie
und 16 lokale Ausgänge pro Auftrag. Gruppenzyklen und mehrdeutige IDs scheitern
vor dem Start von Playern. Kalender-Polling dedupliziert dieselbe Termininstanz.
Der Cache erzeugter Sprachdateien ist auf 64 Dateien bzw. 128 MiB begrenzt;
andere Assets werden beim Aufräumen nicht gelöscht.

## API

Adminpräfix `/admin/mini-services/audio`:

| Methode/Pfad | Funktion |
|---|---|
| GET leer | Ausgänge, Gruppen, Verlauf, Workerstatus |
| POST /scan | lokale Ausgänge suchen und speichern |
| POST /outputs | Ausgang registrieren/ändern |
| POST /groups | Gruppe speichern |
| POST /say | Text und Targets einreihen |
| POST /sound | Preset und Targets einreihen |
| POST /start, /stop, /restart | Worker-Lifecycle |
| GET/POST /settings | Aktiviert, Autostart, Retry-Limit |
| POST /queue/<id>/cancel | wartenden Auftrag abbrechen |

Die gemeinsame `/api/mini-services`-Übersicht zeigt auch `audio-output`,
`audio-sender` und `audio-receiver`. Aktionen werden an die bestehenden
Audio-Besitzer delegiert; es gibt keinen zweiten Audio-Service-Manager.

## Plattformen

Linux mit Benutzeraudiositzung ist der implementierte lokale Wiedergabepfad.
Auf Windows hängen manuelle Ausgänge von den vorhandenen Playern ab; die
PulseAudio-Suche ist dort ohne entsprechenden Server nicht verfügbar. Android
Live-Audio verwendet seine native Bridge; dieser lokale Ansage-Worker ist kein
Ersatz dafür. Systemd-Dienste ohne Audio-Sitzung melden fehlende Geräte.

## Einschränkungen und Tests

Ein registrierter Remote-Node ist noch kein angebundener Empfänger. DLNA bleibt
separat in #285. Diese Durchsagen sind kein zertifiziertes Alarmsystem.
Sprachmodelle und Lautsprecherqualität wurden nicht mit echter Hardware geprüft.

`tests/test_audio_announcements.py` prüft konkurrierende Abholung, Abschluss,
Abbruch, fehlende Hardware, begrenzte Wiederholung, Gruppenfehler, Remote-Fehler
und die Deduplizierung von Kalenderaufträgen ohne Spezialhardware.
## Speicherfehler und Wiederanlauf

Eine nicht lesbare Audio-Konfiguration verhindert den Webstart nicht. Der
betroffene Dienst meldet den Speicherfehler; die gemeinsame Übersicht zeigt
Netzwerk- und Bootdienste weiter an. Bei unbekannten Autostart-Einstellungen
wird keine Wiedergabe auf Verdacht gestartet. Nach Reparatur explizit starten.

Fehler beim Öffnen oder Verarbeiten der Durchsagen-Datenbank während eines
angeforderten Worker-Laufs verwenden begrenzten Backoff: standardmäßig drei
Wiederholungen nach 2/4/8 Sekunden, konfigurierbar bis sechs. Stop unterbricht
das Warten. Nach 60 Sekunden stabilem Betrieb beginnt ein neues Fehlerbudget.
Möglicherweise schon teilweise abgespielte Durchsagen werden nicht automatisch
erneut abgespielt. Storage-APIs liefern verständliche 503-Antworten ohne rohe
Exceptiontexte. Offline-/Namensänderungen werden auch in der Zielauswahl sichtbar.

Bei Fehlern während der Prozessbereinigung wird jeder Player unabhängig beendet,
mit begrenztem Wait und Kill-Fallback. Bleibt ein Prozess unbestätigt aktiv, steht
der Dienst auf „stopping“ und startet keine neuen Wiedergaben. Nach Behebung
der Betriebssystem-/Berechtigungsursache erneut Stop ausführen. Temporäre
Lautstärkedateien und der aktive Auftragsverweis werden trotzdem bereinigt;
Dateifehler bleiben in der Diagnose sichtbar.
