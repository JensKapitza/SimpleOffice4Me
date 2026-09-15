# SimpleOffice4Me Live Audio-Streamer

Der Live-Streamer uebertraegt ein Mikrofon von einem Linux-PC/Raspberry Pi per WLAN/LAN zu einem oder mehreren Zielen. Der Empfaenger kann das Audio gleichzeitig auf einem Lautsprecher ausgeben und als virtuelles Mikrofon fuer andere Programme bereitstellen.

## Architektur

```text
Mikrofon PC/Pi A
   -> ffmpeg Capture
   -> Opus 48 kHz / RTP / UDP
   -> PC/Pi B -> ffmpeg Decode -> PipeWire/Pulse -> Lautsprecher
                                      -> simpleoffice_stream.monitor (virtuelles Mikrofon)
   -> Tablet/VLC (weiteres RTP-Ziel)
```

Audio wird live verarbeitet und nicht als WAV- oder sonstige Audiodatei gespeichert.

## Bedienung

Admin-Seite:

`/admin/mini-services/audio/streamer`

### Empfaenger

Auf PC/Pi B zuerst den Receiver starten. Standardport ist UDP 5004. Optional kann lokale Wiedergabe aktiviert werden. Ist `Virtuelles Mikrofon` aktiv, wird ein Null-Sink `simpleoffice_stream` angelegt. Anwendungen waehlen dessen Monitor-Quelle als Mikrofon:

`simpleoffice_stream.monitor`

### Sender

Auf PC/Pi A den Sender starten. Standardquelle fuer PipeWire/PulseAudio ist `default`. Fuer jedes Ziel wird `HOST:PORT` angegeben, zum Beispiel:

```text
192.168.178.30:5004
192.168.178.40:5004
```

Fuer gleichzeitiges lokales Mithoeren kann ein lokaler Receiver auf einem eigenen Port gestartet und `127.0.0.1:PORT` als weiteres Ziel eingetragen werden.

### Tablet

Die Streamer-Seite bietet eine SDP-Beschreibung fuer den gewaehlten RTP-Port. Ein Tablet kann als zusaetzliches RTP/Opus-Ziel verwendet werden, wenn der Player RTP/Opus und SDP unterstuetzt, z. B. VLC.

## Voraussetzungen

Sender:
- `ffmpeg` mit PulseAudio- oder ALSA-Eingang und `libopus`

Empfaenger:
- `ffmpeg`
- PipeWire-Pulse oder PulseAudio
- `paplay`
- fuer virtuelles Mikrofon zusaetzlich `pactl`

## Sicherheit

Die Steuer-Endpunkte sind nur fuer angemeldete Administratoren erreichbar und unterliegen dem bestehenden CSRF-Schutz. RTP selbst ist fuer das lokale vertrauenswuerdige LAN/WLAN gedacht und ist nicht verschluesselt. Fuer Uebertragung ueber nicht vertrauenswuerdige Netze soll spaeter der Federation-/VPN-Transport genutzt werden.

Hostnamen, IP-Adressen und Ports werden validiert. Externe Programme werden ausschliesslich mit Argumentlisten ohne Shell-Aufruf gestartet. Pro Sender sind maximal 16 RTP-Ziele zugelassen.
# Gemeinsame Gerätesuche

## Lifecycle und gespeicherte Einstellungen

Sender und Receiver verwenden `audio/audio-output.sqlite3` neben der
Mini-Service-Konfiguration. Gespeichert werden jeweils Aktiviert, Autostart,
höchstens sechs Startwiederholungen sowie die fachlichen Einstellungen:

| Dienst | Optionen und Standardwerte |
|---|---|
| Sender | Quelle `default`, Backend `pulse` (alternativ `alsa`), Zielliste leer, Bitrate 64 kbit/s (16–256), Wiederholungen 3 |
| Receiver | RTP-Port 5004 (1024–65535), Interface automatisch, Ausgänge leer, virtuelles Mikrofon aktiv, Sink `simpleoffice_stream`, Wiederholungen 3 |

Beide Dienste sind verwendbar, aber **Autostart ist zunächst aus**. Für
Mikrofonfreigabe müssen Ziele angegeben und Autostart bewusst aktiviert werden.
Start speichert die Auswahl; Start/Stop ändern Autostart nicht. Wiederholte
Starts mit identischen Einstellungen erzeugen keinen neuen Prozess.
Standardwerte setzt den jeweiligen Dienst zurück und stoppt die aktive Session.
Eine Änderung der Einstellungen wird beim nächsten Start übernommen; Deaktivieren
stoppt sofort. „Neustart“ verwendet die gespeicherten Einstellungen.

