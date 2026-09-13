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

SimpleOffice4Me soll diese Betriebssystemdialoge ueber den Menuepunkt **Bildschirm** oeffnen und deren Verfuegbarkeit anzeigen. Die eigentliche Miracast-Aushandlung bleibt beim Windows-Systemstack.

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

Typischer manueller Start nach Installation:

```bash
sudo systemctl stop NetworkManager
sudo miracle-wifid &
miracle-sinkctl --uibc
```

Wichtig: MiracleCast kann mit NetworkManager um die WLAN-Schnittstelle konkurrieren. Deshalb soll SimpleOffice4Me den Receiver nicht ungefragt als Root starten oder NetworkManager automatisch stoppen. Die UI erkennt die Werkzeuge und zeigt den Einrichtungsstatus; Systemdienste muessen bewusst vom Administrator eingerichtet werden.

Fuer einen dauerhaften Rechner ist eine eigene WLAN-Schnittstelle fuer Wi-Fi Direct sinnvoll, damit die normale Netzwerkanbindung nicht unterbrochen wird.

## Android

Android-Hersteller nennen Miracast je nach Geraet z. B. `Cast`, `Smart View`, `Drahtlosprojektion` oder `Bildschirm uebertragen`. Moderne Pixel-Geraete setzen dagegen stark auf Google Cast und bieten Miracast nicht zwingend an.

Fuer SimpleOffice4Me sind zwei Wege vorgesehen:

- System-Cast-Auswahl oeffnen, wenn das Geraet sie anbietet.
- eigener SimpleOffice-Screen-Share ueber Android `MediaProjection` als herstellerunabhaengiger Fallback.

`MediaProjection` muss immer mit sichtbarer Zustimmung des Benutzers gestartet werden; stille Bildschirmaufnahme ist nicht vorgesehen.

## Sicherheitsmodell

- keine automatische Bildschirmfreigabe,
- Empfang standardmaessig bestaetigen,
- keine versteckten Sessions,
- OS-spezifische Miracast-Dienste nicht mit Root-Rechten aus dem Webprozess starten,
- keine frei zusammengesetzten Shell-Kommandos,
- aktive Session muss in der UI sichtbar und stoppbar sein.
