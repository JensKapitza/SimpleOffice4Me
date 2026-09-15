# Live-Audio: Sender und Receiver

## Zweck

Ein Mikrofon per Opus/RTP an einen oder mehrere Empfänger übertragen.
Der Receiver verteilt das empfangene Audio an lokale Lautsprecher und optional
an ein virtuelles Mikrofon. Beide Dienste werden vom bestehenden Webprozess
gesteuert; der Netzwerkworker ist für diese Audiofunktionen nicht erforderlich.
Lokale Signaltöne und TTS-Aufträge gehören zur [Audio-Ausgabe](AUDIO_OUTPUT.md).

## Architektur

Der Desktop-Sender verwendet FFmpeg für Capture und Opus-Codierung. RTP/UDP
transportiert das Audio zum FFmpeg-Decoder des Receivers. Dessen PCM-Verteiler
versorgt PulseAudio-/PipeWire-Lautsprecher und optional einen Null-Sink, dessen
Monitor andere Programme als Mikrofon auswählen können. Live-Audio wird nicht
als WAV oder andere Audiodatei aufgezeichnet. Android verwendet seinen vorhandenen
nativen Audio-Bridge-Pfad.

## Voraussetzungen

| Dienst | Vorhandene Komponenten |
|---|---|
| Desktop-Sender | FFmpeg mit libopus und PulseAudio- oder ALSA-Eingang; Mikrofon |
| Desktop-Receiver | FFmpeg, PulseAudio oder PipeWire-Pulse, paplay |
| Virtuelles Mikrofon | zusätzlich pactl und eine erreichbare Audio-Benutzersitzung |
| Automatische Pulse-Gerätesuche | pactl |
| ALSA-Mikrofonsuche | lesbares /proc/asound/pcm und Karten-IDs unter /proc/asound |
| Android | vorhandene native Bridge und Mikrofonberechtigung für den Sender |

Optionale Komponenten werden weder installiert noch heruntergeladen.
Fehlt eine Voraussetzung, zeigt die Oberfläche einen Fehler mit Diagnose.
Der Webprozess benötigt keine Root-Rechte.

## Standardbetrieb

1. SimpleOffice mit `./start.sh` starten und als Administrator anmelden.
2. **Mini Services → Audio → Live Audio-Streamer** öffnen.
3. Auf dem Empfangsgerät Ausgänge scannen, Lautsprecher und/oder virtuelles
   Mikrofon wählen und den Receiver starten.
4. Auf dem Sendegerät Eingänge scannen, Mikrofon wählen und mindestens ein
   RTP-Ziel als `HOST:PORT` eintragen. Dann den Sender starten.
5. Im Receiver den tatsächlichen PCM-Empfang prüfen. Ohne Daten meldet er
   „wartet“. Zum Beenden die Stop-Aktion des jeweiligen Dienstes verwenden.

Die Seite liegt unter `/admin/mini-services/audio/streamer`.
Der Monitor des Standard-Null-Sinks heißt `simpleoffice_stream.monitor`;
diesen wählen andere Programme als Eingabegerät.

Beispiel für zwei Empfänger im eigenen Netz: `empfaenger-a:5004` und
`empfaenger-b:5004`. Hostnamen müssen im lokalen Netz auflösbar sein.
Für lokales Mithören kann ein zusätzlicher Receiver auf einem anderen Port
laufen; als weiteres Senderziel dient dann `127.0.0.1:PORT`.

## Konfiguration

Gespeichert wird in `audio/audio-output.sqlite3` neben der Mini-Service-
Konfiguration. Start speichert die Auswahl. Stop ändert die Autostart-Präferenz
nicht; Neustart verwendet gespeicherte Einstellungen. Identische wiederholte
Starts erzeugen keinen zweiten Prozess. Eine Einstellungsänderung gilt beim
nächsten Start, Deaktivieren stoppt sofort. Zurücksetzen stoppt die Session und
stellt die folgenden Standardwerte wieder her.

