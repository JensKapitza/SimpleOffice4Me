# Fingerzauber am PC / Finger magic on a PC

**Kinder → Fingerzauber.** Ohne Kamera lässt sich die Zeichenübersicht ansehen.
Mit Kamera kannst du Kristall, Stern, Schild und Funke üben oder zu zweit
spielen. Jede Person startet ihren eigenen Zug mit demselben Ablauf und
30 Sekunden Zeit. Gehaltene Zeichen zählen nur einmal; vor einem Doppelzeichen
die Hand kurz aus dem Bild nehmen. Falsche oder unsichere Zeichen zählen nicht.
Wenn beide alle Treffer schaffen, entscheidet die kürzere Zeit; bei gleichen
Teilergebnissen gibt es ein Unentschieden. Das sind erfundene Spielzeichen, kein
Fingeralphabet und keine Übersetzung der Deutschen Gebärdensprache (DGS).

## Einmalige Einrichtung durch Erwachsene / One-time administrator setup

Ein aktueller PC-Browser auf **HTTPS oder localhost** und eine Webcam werden
benötigt. Die Anwendung enthält keine Drittanbieter-Binärdateien und lädt sie
weder beim Start noch beim Klick aus dem Internet. Erwachsene müssen einmalig die
folgenden zwei Originaldateien bewusst beschaffen und lokal bereitstellen:

- [MediaPipe Tasks Vision 0.10.21](https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-0.10.21.tgz).
  SHA-256: `343736570f433c0100ffc360dbe70f09cc502a5fc573f5cd95954692edc9cfde`.
- [Offizielles MediaPipe Hand Landmarker Full FP16 /1/-Modell](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task).
  SHA-256: `fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1`.

Beispiel für Unix-Shells; der Ordner muss für den SimpleOffice-Prozess lesbar
sein, aber darf nicht im Upload-/Dokumentordner liegen:

```sh
python tools/provision_kids_hand_game.py \
  --package /pfad/tasks-vision-0.10.21.tgz \
  --model /pfad/hand_landmarker.task \
  --destination "$HOME/.config/simpleoffice/hand-game"
export SIMPLEOFFICE_KIDS_HAND_GAME_ASSET_DIR="$HOME/.config/simpleoffice/hand-game"
./start.sh run
```

Unter Windows denselben absoluten Zielordner als Umgebungsvariable vor dem
Start setzen. Bei Docker den Zielordner lesbar und vorzugsweise schreibgeschützt
in den Container mounten und die Variable auf den Containerpfad setzen. Den
Server nach einer Änderung der Startumgebung neu starten. Das Einrichtungsprogramm
prüft das Paket und alle vier übernommenen Dateien, installiert keine
Software und lädt nichts herunter. Unvollständige oder veränderte Dateien
lassen das Kameraspiel sichtbar deaktiviert; die anderen Spiele bleiben
unverändert. Keine ungetestete Aussage über Electron, Android, iOS oder
physische Kameras.

**English:** A current desktop browser at HTTPS or localhost is needed. Fetch
the exact runtime and model manually, verify/stage them with the command
above, then set `SIMPLEOFFICE_KIDS_HAND_GAME_ASSET_DIR` before starting the
server. The browser loads validated assets from the signed-in SimpleOffice
server only. Either hand works; mirroring only changes the display.

## Datenschutz / Privacy

Im Kinderbereich erlaubt nur diese Spielseite die Kamera; weder Mikrofon noch Bildschirmaufnahme werden freigegeben.
Die Geräteabfrage folgt erst auf einen Klick und eine erfolgreiche lokale
Modellinitialisierung. Vorschau und Handpunkte verbleiben im Browser,
Ergebnisse im Arbeitsspeicher der Seite. Es gibt keine Aufnahme-, Upload- oder
Speicherschnittstelle für Bilder, Handpunkte, Namen oder Spielstände. Stopp,
Seitenwechsel, Tab-Wechsel und Erkennungsfehler beenden den Stream. Ein
verspätetes Kameraversprechen kann den Stream danach nicht erneut öffnen.
Keine Cloud-Inferenz, Telemetrie- oder CDN-Pfade in diesem Spiel.

