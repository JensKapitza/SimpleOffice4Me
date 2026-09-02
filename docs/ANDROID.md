# SimpleOffice4Me unter Android

SimpleOffice4Me ist eine serverbasierte Flask-Anwendung. Der kleinste wartbare
Android-Weg ist deshalb die installierbare Progressive Web App (PWA): Sie erhält
ein eigenes Symbol und öffnet sich ohne Browserleiste, bleibt aber direkt mit der
selbst gehosteten SimpleOffice-Instanz verbunden.

## Installation

1. SimpleOffice über eine feste **HTTPS-Adresse** bereitstellen. Ein gültiges
   Zertifikat ist für Service Worker und App-Installation erforderlich; eine
   Zertifikatswarnung darf nicht umgangen werden.
2. Die Adresse auf Android in Chrome öffnen und anmelden.
3. Im Browsermenü **App installieren** bzw. **Zum Startbildschirm hinzufügen**
   wählen und bestätigen.

Über eine reine HTTP-Adresse im lokalen Netz lässt sich die Seite weiterhin im
Browser verwenden, aber nicht zuverlässig als PWA installieren. `localhost` ist
nur auf dem Gerät selbst eine Ausnahme.

## Offline- und Datenschutzgrenze

Der Service Worker speichert ausschließlich statische Oberfläche, Offline-Seite
und App-Symbole. Kontakte, Personal-, Kalender- und Dokumentdaten werden nicht im
PWA-Cache abgelegt. Ohne Verbindung zum eigenen Server zeigt die App daher nur
den Offline-Hinweis.

## Wann ein APK sinnvoll ist

Ein APK wäre hier nur eine WebView-/TWA-Hülle und benötigt vor dem Bau eine feste
HTTPS-Serveradresse, eine Paket-ID und einen verwalteten Signierschlüssel. Es
bringt keine echte Server-unabhängige Offline-Funktion. Falls Verteilung per MDM
oder Sideloading erforderlich ist, kann auf Basis der PWA gezielt eine signierte
TWA für genau diese Serveradresse ergänzt werden.

## Server direkt auf dem Android-Gerät

Für eine lokale Einzelplatzinstallation kann der Flask-/Waitress-Server in
Termux laufen. Termux sollte gemäß der
[offiziellen Installationsanleitung](https://github.com/termux/termux-app#installation)
installiert werden; App und Erweiterungen dürfen nicht aus unterschiedlichen
Quellen stammen.

In Termux:

```sh
pkg update -y && pkg install -y git
git clone https://github.com/JensKapitza/SimpleOffice4Me.git
cd SimpleOffice4Me
bash android/setup-termux.sh
```

Das Setup installiert die Abhängigkeiten, startet den Server unter
`http://127.0.0.1:8080` und öffnet den Browser. Beim ersten Aufruf wird unter
**Registrieren** das erste lokale Administratorkonto angelegt. Danach genügen:

```sh
simpleoffice start
simpleoffice stop
simpleoffice status
simpleoffice log
```

Die Daten liegen im privaten Termux-App-Bereich. Android kann den Prozess trotz
Wake-Lock bei Energiesparmaßnahmen beenden; Termux sollte deshalb von der
Akkuoptimierung ausgenommen werden. Das Telefon-Setup bindet nur an `127.0.0.1`
und ist nicht aus dem WLAN erreichbar. Ressourcenintensive Dokument-, OSM- und
Sensor-Hintergrunddienste bleiben auf dem Telefon standardmäßig deaktiviert.