| Dienst | Option | Standard / Bedeutung |
|---|---|---|
| Beide | enabled | true; deaktiviert verhindert Start |
| Beide | autostart | false; Mikrofonfreigabe nur bewusst aktivieren |
| Beide | retry_limit | 3; ganze Zahl 0–6 |
| Sender | source | default; erkannte oder manuell eingegebene Quelle, maximal 240 Zeichen |
| Sender | backend | pulse; alternativ alsa |
| Sender | destinations | leere Liste; für Start mindestens ein Ziel, höchstens 16 |
| Sender | bitrate_kbps | 64; ganze Zahl 16–256 kbit/s |
| Receiver | port | 5004; RTP-Port 1024–65534, Folgeport für RTCP beachten |
| Receiver | bind | leer: automatische konkrete private LAN-IPv4, sonst Loopback; manuell konkrete lokale IPv4 |
| Receiver | speaker_devices | leere Liste; gewünschte lokale Lautsprecher auswählen |
| Receiver | virtual_microphone | true |
| Receiver | virtual_sink | simpleoffice_stream; 1–80 Buchstaben/Ziffern oder _ . - |

Autostart des Senders ohne Ziele wird abgelehnt. Die Auswahl eines Zielgeräts
ersetzt keine Berechtigung zur Mikrofonaufnahme. Erforderliche native
Berechtigungen bleiben Aufgabe der Android-Bridge.

## Discovery

Beim Öffnen werden Ausgänge und Mikrofone gesucht. **Ausgänge scannen** bzw.
**Eingänge scannen** aktualisiert die Listen; eine Textsuche filtert Treffer.
Scanstatus zeigt Zeitpunkt, Trefferzahl und Fehler. Die Auswahl bevorzugt
das Systemstandardgerät, manuelle Eingaben bleiben möglich.

PulseAudio-/PipeWire-Suche verwendet begrenzte pactl-Aufrufe mit fünf Sekunden
Timeout und höchstens 64 Geräten. Monitor-Quellen werden nicht als Mikrofone
vorgeschlagen. ALSA-Mikrofone werden über /proc/asound erkannt, auch ohne pactl.
Die Auswahl setzt das Capture-Backend auf alsa. Benannte Karten-IDs vermeiden
die Abhängigkeit von gewöhnlichen numerischen Kartenwechseln. Reine
Wiedergabegeräte werden nicht als Mikrofone angeboten.

Die [Audio-Ausgabe](AUDIO_OUTPUT.md) registriert erkannte lokale Ausgänge mit
stabiler ID; Name und Lautstärke bleiben bei erneutem Scan erhalten, verschwundene
Geräte werden offline. Remote-Definitionen werden nicht überschrieben.
Eine Registrierung allein bestätigt noch keine erfolgreiche Wiedergabe.

RTP-Ziele werden derzeit manuell angegeben. Die lokale Mikrofonsuche ist keine
Empfängersuche im Netzwerk. Ein dauerhaft verschwundenes bevorzugtes Mikrofon
wird nicht stillschweigend durch ein anderes aufgenommen.

## Ports

RTP verwendet den konfigurierten UDP-Port, RTCP den Folgeport. Receiver verwenden
FFmpegs localaddr für die konkrete Bind-Adresse. Für RTP und RTCP müssen nutzbare
Ports verfügbar sein. Die SDP-Datei beschreibt den Stream; sie ist keine
Firewallregel. Die Administration nutzt den vorhandenen SimpleOffice-HTTP-Port.

## Security

Steuerung und SDP-Download verlangen Anmeldung und Adminrolle. Mutationen
benötigen den bestehenden CSRF-Token. Hostnamen, IP-Adressen, Ports und
Einstellungen werden validiert; Programme starten mit Argumentlisten ohne Shell.

Dieser RTP-Pfad ist unverschlüsselt und hat keine Teilnehmerauthentifizierung.
Er ist nur für ein vertrauenswürdiges LAN/WLAN vorgesehen; andernfalls muss der
Netzzugriff außerhalb dieses Dienstes geschützt werden. Ein verschlüsselter
Federation-/VPN-Transport ist hier nicht implementiert. Autostart ist zunächst
aus, damit Mikrofone nicht ohne bewusste Auswahl freigegeben werden.

## Fehlerdiagnose und Recovery

