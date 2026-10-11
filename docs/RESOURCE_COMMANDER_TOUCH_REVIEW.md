# Resource Commander: Touch-/Dialogreview (PR #653)

## Korrekturen und Bedienung

- Dialogfelder beachten `hidden`; ausgeblendete Eingaben sind deaktiviert.
- Escape, Abbrechen und Schließen lösen keine Mutation aus und geben den Fokus
  an den Auslöser zurück. Native Dialoge halten den Hintergrund inert.
- Beim Löschen liegt der Anfangsfokus auf Abbrechen. Erst explizites Bestätigen
  sendet genau einen Löschauftrag. Dateinamen werden als Text ausgegeben.
- Tastaturfokus auf einer Dateizeile wählt diese aus und aktiviert deren Seite.
  Leertaste wählt aus, Enter öffnet. F5–F8 sind während Dialogen und Eingaben
  unterdrückt, einschließlich Browser-Neuladen; Wiederholung löst nichts aus.
- Navigation hat mindestens 44 × 44 Pixel große Buttons. Dialogaktionen sind
  mindestens 48 Pixel hoch. Seitliche Safe-Areas und die vom vorhandenen
  Mobile-Viewport-Modul gemeldete Bildschirmtastatur werden berücksichtigt.

## Automatisierter Nachweis

`tests/test_resource_commander_browser.py` lädt die unveränderten produktiven
Commander-Templates, Styles und Skripte gegen isolierte Provider-Testantworten.
Er prüft Abbruchwege, Fokuswiederherstellung, inerten Hintergrund, Felderwechsel,
Löschbestätigung, CSRF-Header, Textausgabe manipulierter Dateinamen, Tastatur- und
Touch-Bedienung sowie 320×568, 390×844, 760×360, 1024×768 und 1440×900 Pixel.
Die Keyboard-Geometrie wird mit den bestehenden Viewport-CSS-Variablen simuliert.
Der Ubuntu-Kompatibilitätsworkflow führt die Tests nach der Chromium-Installation
im Browser-Job aus. Lokal:

```sh
python -m playwright install chromium
python -m unittest discover -s tests -p test_resource_commander_browser.py -v
```

Die HTML-Semantikprüfung erlaubt `method="dialog"` nur für Formulare innerhalb
von `<dialog>`; Netzwerkformulare bleiben auf GET/POST beschränkt.

## Abgrenzung

Headless Chromium ersetzt keine Prüfung auf echter Android-Hardware oder Safari.
Safe-Area-Werte echter Kameraausschnitte sind geräteabhängig.
Mail-Tabellenregression aus #651 und bestehende Metadatenpfad-Tests werden in
separater CI-Abhängigkeit korrigiert; keine Mail- oder Provider-Serveränderungen
gehören zum UI-Diff von #653.
