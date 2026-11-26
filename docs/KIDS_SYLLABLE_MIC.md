# Silben klatschen: freiwilliges Mikrofon / Optional syllable microphone

**Kinder → Silben klatschen → Klatsche mit → Mikrofon ausprobieren.**
Tasten und Tippen funktionieren weiterhin ohne Berechtigung oder Ton, auch in der
Vorschulrunde. Das Mikrofon fragt erst nach Klick auf einen der beiden Modi.
Ziel und gemessene Zahl sind sichtbar. Mit **−/+** lässt sich die Zahl ändern,
**Zahl prüfen** bestätigt sie. Die Erkennung allein beantwortet keine Aufgabe.

- **Mit Mikrofon klatschen:** kurz leise bleiben, dann einzelne kurze Impulse mit
  Abstand klatschen. Längere/anhaltende Geräusche zählen nicht als Klatschen;
  sehr lauter Hintergrund beim Start beendet das Mikrofon.
- **Mit Pausen sprechen:** z. B. „Ba … na … ne“, jeweils klar und mit kurzen
  stillen Pausen. Das Spiel zählt geeignete periodische Klangabschnitte, nicht
  Wörter, „richtige“ Aussprache, Sprache, Stimme oder Person. Ein Ton kann
  wie Stimme wirken. Sehr kurze oder leise Laute können fehlen. Das ist keine
  Sprachdiagnostik und ersetzt keine Logopädie.

**Trennung von Ton und Aufnahme:** Ein Vorlesen oder Vormachen stoppt die
Messung vor der Wiedergabe. Umgekehrt beendet ein bewusster Mikrofon-Klick
noch laufende Übungstöne, wartet auf eine kurze ruhige Phase und beginnt
erst danach zu zählen. Die App schaltet den Stream nach Tastendruck, Stopp,
Ende, Wechsel der Aufgabe, Schließen der Mikrofonbox, Tab-/Seitenwechsel
und längerer Inaktivität aus. Das Spiel bleibt ohne Zeitlimit; die
30 Sekunden betreffen nur die inaktive Mikrofon-Sitzung. Verspätete Freigaben
starten nach Wegklicken keine alte Aufgabe. Es gibt keine Aufzeichnung,
Übertragung, Wiedergabe oder persistente Speicherung des Mikrofonsignals.
[Web Audio AnalyserNode](https://webaudio.github.io/web-audio-api/#AnalyserNode)
bleibt ohne Verbindung zum Lautsprecherausgang.

**Voraussetzungen:** HTTPS oder localhost, ein Browser mit MediaDevices und
Web Audio. Die bestehende APK erlaubt `RECORD_AUDIO` über ihren lokalen
WebView-Berechtigungsfluss; diese Änderung fügt keine Android-Permission und
keine native Brücke hinzu. Nach einem Systemdialog ggf. erneut bewusst
einschalten. Auf APK/Handy und mit physischen Mikrofonen ist die Genauigkeit
dieser ersten Ausführung nicht geprüft.

**English:** Open Kids → Syllable clapping → Clap along → Try the microphone.
The two voluntary modes count brief impacts or voiced blocks separated by
silence. No word/speech/speaker recognition; always review or correct the
count. Audio input remains in memory and is shut down before prompts and
demonstrations. There is no recording, capture upload or monitoring playback.

## Nachstellen / Reproduce

```sh
node --test tests/kids_syllable_mic.test.cjs tests/kids_syllables_example.test.cjs tests/kids_syllables_frontend.test.cjs
PYTHONPATH=.:tests python -m unittest   tests.test_kids_syllable_mic tests.test_kids_syllables tests.test_kids_preschool_round
# After installing the existing web-export extra and Chromium in a test environment:
PYTHONPATH=.:tests python -m unittest -v tests.test_kids_syllable_mic_browser
```

Node verarbeitet deterministisch erzeugte Impulse, Echos, periodische
Klangblöcke und Rauschen bei 16, 44,1 und 48 kHz. Wenn FFmpeg bereits
vorhanden ist, prüft Node zusätzlich die im Projekt vorhandenen,
[synthetisch mit Piper/Thorsten erzeugten Clips](../static/audio/kids/games/README.md)
„Ba … na … ne“, mit 200 ms künstlichen Pausen, im Speicher. Keine neue
Aufnahme oder Audiodatei wird verteilt. Die Playwright-Tests führen
vom angemeldeten Kindermenü bis zur ausgelieferten Browser-Analyse. Sie
speisen rein im RAM erzeugte Klangblöcke über einen virtuellen
`MediaStreamAudioDestinationNode` in den echten Web-Audio-Pfad ein.
Der Lautsprecher bleibt vom Testsound getrennt. Berechtigungsablehnung
und späte Antworten sind Testfälle, keine OS-Messung. Keine Kinderstimme,
Fremdaufnahme, physisches Mikrofon oder Android-Gerät kommt in diesen
Tests zum Einsatz.
