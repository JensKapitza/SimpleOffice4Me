# SimpleOffice4Me unter Android

Auf Android gibt es zwei unterschiedliche Installationen. Öffne die **PWA oder
den Browser**, um mit den Daten auf deinem vorhandenen SimpleOffice-Server zu
arbeiten. Die **native APK** bringt ihre eigene Flask-Instanz mit und verwendet
einen getrennten Datenbestand auf dem Handy.

## PWA für den vorhandenen Server installieren

1. SimpleOffice über eine feste **HTTPS-Adresse** bereitstellen. Ein gültiges
   Zertifikat ist für Service Worker und App-Installation erforderlich; eine
   Zertifikatswarnung darf nicht umgangen werden.
2. Die Adresse auf Android in Chrome öffnen und anmelden.
3. Im Browsermenü **App installieren** bzw. **Zum Startbildschirm hinzufügen**
   wählen und bestätigen.

Über eine reine HTTP-Adresse im lokalen Netz lässt sich die Seite weiterhin im
Browser verwenden, aber nicht zuverlässig als PWA installieren. `localhost` ist
nur auf dem Gerät selbst eine Ausnahme.

## Offline- und Datenschutzgrenze der PWA

Der Service Worker speichert ausschließlich statische Oberfläche, Offline-Seite
und App-Symbole. Kontakte, Personal-, Kalender- und Dokumentdaten werden nicht im
PWA-Cache abgelegt. Ohne Verbindung zum eigenen Server zeigt die App daher nur
den Offline-Hinweis.

## Native APK für die eigene Handy-Instanz

Die APK startet den mitgelieferten Python-/Flask-Server. Ihre WebView öffnet
`http://127.0.0.1:8765`. Für die LAN-Föderation lauscht der Server auch an den
Netzwerkschnittstellen des Handys. Externe HTTPS-Links übergibt die APK an
Android. Dadurch wird kein fremder Server in die APK eingebunden. Weitere
Informationen zu Installation, Signatur und Updates stehen in der
[APK-Anleitung](../android/apk/README.md).

## Foto, OCR und Reader: Wo die Arbeit läuft

| Einstieg | Foto hinzufügen | Wo lokale Werkzeuge laufen |
|---|---|---|
| HTTPS-Server im Browser oder als PWA | Foto im Webformular auswählen. Je nach Gerät kann Android auch direkt die Kamera anbieten. | Dort, wo SimpleOffice läuft. Bei Docker zählen Programme und Modellpfade **im Container**. |
| Native APK | Vorhandenes Bild aus der Android-Dateiauswahl übernehmen oder aus der Galerie über **Teilen → SimpleOffice4Me** an die Dokumentenablage übergeben. Der Import muss noch bestätigt werden. | Im Python-/Flask-Prozess der APK auf dem Handy. Werkzeuge auf einem anderen Server werden hier nicht verwendet. |

Wenn die Dateiauswahl der APK keine Kamera anbietet, nimm das Foto zuerst mit
der Android-Kamera auf und wähle es anschließend aus. Die APK verwendet den
Android-Dateidialog; die Kameraoption hängt vom Gerät und der gewählten App ab.

PDF-Seiten kann die APK mit Androids PDF-Renderer anzeigen. Das sagt noch nichts
über die Verfügbarkeit von Texterkennung, PDF-Umwandlung oder Piper aus.
Poppler, Tesseract und Piper samt Sprachmodell werden von der Standard-APK
nicht mitgeliefert. Für die Foto-OCR braucht das gewählte Backend eine verfügbare
lokale OCR-Engine, für PDF-OCR außerdem die benötigten PDF-Werkzeuge und für
Piper zusätzlich ein lokal konfiguriertes Sprachmodell. Ein separat
installierter Termux-Server ist ebenfalls eine andere Instanz.

### Lokale Sprachausgabe in der APK

Unter **Lokale Extras → Lokales Vorlesen** stellst du als Administrator für
**Reader und Mail** sowie **Kids** getrennt ein, welche Sprachausgabe läuft:

- **Automatisch:** Piper verwenden, wenn Engine und passende Stimme
  vorhanden sind. Nur wenn du den Android-Fallback für den Bereich
  ausdrücklich erlaubst, kann stattdessen die Gerätestimme einspringen.
- **Android-Gerätestimme:** Die in Android eingestellte Engine verwenden,
  ausschließlich mit bereits installierten Offline-Stimmen. Android
  erhält beim Vorlesen nur den ausgewählten Text. Verwende eine Engine,
  der du vertraust. Die App sendet Texte nicht an einen eigenen Onlinedienst.
- **Piper:** Nur vorhandener lokaler Piper, ohne automatischen Ersatz oder
  Download. Das ist kein Android-Piper-Installer.
- **Deaktiviert:** Für den gewählten Bereich keine synthetische Sprache.
  Bestehende lokale Lernaufnahmen sind keine TTS-Stimmen.

Deutsch und Englisch lassen sich aus den verfügbaren Offline-Stimmen wählen.
Wenn keine passende Stimme vorhanden ist, kannst du über die App die
Android-Einstellungen für Text-in-Sprache öffnen. Die Installation erfolgt
dort bewusst über Android; SimpleOffice lädt nichts im Hintergrund. Tempo
und Tonhöhe gelten für die Gerätestimme. Die Testansage verwendet ausschließlich
einen fest vorgegebenen Beispielsatz, niemals ein Dokument oder eine E-Mail.

Reader (TXT und EPUB), Mail/EML und die unterstützten Kids-Sprechaufgaben
verwenden denselben lokalen Sprachdienst. Der PDF-Reader kann PDFs anzeigen;
PDF-Textentnahme und OCR auf der Standard-APK sind getrennte Aufgaben. Die APK benötigt
weder Termux noch einen externen Server für die Gerätestimme. Die
Sprachausgabe von Desktop, PWA und Server bleibt unabhängig.

Vorgehensweise für die Geräteabnahme: bereits installierte DE/EN-Offline-
Stimmen auswählen, Flugmodus einschalten, je einen Text in Reader und
Mail abspielen, im Kids-Mixed-Modus Frage sehen und hören, wiederholen,
schnell zur nächsten Frage wechseln, App in den Hintergrund senden und
zurückkehren. Mit fehlender/nur online verfügbarer Stimme muss Text
sichtbar bleiben. Anschließend Android-Engine wechseln, neu starten und
die Liste prüfen; Bluetooth-Ausgabe mit demselben Ablauf separat prüfen.

Wer die OCR- und Audio-Einrichtung seines Servers nutzen will, öffnet dessen
Adresse am Handy als PWA. Siehe
[Systemwerkzeuge für Vorschauen und OCR](SYSTEMWERKZEUGE_VORSCHAU.md) und
[Audio-Ausgabe und lokales Piper](AUDIO_OUTPUT.md).

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
