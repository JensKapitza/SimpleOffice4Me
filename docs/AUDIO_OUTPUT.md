# Audio-Ausgabe und Durchsagen

## Zweck

Lokale Lautsprecher suchen, gruppieren und Signaltöne oder gesprochene Texte
abspielen; Durchsagen an explizit gebundene SimpleOffice-RTP-Empfänger senden. Der bestehende Webprozess besitzt den Wiedergabe-Worker. Es entsteht
kein zusätzlicher Netzwerkdienst. Live-Audio bleibt im Audio-Streamer.

## Voraussetzungen

Für automatisch erkannte PulseAudio-/PipeWire-Ausgänge werden die vorhandenen
Programme `pactl` und `paplay` benötigt. Manuell definierte lokale Ausgänge nutzen
die vorhandenen Alternativen `pw-play`, `aplay` oder `ffplay`. Piper plus das
lokale Modell aus den Medien-Einstellungen (`media.piper_model`, Admin →
Dokumente & Medien) werden nur für Sprachausgabe benötigt. Die deutsche
Standardstimme ist Thorsten (medium); englische Ansagen nutzen optional
`media.piper_model_en` (empfohlen: LibriTTS-R, medium) und fallen sonst auf
die deutsche Standardstimme zurück. Es gibt bewusst keinen Browser- oder
Netzwerk-Stimmen-Fallback.
Signaltöne entstehen mit Python-Standardbibliothek als WAV. Nichts wird durch
den Audio-Worker installiert oder heruntergeladen.

