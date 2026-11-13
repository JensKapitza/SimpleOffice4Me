# Browser-Lesezeichen

SimpleOffice stellt unter `/bookmarks/` eine persönliche Lesezeichensammlung bereit. Sie kann als Teil der installierbaren SimpleOffice-PWA genutzt werden; das Hauptmanifest bietet einen Lesezeichen-Shortcut.

## Firefox und andere Browser

Eine Web-App kann aus Sicherheitsgründen nicht direkt die interne Lesezeichenleiste oder Datenbank von Firefox, Chrome oder Safari lesen und verändern. Für Firefox ist der unterstützte Austauschweg die Netscape-HTML-Datei:

1. In Firefox die Lesezeichen-Verwaltung öffnen und „Lesezeichen nach HTML exportieren“ wählen.
2. Die Datei in SimpleOffice unter „Firefox importieren“ hochladen.
3. Bei Bedarf in SimpleOffice Ordner, Tags oder Titel bearbeiten.
4. Über „Als Firefox-HTML exportieren“ die Sammlung wieder herunterladen und in Firefox importieren.

Der Import zeigt zuerst eine Vorschau mit neuen, doppelten und ungültigen Einträgen. Erst nach Bestätigung werden die gültigen Einträge atomar ergänzt; vorhandene Einträge bleiben stehen. Identische URLs im selben Ordner werden übersprungen. Der serverseitig gespeicherte Importentwurf ist an Benutzer und Sitzung gebunden und verfällt nach 15 Minuten. Importdateien sind auf 2 MiB und 5.000 Einträge begrenzt. Es werden nur HTTP(S)-Links übernommen; Icons und Seiteninhalte aus der Datei werden ignoriert und nicht vom Server geladen.

## Betrieb und Grenzen

Die Daten liegen benutzerbezogen in der SimpleOffice-Datenbank und sind damit nach Anmeldung auf Geräten verfügbar, die denselben Server erreichen. Der Service Worker speichert keine Lesezeichendaten offline. Eine installierte PWA verwaltet somit die SimpleOffice-Sammlung, aber synchronisiert nicht automatisch mit der nativen Firefox-Leiste. Live-Synchronisierung in den Browser erfordert eine Browser-Erweiterung oder eine browserseitige Synchronisationsfunktion.