## Quellen und Lizenzen / Sources and licenses

- **Laufzeit:** [MediaPipe-Code v0.10.21](https://github.com/google-ai-edge/mediapipe/tree/v0.10.21)
  und die offizielle npm-Paketmetadatei nennen Apache-2.0. Der Installer
  übernimmt unverändert nur das ES-Modul und den portablen Non-SIMD-WASM-Loader
  samt WASM. Die Original-Lizenz liegt in [licenses/MEDIAPIPE-APACHE-2.0.txt](../licenses/MEDIAPIPE-APACHE-2.0.txt);
  die Einrichtung legt Lizenz und Herkunftshinweis direkt neben die Dateien.
- **Gewichte:** Die [offizielle Funktionsdokumentation](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker)
  verweist auf die [Modellkarte des exakten Bundles](https://huggingface.co/litert-community/MediaPipe-HandLandmarker-Full).
  Paket SHA-256, Modellversion sowie Apache-2.0 sind dort separat ausgewiesen.
  Es enthält Palmendetektor und 21-Punkte-Handmodell samt eingebetteten
  Labels. Das Repository und veröffentlichte Anwendungspakete enthalten
  keine Fremdbinärdateien. Trainingsbilder werden nicht weitergegeben.
- **Spielmaterial:** Eigene Regeln, Ziele und Texte mit Unicode-Symbolen;
  keine fremden Figuren oder Vorlagen. Ein DGS-Fingeralphabet-Modul bleibt
  offen, bis fachlich geprüfte Definitionen, Rechte und reproduzierbare
  Tests für Ausrichtung, Bewegung und Wiederholung vorliegen. Siehe
  [Deutscher Gehörlosen-Bund zur DGS](https://gehoerlosenbund.de/service/ratgeber-gehoerlosigkeit/deutsche-gebaerdensprache/).
- **Browser-Testbild:** `pointing_up.jpg` ist ein öffentlich abrufbares Bild aus
  [Googles eigenem Hand-Landmarker-Test](https://github.com/google-ai-edge/mediapipe/blob/v0.10.21/mediapipe/tasks/cc/vision/hand_landmarker/hand_landmarker_test.cc).
  CI lädt es ausdrücklich nur flüchtig für den Test, prüft die genaue
  Prüfsumme und veröffentlicht weder Foto noch Video als Repository-,
  Build- oder Testartefakt. Keine Behauptung über eine separate
  Weiterverbreitungslizenz für das Foto. Die Leerbildkontrolle ist selbst erzeugt.

## Nachprüfen / Reproduce

```sh
node --test tests/kids_hand_game.test.mjs
PYTHONPATH=.:tests python -m unittest -v test_kids_hand_game
```

Der Workflow „Kids local hand model“ lädt die drei gepinnten offiziellen
Dateien nur in seinem temporären Runnerordner und führt
`tools/make_kids_hand_test_video.py` aus. Das erzeugt zwei synthetische
Webcam-Quellen: wiederholtes öffentliches Testbild bzw. schwarzer Frame.
Für denselben Browsertest mit vorhandenem Playwright/Chromium:
`SIMPLEOFFICE_KIDS_HAND_PUBLIC_TEST_VIDEO` auf `pointing.y4m` und
`SIMPLEOFFICE_KIDS_HAND_BLANK_TEST_VIDEO` auf `blank.y4m` setzen, zusätzlich
den oben eingerichteten Asset-Ordner konfigurieren und
`PYTHONPATH=.:tests python -m unittest -v test_kids_hand_game_browser` ausführen.
Die Kameraquelle ist im Test immer virtuell, selbst wenn Chromium und
MediaPipe tatsächlich laufen. Physische Webcam und DGS werden damit nicht
geprüft.
