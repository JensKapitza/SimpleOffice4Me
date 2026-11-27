# Lokale Extras / Optional local extras

Die **Übersicht → Lokale Extras** und der bestehende Erststart unter
**Dokumente → Einrichtung** führen zum selben Assistenten. Downloadbare
Pakete sind **optional** und werden nie beim Anmelden, Starten der Anwendung
oder Öffnen eines Spiels geladen. Die Option **Vorerst überspringen** blendet
nur die Details auf der eigenen Übersicht aus. Sie ändert weder Rechte noch
Dateien, angelegte Jobs oder die Anzeige anderer Konten.

- **Reader & Mail:** Die separate Piper-Engine und die Standard-/Englischstimme
  werden unabhängig angezeigt. **Vorlesen einrichten** in Reader und
  Mail-Vorschau führt direkt hierher. **Technische Diagnose** führt separat
  zum Werkzeuge-Test in den Medien-Einstellungen. „Konfiguriert“ prüft nur Pfad und begleitende
  JSON-Datei, nicht die Wiedergabe. Die Administration kann vorhandene lokale
  Stimmen direkt in den Medien-Einstellungen wählen. Ohne EN-Einstellung
  verwendet die App wie bisher die konfigurierte Standardstimme. Der
  technische Admin-Test im Medienbereich bleibt ein separater bewusster
  Schritt. Bereits verifizierte Thorsten-Scratch-Dateien können separat über
  **Thorsten Scratch für Reader & Mail auswählen** gewählt werden. Das gilt
  nur für Reader/Mail bei deutscher Oberflächensprache (keine automatische
  Erkennung der Textsprache) und setzt weder den allgemeinen Modellpfad noch die englische Auswahl oder ihren bisherigen Fallback um.
  Die Auswahl lässt sich zurücknehmen. Werden Modell oder JSON verändert,
  bleibt die Auswahl sichtbar, liefert aber kein Audio. Diese Aktion startet
  keinen Download, keine Synthese und keinen Zusatzprozess.
- **Piper-Laufzeit:** Auf Linux x86_64 mit CPython 3.12 und glibc ab 2.28 kann
  die Administration die Engine per Klick ohne Root installieren. Sieben feste
  PyPI-Wheels (Piper 1.8.0, ONNX Runtime 1.32.0 und deren explizite
  Abhängigkeiten) haben fest eingetragene URLs, Größen und SHA-256-Werte. Alle
  Pakete werden zuerst geprüft und dann mit pip offline und --require-hashes
  in eine private, persistente App-venv installiert. Ein Status-/Versionsmanifest
  bindet den aufgerufenen Piper-Launcher an seinen Hash. Abgebrochene Promotionen
  sind mit einer Installationsmarkierung wiederholbar; Schreibvorgänge verwenden
  private Verzeichnisse und einmalige temporäre Dateien. Die Einrichtung zeigt
  Download-, Installations- und Prüfphase getrennt an. System-Piper hat Vorrang;
  manuelle Systemdateien werden nicht überschrieben. Das Setup lädt keine Stimme
  und wählt keine Stimme aus. Die Sammelaktion kann die Laufzeit auf dieser
  Plattform mit Finger-Magic-Basisdaten einrichten; Stimmen bleiben separat.
  Andere Linux-Python-Versionen, Linux-ARM, Windows, macOS, Termux und APK sind
  für diese Piper-Laufzeit noch nicht freigegeben.