| Anzeige / Problem | Prüfung und nächste Aktion |
|---|---|
| Keine Eingänge | Mikrofon und Audio-Benutzersitzung prüfen, erneut scannen; bei ALSA Karteninventar prüfen |
| Keine Ausgänge | PulseAudio/PipeWire-Pulse und pactl prüfen; Desktop-Receiver benötigt diese Sitzung |
| Start fehlgeschlagen | FFmpeg/libopus, Geräte, Bind-Adresse und freie RTP/RTCP-Ports prüfen |
| Receiver wartet | Senderstatus, Zieladresse, Port, Netzverbindung und Firewall prüfen |
| Virtuelles Mikrofon fehlt | pactl und Null-Sink prüfen; im Zielprogramm dessen .monitor auswählen |
| Einstellungen nicht verfügbar | Dateirechte und SQLite-Datenbank prüfen; anschließend erneut versuchen |
| Wiederholungen ausgeschöpft | Ursache beheben und explizit neu starten |

Ein gemeinsamer Audio-Health-Thread überwacht Prozesse. Nach einem Ausfall folgen
höchstens die konfigurierten Wiederholungen mit exponentiellem Backoff. Stop
bricht Wiederholungen ab. Nach 60 Sekunden stabilem Betrieb wird das Fehlerbudget
zurückgesetzt. Status, letzter Fehler und Wiederholungen werden strukturiert
bereitgestellt; rohe Exceptions erscheinen nicht im normalen UI.

Der Receiver bestätigt PCM-Daten am Verteiler. Das ist kein Nachweis, dass ein
physischer Lautsprecher hörbar spielt. Stop räumt Decoder, Player, Pipes und
virtuelle Mikrofone auf.

## API

Basis der bestehenden Fach-API: `/admin/mini-services/audio/streamer`.
Alle nachfolgenden Pfade sind relativ zu dieser Basis.

| Methode | Pfad | Funktion |
|---|---|---|
| GET | /status | Laufzeitstatus von Sender und Receiver |
| GET | /inputs | automatische Mikrofonsuche; ?backend=alsa beschränkt auf ALSA |
| GET | /outputs | lokale PulseAudio-/PipeWire-Ausgänge |
| GET | /settings | gespeicherte Konfiguration beider Dienste |
| POST | /sender/start, /receiver/start | starten mit validierten Einstellungen |
| POST | /sender/stop, /receiver/stop | stoppen |
| POST | /sender/restart, /receiver/restart | mit gespeicherten Werten neu starten |
| POST | /sender/settings, /receiver/settings | Einstellungen aktualisieren |
| POST | /sender/reset, /receiver/reset | stoppen und Standardwerte speichern |
| GET | /receiver.sdp?port=5004 | SDP für RTP/Opus-Player herunterladen |

Zusätzlich registriert `POST /admin/mini-services/audio/scan` die lokalen
Ausgänge. Die gemeinsame API unter `/api/mini-services` stellt die Audio-Dienste
ebenfalls bereit; siehe [gemeinsamer Betrieb](MINI_SERVICES.md).

## Plattformen

| Plattform | Unterstützung / Grenze |
|---|---|
| Linux | Sender über PulseAudio oder ALSA; Receiver über PulseAudio/PipeWire-Pulse |
| Windows | Dieser Desktop-Capture-Pfad besitzt kein natives Windows-Backend; fehlendes PulseAudio wird als Fehler angezeigt |
| Android | vorhandene native Bridge mit Systemgeräten; Desktop-Scans überschreiben die native Auswahl nicht |
| Externe Tablets/Player | können RTP/Opus über die SDP-Datei empfangen, sofern der Player dies unterstützt |

## Einschränkungen und Tests

ALSA-Lautsprecher sind kein zusätzlicher Receiver-Backend. RTP-Zieldiscovery,
verschlüsselter Audiotransport und eine automatische Übernahme eines beliebigen
Ersatzmikrofons sind nicht implementiert. Hardwareentfernung wird über
Prozess-/Discovery-Zustand erkannt; ein weiterlaufender, aber stummer Treiber
kann zusätzliche manuelle Diagnose erfordern.

Lifecycle-, Konfigurations-, Discovery- und API-Tests mocken Systemgrenzen und
benötigen keine Spezialhardware. `tests/test_audio_rtp_loopback.py` prüft mit
vorhandenem FFmpeg/libopus einen synthetischen RTP→PCM-Stream auf Loopback; fehlt
FFmpeg, wird der Test übersprungen. Reale Linux-/Windows-/Android-Geräteabnahme
und mobile visuelle Abnahme sind damit nicht ersetzt.