Beim Start zur Synthese setzt SimpleOffice nur für den Piper-Unterprozess
`ORT_DISABLE_TELEMETRY=1`. Das deaktiviert laut der
[ONNX-Runtime-Dokumentation](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md#disabling-telemetry)
den 1DS-Telemetrieanbieter, sofern die verwendete Laufzeit den Schalter
unterstützt. Der ältere Windows-TraceLogging-Anbieter verwendet ihn nicht.
Der Schalter ist keine Netzwerksperre für beliebige Piper-Builds oder andere
Prozesse; die Umgebung von SimpleOffice bleibt unverändert.

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

### Entfernte Durchsagen

Auf dem Ziel den vorhandenen Audio-Streamer als Empfänger starten. Unter
**Audio → Entfernten RTP-Empfänger verbinden** suchen, Treffer auswählen und
speichern. Alternativ private IPv4-Adresse und RTP-Port manuell eintragen.
Die vorhandene Federation-LAN-Suche zeigt aktive Desktop-Empfänger, ohne diese
automatisch zu aktivieren oder Audio zu senden. Die Auswahl wird in derselben
SQLite-Datenbank gespeichert. Der Ausgang ist anschließend für Signaltöne,
Sprachausgabe und Gruppen auswählbar. Erneutes Speichern derselben Adresse und
desselben Ports aktualisiert den Eintrag.

Die Ausgangs-API akzeptiert zusätzlich beispielsweise:

```json
{"node_id":"rtp-192.168.1.20","output_id":"rtp-192.168.1.20-5004",
 "name":"Wohnzimmer","volume":50,
 "transport":{"kind":"rtp-udp","host":"192.168.1.20","port":5004}}
```

`transport` bleibt für lokale oder alte ungebundene Einträge leer. Ein Transport
ist nur für externe Knoten zulässig. Erlaubt sind RFC1918-IPv4-Adressen und
Loopback, keine Hostnamen, URLs, öffentlichen Adressen, IPv6 oder Multicast.
Der vorhandene Desktop-Receiver unterstützt derzeit IPv4. RTP-Port 1024–65534,
RTCP auf dem Folgeport. Bestehendes FFmpeg mit libopus kodiert die gerenderte WAV
in Echtzeit (48 kHz, Stereo, 64 kbit/s, Payload 111). Keine zusätzliche Bibliothek.
Stop beendet lokale Player und RTP-Sender; bereits empfangene Puffer lassen
sich dadurch nicht am entfernten Gerät zurückrufen.

Ein erfolgreicher Auftrag enthält `delivery="sent-unconfirmed"`, sobald ein
Remote-Ausgang beteiligt war. Das bedeutet nur: Der lokale Sender hat ohne
Prozessfehler beendet. RTP liefert keine Wiedergabebestätigung. Der registrierte
Online-Schalter ist keine Hardware- oder Empfangsprüfung. Keine zwei Sender
auf denselben Empfänger richten; Live-Übertragung vorher beenden. Teilweise
übertragene Aufträge werden nach Fehlern nicht automatisch erneut versendet.

## Discovery

Der Scan aktualisiert lokale PulseAudio-/PipeWire-Ausgänge spätestens alle 30
Sekunden. Nicht mehr gefundene Geräte werden offline. Remote-Definitionen werden
nicht überschrieben. Scanstatus enthält Zeitpunkt, Trefferzahl und Fehler.
Ein fehlgeschlagener Scan behauptet keine erfolgreiche Hardwareerkennung.

## Ports

Kein neuer Listener am Sender. Entfernte Ausgänge verwenden UDP am konfigurierten
RTP-Port und Folgeport für RTCP des bestehenden Empfängers. Adminsteuerung verwendet den vorhandenen HTTP-Port. Lokale
Audioprogramme sprechen den vorhandenen Audio-Server der Benutzersitzung an.

## Security

RTP ist unverschlüsselt und ohne Peer-Authentifizierung. Nur in einem
vertrauenswürdigen privaten Netz verwenden; keine Internetfreigabe. Eine private
IP-Adresse allein schafft kein Vertrauen. Die ausdrückliche Zielbindung durch
den Administrator ist erforderlich; die Suche sendet keine Durchsagen.

Adminrolle und vorhandener CSRF-Schutz gelten für alle Bedienaktionen. Externe
Programme erhalten Argumentlisten ohne Shell. Text wird Piper über stdin
übergeben. Neue Datenbanken und temporäre Sprachdateien werden privat angelegt.
Der Worker protokolliert keine TTS-Texte oder Exception-Inhalte. Rendering ist
auf 45 Sekunden begrenzt und abbrechbar; Wiedergabe auf 300 Sekunden.

## Fehlerdiagnose und Recovery

- **Keine lokalen Ausgänge:** Benutzersitzung und `pactl` prüfen, erneut scannen.
- **Piper/Modell fehlt:** vorhandenes Modell konfigurieren; zunächst einen
  Signalton testen. Der Audio-Ausgabe-Mini-Service lädt keine Modelle nach.
- **Kids-Vorlesen (`GET /kids/lernen/sprache`, Anmeldung erforderlich):** kurze
  Lern- und Spieltexte (1–500 Zeichen, `lang` `de-DE`/`en-US`) rendert die
  lokale Piper-CLI und legt sie per Texthash auf dem Server ab (Cache
  `audio/kids-tts`, wiederverwendete `render_tts`-Engine). Wiederholte Maze-
  und Quizansagen brauchen keine Neuberechnung. Bestehende Sprachrouten nutzen
  den privaten Browser-Cache, Anleitungen mit wechselbarer Kinderstimme nicht. Ohne Modell/CLI antwortet der Endpunkt `503` mit
  Handlungsanweisung statt Ersatzstimme.
- **Optionale deutsche Labyrinth-Stimme:** Unter **Admin → Dokumente & Medien**
  lädt **Deutsche Kinderstimme laden und aktivieren** einmalig Modell und
  Konfiguration vom [gepinnten Herausgeber-Stand][piper-scratch] (ca. 64 MB) und
  prüft beide Dateien anhand von Größe und SHA-256. Ohne Klick findet kein
  Download statt. Wenn der Proxy währenddessen abbricht, die Einstellungsseite
  nach der Wartezeit neu laden; laufende Downloads werden nicht dupliziert.
  Die Dateien liegen privat unter
  `.simpleoffice-meta/kids-voices/<Modellrevision>` des Dokumentenstamms. Bei
  Deaktivierung bleiben sie zur späteren Offline-Reaktivierung dort. Die Stimme
  liest ausschließlich deutsche Labyrinth-Anleitungen, falls kein deutsches
  Admin-Modell hinterlegt ist. Aussprache, Lautübungen und Englisch behalten den
  bisherigen Pfad. Die Modellkarte dokumentiert experimentelle Aussprache.
  Pro Instanz wird gleichzeitig nur eine Ansage berechnet; der separate Cache
  behält höchstens 32 WAV-Dateien und 16 MiB.
- **Lizenzen und Plattformen:** Modellgewichte und Aufnahmen: Thorsten Müller /
  Thorsten-Voice, [CC0 1.0][cc0]. Die Stimme wurde ohne fremden Checkpoint aus
  den eigenen [CC0-Aufnahmen von 2022.10][thorsten-dataset] trainiert. Sie ist
  nicht die Lessac-basierte ältere Piper-Stimme. Piper ([GPL-3.0-or-later][piper-license])
  wird separat installiert, von der App nicht gebündelt oder nachinstalliert.
  Der Server muss `piper` im `PATH` haben. Geprüft auf Linux mit `piper-tts 1.8.0`.
  Windows und macOS erfordern eine funktionierende lokale CLI; Docker erfordert
  Piper im Container und ein persistentes Dokumentenvolume. Diese Plattformen
  und ein alleinstehendes Android-APK wurden nicht getestet. Ein Android-Client
  kann Audio von einem entsprechend eingerichteten Server abspielen. Keine
  Lerntexte gehen an Hugging Face oder einen Online-Synthesedienst.

- **Externer Knoten:** Ohne explizite RTP-Bindung scheitert der Auftrag verständlich.
  Transport speichern und auf dem Ziel den Audio-Streamer-Empfänger starten.
  Bei ausbleibendem Ton Ports, Empfängerstatus und Lautsprecher prüfen; erfolgreicher
  Versand ist kein Empfangsnachweis.
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

[piper-scratch]: https://huggingface.co/Thorsten-Voice/Piper-Scratch/blob/d3a970797980dbc61b50bba8852a1728426d46bc/README.md
[thorsten-dataset]: https://www.thorsten-voice.de/ueber-das-projekt/aufnahmen/
[cc0]: https://creativecommons.org/publicdomain/zero/1.0/
[piper-license]: https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md

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

Nur explizit gebundene RTP-Nodes sind angebundene Empfänger. Ein Ende-zu-Ende-
Empfangsnachweis, verschlüsselter Transport und IPv6-Audio sind nicht implementiert. DLNA bleibt
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

Für ein administratives Linux-Setup kann `SIMPLEOFFICE_PIPER_INSTALL=1 ./start.sh setup` die feste Piper-Version in der Projektumgebung installieren. RapidOCR/ONNX ist separat über `SIMPLEOFFICE_OCR_INSTALL=1 ./start.sh setup` wählbar. Stimmenmodelle werden separat eingerichtet. Unter Termux bleiben beide Wheel-Installationen deaktiviert.