- **Stimmdateien:** Unter **Reader & Mail** kann die Administration das
  deutsche, experimentelle Thorsten-Scratch-Paket (Erwachsenenstimme)
  separat und einmalig laden. Das geht auch, wenn bereits eigene Stimmen
  eingerichtet sind, ein früherer Standardpfad nicht mehr erreichbar ist
  oder die Piper-Engine auf dem System noch fehlt. „Dateien verifiziert“
  bedeutet dann nur, dass Modell und JSON bereitstehen. Für Auswahl und
  Wiedergabe muss Piper separat verfügbar sein; danach ist kein zweiter
  Download nötig. Der Download installiert weder Piper noch FFmpeg.
  Vor dem Klick sind Anbieter, Größe, Version, Lizenz und SHA-256 für Modell
  und JSON sichtbar. Das Laden ändert keine Modellpfade, Auswahl oder
  Kids-Aktivierung. Siehe [Modell und Aufnahmen (CC0 1.0)](https://huggingface.co/Thorsten-Voice/Piper-Scratch)
  und getrennt [Piper-Engine und Lizenz](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md).
- **Englische Piper-Stimme:** Das optionale LibriTTS-High-Modell (US English,
  rund 130 MiB) kann unabhängig von der Engine geladen werden. Beide Dateien
  haben feste Version und SHA-256. Der Download liest keine Nutztexte und
  installiert oder startet Piper nicht. Nur wenn Piper vorhanden ist, kann die
  Administration das verwaltete Modell ausdrücklich als englische Stimme
  auswählen. Eine bereits eingetragene eigene EN-Stimme wird nie überschrieben;
  die verwaltete Auswahl lässt sich wieder auf den bisherigen EN-Fallback
  zurücksetzen. Attribution: LibriTTS by He et al.,
  [OpenSLR 60](https://www.openslr.org/60/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- **Kids-Labyrinth:** Dieselben geprüften Dateien können als separater Schritt
  ohne erneuten Download aktiviert werden. Der Modellsatz bleibt dort auf
  deutsche Labyrinth-Ansagen beschränkt, wenn keine Standardstimme
  eingerichtet ist. Ausspracheübungen, Reader und Mail werden durch den
  Kids-Schalter nicht umgestellt. Hinweise zum gemeinsamen Paket stehen im
  Abschnitt **Reader & Mail**.
- **Fingerzauber:** Ein administrativer Klick lädt das bereits unterstützte,
  fest versionierte MediaPipe-Paket und das Handmodell serverseitig von den
  festgelegten Anbietern. Keine beliebigen URLs, keine Bilder oder Kameradaten
  an Anbieter. Erst nach Prüfung von Grenzen, SHA-256 und allen vier
  erforderlichen Dateien wird ein verwalteter Ordner aktiviert. Installation
  ist keine Funktionsprüfung von Browser oder Webcam. Falls ein Paket die
  Lizenzdatei nicht mitliefert oder der Betreiber einen manuellen Ordner
  vorgibt, bleibt [die manuelle Einrichtung](KIDS_HAND_GAME.md) maßgeblich.
  Zeichenübersicht und andere Kids-Spiele benötigen die Extras nicht.

**Status:** Ein ausdrücklicher Klick einer Administratorin/eines Administrators
merkt ein Paket vor. Die Seite unterscheidet Vormerkung, Download/Prüfung,
verifizierte Dateien und Fehler. Gleiche laufende Jobs werden nicht erneut
gestartet; nach einem Fehler braucht es einen neuen Klick. Ein durch Neustart
oder Abbruch verwaister Job wird nach höchstens fünf Minuten als fehlgeschlagen
behandelt und startet nicht von selbst erneut. Modell- und Ablaufstatus stehen
nur in den privaten Server-Metadaten; keine Nutztexte oder Aufnahmen. Der
Fingerprint/Status ist nicht gleichbedeutend mit einem Engine-/Gerätetest.

**English:** Choose **Overview → Local extras**. The same page is linked from
first-run setup and the affected Kids screens. Only an administrator can
explicitly queue a listed download; readiness is visible to signed-in accounts
and skipping it affects only the current dashboard. Reader/Mail retain their
separate local configuration; the opt-in for the verified German voice is
independent of Kids and can be reverted. The pinned files can be downloaded
  without Piper or with custom defaults. This does not install an engine, choose a
  voice or enable Kids. Once Piper is available the verified files can be reused.
  On Linux x86_64 with CPython 3.12 and glibc 2.28+, an administrator can install
  the Piper 1.8.0 runtime with a click. Seven fixed PyPI wheels are size and
  SHA-256 pinned, downloaded first, then installed offline with pip hash checking
  into persistent private app data. This downloads no voice model and changes no
  voice selection. Other operating systems, architectures, Python versions,
  Termux and the APK are not enabled for this installer.
An optional US English LibriTTS high model is also available as a separately
verified 130 MiB download. It is selected only by an administrator, only when
Piper is present, and never replaces an already configured English voice.

## Offline tests (synthetic input only)

```sh
python -m pip install -e '.[test]'
PYTHONPATH=.:tests python -m unittest -q tests.test_optional_local_setup \
  tests.test_optional_piper_runtime tests.test_first_run_setup tests.test_kids_piper_assets tests.test_kids_hand_game \
  tests.test_template_accessibility tests.test_frontend_route_contract
node --test tests/optional_setup_progress.test.cjs tests/kids_hand_game.test.mjs
```

These tests exercise signed-in setup routes, queue, retry, and the selected
voice reaching Reader/Mail/Kids through bounded test doubles. They do not
download real models, execute Piper or MediaPipe, use a physical camera, or
prove Android/browser playback.

Der reale Linux-Smoke wurde auf CPython 3.12.14, Linux x86_64 und glibc ≥2.28
ausgeführt: alle sieben Wheels wurden über den Installer geladen und geprüft,
`pip check` und `piper --help` liefen erfolgreich; danach erzeugte Piper mit dem
vorhandenen Thorsten-Scratch-Modell eine mono-16-bit-WAV-Datei mit 22.05 kHz.
Ein neuer Prozess löste die persistent gespeicherte Engine wieder auf. Dieses
Ergebnis ergänzt die Offline-Tests und ist kein Geräte- oder Plattformnachweis.

Android APK uses Chaquopy's Android ARM64 Python ABI; the
installer's manylinux wheels are not compatible with that ABI. The APK has no
verified Piper/ONNX Runtime native Android build or linked phonemizer libraries.
Android device TextToSpeech remains the functional local speech alternative.
Piper is therefore not advertised as an APK install option.
