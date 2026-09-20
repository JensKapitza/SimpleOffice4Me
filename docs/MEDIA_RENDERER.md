# Media / DLNA Renderer

Der Media-Renderer ist ein eigener Mini Service. Er ist von Live-RTP-Audio und
Bildschirmfreigabe getrennt und erscheint im LAN als UPnP
`MediaRenderer:1`.

## Komponenten

- `simpleoffice_media_renderer.py`: persistente Konfiguration und Media-URI-Policy.
- `simpleoffice_media_upnp.py`: AVTransport, RenderingControl und ConnectionManager.
- `simpleoffice_media_service.py`: gebundener HTTP-/SOAP-Listener und SSDP.
- `simpleoffice_media_playback.py`: sicherer Loopback-Media-Proxy und FFplay-Playback.
- `tools/mini_services.py`: gemeinsamer Worker-Lifecycle mit Start/Stop/Restart,
  Autostart, Waiting und bounded Recovery.
- `app/media_renderer_admin.py`: Administrator-Konfiguration.

## Netzwerk

Der Dienst benötigt eine **explizite lokale IPv4-Adresse**. Ein Wildcard-Bind
wird nicht verwendet. SSDP nutzt `239.255.255.250:1900/UDP` und bewirbt den
Renderer nur über die konfigurierte lokale Schnittstelle. Der HTTP-Port ist
konfigurierbar, Standard ist 8200.

Die Fachkonfiguration `enabled` und die gemeinsamen Mini-Service-Einstellungen
`enabled/autostart` müssen beide passen. Dadurch startet ein frisch
installierter Renderer nicht unbeabsichtigt im LAN.

## Media-URI- und SSRF-Schutz

Standardmäßig akzeptiert der Renderer nur HTTP/HTTPS-Medien auf privaten
Zieladressen. Zugangsdaten und URL-Fragmente sind verboten. Loopback,
Link-Local, Multicast, Unspecified und Reserved sind immer gesperrt. Öffentliche
Ziele müssen ausdrücklich aktiviert werden.

DNS-Auflösung wird vor dem Abruf validiert und gepinnt. Eine Antwort mit
gemischten erlaubten und nicht erlaubten Adressen wird abgelehnt. Redirects
werden erneut vollständig validiert.

FFplay erhält **nie die Remote-URL**. Der Player verbindet ausschließlich zu
einem zufälligen Loopback-Proxy. Nur dieser Proxy verbindet zu der validierten,
gepinnten Zieladresse. Dadurch kann FFplay nicht selbst DNS neu auflösen oder
Redirects außerhalb der Policy verfolgen.

## UPnP

Implementiert sind:

- Device Description als `MediaRenderer:1`
- AVTransport: Set URI, Next, Play, Pause, Stop, Seek, Transport-/Positionsstatus
- RenderingControl: Lautstärke und Mute
- ConnectionManager: Audio-/Video-Sink-Protokolle
- SSDP Alive/Byebye und M-SEARCH

SOAP-Requests sind größenbegrenzt und werden mit `defusedxml` geparst.
UPnP-Eventing nimmt noch keine externen Callback-URLs an und antwortet
fail-closed mit HTTP 501.

## Wiedergabe

FFplay ist die Playback-Abhängigkeit. Audio und Video werden unterstützt;
Hardware-Decoding wird mit `-hwaccel auto` angefordert. Video kann im Fenster
oder Vollbild laufen.

Auf Linux kann ein erkannter Pulse-/PipeWire-Sink über `PULSE_SINK` gewählt
werden. Unter Windows ist in diesem Stand nur der Systemstandard als bestätigt
unterstützt. Ein konfigurierter abweichender Ausgang schlägt kontrolliert fehl,
statt still auf dem falschen Gerät wiederzugeben.

Play/Pause/Seek/Volume/Mute werden plattformneutral durch kontrollierte
Player-Neustarts mit erhaltenem Transportzustand umgesetzt.

## Technische Grenzen und Abnahme

Automatisierte Tests decken Konfiguration, URI-Policy, XML/SOAP, SSDP-Grenzen,
Loopback-HTTP, Redirect-Revalidierung, Player-Handoff und Worker-Control ab.

Für die Gesamtabnahme bleiben reale Tests mit mindestens einem verbreiteten
DLNA-Controller, echter Video-/Audiohardware, Windows-Audioausgabe sowie
Firewall-/Multicast-Konfiguration notwendig. Diese Punkte dürfen ohne reale
Testumgebung nicht als praktisch bestätigt markiert werden.
