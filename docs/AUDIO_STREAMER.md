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
möglich; `.monitor`-Quellen werden nicht als Mikrofone vorgeschlagen. ALSA-Quellen
bleiben manuell wählbar. `pactl`-Abfragen haben fünf Sekunden Timeout und liefern
höchstens 64 Geräte. Es wird keine optionale Software automatisch installiert.

Android nutzt weiterhin den vorhandenen nativen Audio-Bridge-Pfad und seine
Systemgeräte. Desktop-Scans überschreiben die native Auswahl nicht. Windows ohne
PipeWire/PulseAudio liefert eine erklärende Meldung statt einer leeren Erfolgsanzeige.

Admin-API: `GET /admin/mini-services/audio/streamer/inputs` und `/outputs`;
`POST /admin/mini-services/audio/scan` registriert die lokalen Ausgänge und benötigt
den bestehenden CSRF-Token. Alle drei Endpunkte erfordern die Adminrolle.
