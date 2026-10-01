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


## Kontrollierter Offline-Arbeitsbereich in der APK

Die native APK besitzt zusätzlich zum unveränderten PWA-Verhalten einen begrenzten,
app-privaten Offline-Arbeitsbereich. Die Funktion ist additiv und standardmäßig
deaktiviert. Sie wird mit `SIMPLEOFFICE_V3_ANDROID_OFFLINE_ENABLED=1` aktiviert.
Ist die Capability aus, wird weder die Navigation noch
`window.SimpleOfficeOffline` bereitgestellt und die API liefert 404.

Die integrierte Oberfläche liegt unter **Mehr -> Android Offline**. Aktuell ist
bewusst nur der fachlich vollständig synchronisierte Workset **Aufgaben** für die
Offline-Auswahl freigegeben:

- Eine sichtbare Aufgabe wird erst nach ausdrücklicher Auswahl lokal gespeichert;
  nicht ausgewählte Aufgaben werden nicht gespiegelt.
- Der lokale Datensatz enthält die Server-ID, einen starken ETag als Version,
  Speicher-/Ablaufzeit und die Workset-Zuordnung.
- Der Speicher ist auf 128 Einträge, 16 MiB je Eintrag und 128 MiB insgesamt
  begrenzt; die maximale Aufbewahrung beträgt 30 Tage. Die UI zeigt Belegung,
  Outbox und Belegung pro Workset.
- Kalender, Kontakte, Mail und Federation bleiben online-only. Passwörter,
  Session-Secrets, OAuth-/API-Schlüssel, Federation-Private-Keys und Vault-
  Geheimnisse werden von der Offline-API nicht ausgeliefert.
- Als Offline-Schreiboperation ist ausschließlich der **Aufgabenstatus**
  zugelassen. Die Outbox speichert Operation-ID, Ziel, Basis-ETag, Zeitpunkt,
  Payload und Status.
- Beim Wiederverbinden sendet die UI ausstehende Operationen an
  `/api/v3/android-offline/sync`. Der Server nutzt den bestehenden
  `TodoStore`, dessen Aufgabenrechte und starke ETags.
- Operationen sind serverseitig pro Benutzer idempotent. Mutation und
  Idempotenz-Receipt werden atomar im bestehenden Aufgabenbestand geschrieben.
  Ein Retry kann dieselbe Änderung daher nicht zweimal anwenden.
- Ist der Server-ETag nicht mehr die gespeicherte Basisversion, wird die Operation
  als `conflict` festgehalten. Es gibt kein Last-Write-Wins. In der UI kann der
  Benutzer den Konflikt verwerfen und den aktuellen Serverstand übernehmen.
- Das Android-Netzwerkmonitoring löst beim Wiederverbinden einen Sync aus. Ein
  manueller Sync bleibt zusätzlich verfügbar.
- Der Cache ist gleichzeitig an die ausgewählte Android-Identität und die
  authentifizierte SimpleOffice-Benutzer-ID gebunden. Auf einer ausgeloggten Seite
  wird die Offline-Bridge nicht bereitgestellt. Wechselt die Web-Session trotzdem
  zu einem anderen SimpleOffice-Benutzer, verwirft `enforceOwner()` den vorherigen
  Offline-Bestand, bevor er gelesen werden kann. **Android-Konto wechseln** löscht
  Offline-Daten und Outbox zusätzlich vor dem Löschen von Identität und
  WebView-Cookies.
- Der gesamte Offline-Bereich kann manuell gelöscht werden.

Die PWA bleibt unverändert: Ihr Service Worker speichert weiterhin keine Kontakte,
Personal-, Kalender- oder Dokumentdaten. Weitere fachliche Offline-Typen dürfen erst
angebunden werden, wenn ihr eigener serverseitiger Versions-, Rechte- und
Konfliktpfad ebenso vollständig implementiert ist.
