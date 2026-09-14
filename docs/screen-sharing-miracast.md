# Bildschirm teilen: Miracast / Wi-Fi Display

SimpleOffice4Me trennt Medienwiedergabe (DLNA) und Bildschirmfreigabe. Miracast/Wi-Fi Display ist fuer interaktive Bildschirmuebertragung gedacht und benoetigt plattformspezifische Unterstuetzung.

## Windows 10/11

### Bildschirm senden

1. WLAN eingeschaltet lassen; Miracast benoetigt Wi-Fi Direct, auch wenn die normale Netzwerkverbindung ueber Ethernet laeuft.
2. `Win + K` druecken.
3. Drahtlose Anzeige auswaehlen.
4. In Windows mit `Win + P` den Modus Duplizieren oder Erweitern waehlen.

### Windows als Empfaenger

1. Einstellungen -> Apps -> Optionale Features -> Feature hinzufuegen.
2. **Wireless Display / Drahtlose Anzeige** installieren.
3. Einstellungen -> System -> Projizieren auf diesen PC.
4. Empfang aktivieren und die Connect-/Drahtlose-Anzeige-App starten.

SimpleOffice4Me öffnet diese Betriebssystemdialoge über den Menüpunkt **Bildschirm**. Die eigentliche Miracast-Aushandlung bleibt beim Windows-Systemstack. Falls das optionale Windows-Feature fehlt, muss es weiterhin bewusst über Windows installiert werden.

## Linux

Linux besitzt keinen einheitlichen, distributionsuebergreifenden Miracast-Systemdialog. Deshalb werden zwei offene Backends vorgesehen.

### Linux als Sender: GNOME Network Displays

Empfohlener Weg unter GNOME/Wayland:

```bash
flatpak install flathub org.gnome.NetworkDisplays
flatpak run org.gnome.NetworkDisplays
```

Je nach Distribution existiert auch ein natives Paket `gnome-network-displays`.

Voraussetzungen:
- funktionierendes Wi-Fi Direct / P2P im WLAN-Treiber,
- PipeWire fuer Bildschirm und Audio,
- GStreamer-Codecs fuer H.264/AAC je nach Distribution.

### Linux als Empfaenger: MiracleCast

MiracleCast stellt einen Open-Source-Wi-Fi-Display/Miracast-Stack bereit. Fuer Receiver-Betrieb werden insbesondere `miracle-wifid` und `miracle-sinkctl` benoetigt.

Nach der systemseitigen Einrichtung öffnet SimpleOffice nur die unprivilegierte Receiver-Steuerung:

```bash
miracle-sinkctl --uibc
```

Wichtig: MiracleCast kann mit NetworkManager um die WLAN-Schnittstelle konkurrieren. SimpleOffice4Me startet deshalb weder Root-Kommandos noch `miracle-wifid` und stoppt NetworkManager nicht. Eventuell nötige Systemdienste müssen bewusst vom Administrator eingerichtet werden.

Fuer einen dauerhaften Rechner ist eine eigene WLAN-Schnittstelle fuer Wi-Fi Direct sinnvoll, damit die normale Netzwerkanbindung nicht unterbrochen wird.

## Android

Android-Hersteller nennen Miracast je nach Geraet z. B. `Cast`, `Smart View`, `Drahtlosprojektion` oder `Bildschirm uebertragen`. Moderne Pixel-Geraete setzen dagegen stark auf Google Cast und bieten Miracast nicht zwingend an.

SimpleOffice4Me stellt zwei Wege bereit:

- System-Cast-Auswahl oeffnen, wenn das Geraet sie anbietet.
- eigener SimpleOffice-Screen-Share über Android `MediaProjection` als herstellerunabhängiger Fallback. Die aktive Aufnahme läuft als sichtbarer Foreground-Service und lässt sich in der Oberfläche sowie über die Android-Benachrichtigung stoppen.

`MediaProjection` muss immer mit sichtbarer Zustimmung des Benutzers gestartet werden; stille Bildschirmaufnahme ist nicht vorgesehen.

## SimpleOffice ↔ SimpleOffice

Sender und Empfänger öffnen **Bildschirm**. Der Sender wählt **Teilen starten** und übermittelt den angezeigten achtstelligen Verbindungscode. Der Empfänger trägt den Code ein und wählt **Verbinden**. Die Bildspur wird per WebRTC übertragen; der Flask-Endpunkt transportiert ausschließlich Offer, Answer und ICE-Signale. Im normalen Browser bleibt `navigator.mediaDevices.getDisplayMedia()` der Capture-Pfad.

Die aktuelle Signaling-Implementierung verwendet keine externen STUN-/TURN-Dienste und ist damit für direkte Verbindungen im erreichbaren lokalen Netz ausgelegt. Für Verbindungen über NAT wäre ein bewusst konfigurierter ICE-Dienst erforderlich.

## Sicherheitsmodell

- keine automatische Bildschirmfreigabe,
- Empfang standardmaessig bestaetigen,
- keine versteckten Sessions,
- OS-spezifische Miracast-Dienste nicht mit Root-Rechten aus dem Webprozess starten,
- keine frei zusammengesetzten Shell-Kommandos,
- aktive Session muss in der UI sichtbar und stoppbar sein.