Der Web-Launcher startet einen gemeinsamen Audio-Health-Thread. Nach einem
Prozessausfall erfolgen höchstens die konfigurierte Anzahl an Wiederholungen
mit exponentieller Wartezeit; Stop bricht die Wiederholung ab. Nach 60 Sekunden
stabilem Betrieb wird das Fehlerbudget zurückgesetzt. Prozessstatus, letzter
Fehler, Wiederholung und Konfiguration sind separat sichtbar. Der Receiver
meldet „wartet“, bis tatsächlich PCM-Audiodaten am Verteiler ankommen.

Receiver verwenden FFmpegs `localaddr` für eine konkrete lokale IPv4-Adresse;
automatisch wird eine private LAN-Adresse, sonst Loopback gewählt. Die SDP-Datei
beschreibt weiterhin den RTP-Stream und ist kein Firewallmechanismus. RTP/RTCP
verwendet den gewählten UDP-Port und den Folgeport. Audio ist unverschlüsselt und
besitzt in diesem bestehenden Protokollpfad keine Teilnehmerauthentifizierung:
nur im vertrauenswürdigen LAN verwenden oder Netz-Zugriff extern beschränken.

Zusätzliche Admin-API: `GET .../streamer/settings`,
`POST .../streamer/{sender,receiver}/settings`, `/reset` und `/restart`.
Mutationen verlangen den vorhandenen CSRF-Token. Voraussetzungen bleiben
ffmpeg/Opus und für Wiedergabe/virtuelles Mikrofon die lokale PulseAudio-/
PipeWire-Sitzung mit paplay/pactl. Fehler werden ohne Exception-Inhalte ausgegeben.

Prüfung: Lifecycle-/Konfigurations-/API-Tests mocken Prozess- und Gerätegrenzen.
`tests/test_audio_rtp_loopback.py` verwendet vorhandenes FFmpeg mit libopus für
einen echten synthetischen RTP→PCM-Test ausschließlich auf Loopback. Bei fehlendem
optionalem FFmpeg wird dieser Test übersprungen, nichts installiert.

Die Seiten **Mini Services → Audio** und **Live Audio-Streamer** verwenden
dieselbe lokale PipeWire-/PulseAudio-Suche. Beim Öffnen werden Ausgänge gesucht;
im Streamer zusätzlich Mikrofone. „Ausgänge scannen“ bzw. „Eingänge scannen“
aktualisiert die Liste. Eine lokale Textsuche filtert die Treffer. Trefferzahl,
Zeitpunkt und Fehler werden sichtbar angezeigt.

Die Audio-Seite speichert erkannte Ausgänge unter Node `local` mit stabiler
abgeleiteter ID. Weitere Scans erhalten Namen/Lautstärke und markieren nicht mehr
vorhandene Geräte offline. Remote-Definitionen werden nicht überschrieben.
Diese Registrierung allein ist noch kein Nachweis einer laufenden Wiedergabe.

Die Streamer-Auswahl bevorzugt das Systemstandardgerät. Manuelle Eingaben bleiben
möglich; `.monitor`-Quellen werden nicht als Mikrofone vorgeschlagen. ALSA-Mikrofone
werden über `/proc/asound/pcm` und die Karten-IDs erkannt, auch wenn `pactl` fehlt.
Die Auswahl setzt das vorhandene Capture-Backend passend auf `alsa`; benannte
Karten-IDs vermeiden Abhängigkeit von gewöhnlichen numerischen Kartenwechseln.
`GET .../inputs?backend=alsa` sucht ausschließlich ALSA-Geräte. Playback-only-
Geräte werden nicht als Mikrofone angeboten. ALSA-Lautsprecher/virtuelle Mikrofone
sind damit nicht als zusätzlicher Receiver-Backend implementiert; der vorhandene
Receiver benötigt weiterhin PipeWire/PulseAudio. `pactl`-Abfragen haben fünf Sekunden Timeout und liefern
höchstens 64 Geräte. Es wird keine optionale Software automatisch installiert.

Android nutzt weiterhin den vorhandenen nativen Audio-Bridge-Pfad und seine
Systemgeräte. Desktop-Scans überschreiben die native Auswahl nicht. Windows ohne
PipeWire/PulseAudio liefert eine erklärende Meldung statt einer leeren Erfolgsanzeige.

Admin-API: `GET /admin/mini-services/audio/streamer/inputs` und `/outputs`;
`POST /admin/mini-services/audio/scan` registriert die lokalen Ausgänge und benötigt
den bestehenden CSRF-Token. Alle drei Endpunkte erfordern die Adminrolle.
