# Shopping / Einkaufslisten

## Umfang

Die Shopping-Funktion verwaltet persönliche und geteilte Einkaufslisten mit expliziten
Rechten für Lesen, Hinzufügen, Bearbeiten, Erledigen und Verwalten. Einträge bleiben
bei `not_found`, `unavailable` oder `deferred` erhalten und können später wieder
geöffnet werden.

## Lokales Produktgedächtnis

Produktwissen wird lokal in der Shopping-Ablage gespeichert und ist pro Benutzer
privat. Ein Produkt kann mit oder ohne Barcode gemerkt werden. Für EAN/UPC/GTIN wird
der validierte Barcode als stabile lokale Identität verwendet; ohne Barcode wird eine
normalisierte Kombination aus Name, Marke, Packungsgröße und Einheit verwendet.

Gespeichert werden insbesondere:

- Anzeigename, Marke und Packungsgröße
- Menge/Einheit und Kategorie
- Barcode/GTIN, sofern vorhanden
- bekannte und zuletzt verwendete Läden
- letzter angegebener Preis
- Kaufzähler und letzter Kaufzeitpunkt
- Favoritenstatus
- optionales Produktfoto, lokal als Bilddatei gespeichert

Produktfotos werden nicht in die Shopping-JSON-Datei eingebettet. SimpleOffice speichert
nur eine lokale Foto-ID; die Bilddatei liegt getrennt im geschützten
`.simpleoffice-meta/shopping-photos/`-Bereich. Uploads werden auf maximal 8 MiB
begrenzt, mit Pillow als Bild validiert, auf höchstens 1600 Pixel Kantenlänge
normalisiert und als JPEG neu geschrieben. Dabei werden EXIF-/GPS-Metadaten nicht
übernommen.

Beim Barcode-Lookup wird ein bereits bekanntes lokales Produktfoto wieder angezeigt.
Für unbekannte Produkte kann direkt am Handy ein Foto aufgenommen oder ein vorhandenes
Bild ausgewählt werden. Ein externer Produktbild- oder Barcode-Dienst ist dafür nicht
erforderlich.

Eine Änderung des Produktgedächtnisses verändert vorhandene Listeneinträge nicht.
Barcode-Lookups verwenden zuerst das private Produktgedächtnis und danach nur
Listeneinträge, die der aktuelle Benutzer ohnehin lesen darf.

Bereits bekannte Produkte können über **Nachlegen** erneut auf die aktuelle Liste
gesetzt werden. Dabei sind **Anzahl / Menge** und **Einheit** vor dem Hinzufügen
änderbar. Bei einem vorhandenen Listeneintrag können Anzahl/Menge und Einheit mit
Edit-Recht direkt angepasst werden, ohne den Artikel neu erfassen zu müssen.

## Offline und Reconnect

Wenn die Shopping-Seite bereits geladen ist, kann ein neuer Artikel bei unterbrochener
Verbindung im Browser vorgemerkt werden. Die Queue liegt ausschließlich im lokalen
Browser-Storage und wird bei wiederhergestellter Verbindung erneut gesendet. CSRF-Token werden dabei nicht persistent gespeichert, sondern erst beim Senden aus der aktuellen Seite ergänzt.

Jeder vorgemerkte neue Eintrag erhält eine `request_id`. Der Server behandelt dieselbe
`request_id` pro Liste und Ersteller idempotent, damit ein Retry keine Dublette erzeugt.

Grenzen:

- Es gibt bewusst keinen externen Produktdienst und keinen verpflichtenden Cloud-Dienst.
- Die Offline-Queue ist kein vollständiger Service-Worker-/PWA-Cache; eine noch nicht
  geladene Seite kann ohne Serververbindung nicht neu geöffnet werden.
- Statusänderungen und Listenverwaltung bleiben serverseitige Operationen.
- Push-/Systembenachrichtigungen sind optional und werden nicht erzwungen.

## Datenschutz und Sicherheit

- Listen sind standardmäßig privat.
- Produktgedächtnis ist benutzerbezogen und wird nicht durch eine Listenfreigabe geteilt.
- Kamera wird nur während eines explizit gestarteten Barcode-Scans verwendet.
- Rechte werden serverseitig geprüft.
- Barcode-Erkennung funktioniert lokal im Browser, sofern `BarcodeDetector` verfügbar ist;
  die manuelle Eingabe bleibt immer möglich.
