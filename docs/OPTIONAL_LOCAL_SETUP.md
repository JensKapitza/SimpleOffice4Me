# Lokale Extras / Optional local extras

Die **Übersicht → Lokale Extras** und der bestehende Erststart unter
**Dokumente → Einrichtung** führen zum selben Assistenten. Downloadbare
Pakete sind **optional** und werden nie beim Anmelden, Starten der Anwendung
oder Öffnen eines Spiels geladen. Die Option **Vorerst überspringen** blendet
nur die Details auf der eigenen Übersicht aus. Sie ändert weder Rechte noch
Dateien, angelegte Jobs oder die Anzeige anderer Konten.

- **Reader & Mail:** Die separate Piper-Engine und die Standard-/Englischstimme
  werden unabhängig angezeigt. „Konfiguriert“ prüft nur Pfad und begleitende
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
- **Kids-Labyrinth:** Thorsten Scratch ist eine erwachsene experimentelle
  deutsche Stimme. Der vorhandene #1220-Modellsatz bleibt auf den deutschen
  Labyrinth-Fallback beschränkt. Sein Download schaltet weder Reader/Mail noch
  Ausspracheübungen frei; dafür gibt es die getrennte Reader-/Mail-Auswahl. Lizenz, Version und SHA-256 für Gewicht und JSON
  stehen neben der Schaltfläche. Deaktivierte, aber vollständig geprüfte Dateien
  lassen sich separat lokal wieder aktivieren, ohne sie erneut zu laden. Siehe [Modell und Aufnahmen (CC0 1.0)](https://huggingface.co/Thorsten-Voice/Piper-Scratch)
  und getrennt [Piper-Engine und Lizenz](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/LICENSING.md).
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
and skipping it affects only the current dashboard. Reader/Mail retain their separate local configuration; the opt-in for the verified
German voice is independent of Kids and can be reverted. Verified files are not
a playback test.

## Offline tests (synthetic input only)

```sh
python -m pip install -e '.[test]'
PYTHONPATH=.:tests python -m unittest -q tests.test_optional_local_setup \
  tests.test_first_run_setup tests.test_kids_piper_assets tests.test_kids_hand_game \
  tests.test_template_accessibility tests.test_frontend_route_contract
node --test tests/optional_setup_progress.test.cjs tests/kids_hand_game.test.mjs
```

These tests exercise signed-in setup routes, queue, retry, and the selected
voice reaching Reader/Mail/Kids through bounded test doubles. They do not
download real models, execute Piper or MediaPipe, use a physical camera, or
prove Android/browser playback.
