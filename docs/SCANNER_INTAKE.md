# Bücher und Gegenstände als private Entwürfe erfassen

**Inventar → Fotoeingang → Bücher & Gegenstände als Entwurf erfassen.**
Ein Foto wird beim Auswählen zuerst als Entwurf gespeichert; ein gültiger
Barcode funktioniert auch ohne Foto. Beides erfolgt ohne OCR,
externen Abruf oder Inventarobjekt. Ohne JavaScript den Knopf zum Anlegen
verwenden. Der Entwurf bleibt nach dem Neuladen an seiner Position; Regal
oder Box sind optional. Im Browser unterstützte Barcodes lassen sich lokal
aus dem gespeicherten Foto lesen. Falls das Gerät das nicht unterstützt,
den Code eingeben und speichern. Mobilgeräte benötigen weiterhin eine
Verbindung zur eigenen SimpleOffice4Me-Instanz; manuelle Eingabe braucht
keinen externen Internetdienst.

- **Manuell:** Felder eingeben und den Entwurf aktualisieren.
- **OCR:** Auf Wunsch läuft Tesseract auf dem SimpleOffice4Me-Server. Das
  Bild bleibt lokal. Lesbarer Text bleibt als eigener Beleg sichtbar. Nur
  eindeutig bezeichnete Werte (beispielsweise `Marke: …`) ergeben direkt
  auswählbare Feldvorschläge. Einzelne unbeschriftete OCR-Zeilen sind nur
  mögliche Titel, keine automatische Einstufung. Bei fehlender Sprache, leerem Ergebnis oder
  unleserlichem Bild sind manuelle Angaben weiterhin möglich.
- **Katalog:** Erst nach ausdrücklicher Zustimmung und Button werden
  ausschließlich geprüfte ISBN/EAN-Kennungen gesendet. Für Quellen,
  Belegregeln und Grenzen siehe [Scanner-Kataloge](SCANNER_CATALOG.md).
- **Lokale KI:** Optional. Nur abgeleitete Textwerte und deren vorhandene
  Belegkennungen werden an die eigene Ollama-Instanz gesendet; keine Bilder,
  Rohdokumente oder Geheimnisse. Das Modell darf nur vorhandene Belege
  auswählen, nichts erfinden oder nicht abgeglichene Buchausgaben verbinden.

Nur zwei konkret gegengeprüfte Katalogtreffer derselben Ausgabe können sich feldweise ergänzen. Ältere oder unvollständige Abgleiche müssen dafür erneut geprüft werden. Ein beschrifteter OCR-Widerspruch setzt die Katalogkarte auf Rückfrage, auch wenn der Katalog zuerst geladen wurde. Beide Quellen bleiben sichtbar; unbeschriftete OCR-Zeilen bleiben Hinweise. Bei Bedarf eine Quelle wählen oder
manuell entscheiden. Die Kachel zeigt den gewählten Titel mit seiner Herkunft.
Sobald du eine Quelle auswählst, zeigt **Aktuell zur Übernahme ausgewählt**
die gespeicherten Entwurfswerte mit Quellenhinweisen. Eine KI-Angabe zeigt
zusätzlich ihre vorhandenen Belege. Die manuellen Eingabefelder bleiben
unverändert. Nach dem Speichern bleiben die bestätigten Entwurfsangaben
und ihre Herkunft an der Karte sichtbar. Fehlt ein verifizierter Quellenstand,
etwa bei einem älteren oder wiederhergestellten Beleg, bleibt der
Inventareintrag erreichbar, ohne die Herkunft zu behaupten.
Erst **Als neues Exemplar lokal speichern** legt das
Inventarobjekt an, ohne es für die Föderation freizugeben. Die Erfassung als
Inventargegenstand und der gesonderte PDF-Buchscan mit ausdrücklicher
Empfängerfreigabe sind zwei verschiedene Vorgänge.

## Eigenen Bestand beim Erfassen prüfen

Beim gespeicherten ISBN-, EAN- oder UPC-A-Code stehen passende eigene
Exemplare direkt auf der Entwurfskarte. Verglichen werden geprüfte Codes,
nicht ähnliche Titel. Auch ältere Einträge ohne verknüpfte Ausgabe sind
einzeln mit ihrer Kennung erkennbar. Der Lagerort erscheint nur aus den
dafür verwendbaren Angaben des eigenen Eintrags; fremde Treffer und deren
Anzahl werden nicht angezeigt.

**Exemplar ansehen** sichert die Eingaben und die gewählte Box im Entwurf
und zeigt das ausgewählte Exemplar nur lesend. **Zurück zum Entwurf**
führt zur selben Karte. Das verschiebt kein Inventarstück. Für eine
Umlagerung nutzt du die bestehende Objektpflege bewusst separat.
Wird ein neuer gültiger Code im Bestätigungsschritt eingetragen, speichert
der erste Klick nur den Entwurf und zeigt den Bestand für den neuen Code.
Erst der folgende Klick legt eine weitere Kopie an. Der nächste Scan bleibt
ein eigener Entwurf und übernimmt den Standort des vorigen nicht automatisch.

## Lokale KI freischalten

Standardmäßig ist die KI aus. Für einen vertrauenswürdigen lokalen Ollama-
Dienst im selben Netzwerk-Namensraum (nicht LiteLLM/Proxy und nicht ein
anderer Container):

1. In **Ollama** laut [Ollama-FAQ](https://docs.ollama.com/faq) die Cloud per
   `OLLAMA_NO_CLOUD=1` deaktivieren und Ollama neu starten.
2. In **SimpleOffice4Me** den vorhandenen `CONTEXTFLOW_OLLAMA_URL` auf
   die literale Loopback-URL (z.B. `http://127.0.0.1:11434`) und
   `CONTEXTFLOW_OLLAMA_MODEL` auf ein bereits lokal installiertes Modell
   setzen; dann `SIMPLEOFFICE_INTAKE_LOCAL_OLLAMA=1` setzen.
3. Vor **jeder** Anfrage prüft der Scanner den positiven Cloud-aus-Status
   aus `/api/status` und den exakten installierten Eintrag ohne Remote-
   Metadaten aus `/api/tags`. Der Aufruf fordert das Modell mit `:local`
   an. Keine Modellinstallation, kein Host-Fallback, kein Proxy und keine
   Redirects. Eine ältere Version ohne Status, fehlende Angaben oder ein
   ungeeigneter Dienst bleiben deaktiviert. Diese Prüfung vertraut dem vom
   Administrator eingerichteten echten Ollama-Dienst, sie ist keine
   Netzwerk-Sandbox für beliebige Fremdprozesse.

## Grenzen und lokale Verifikation

JPEG/PNG/WebP, optional ein Foto bis 12 MiB und 24 Megapixel. Maximal 60 offene
Entwürfe pro Benutzer. Entwurfsdaten liegen im bestehenden privaten
Inventory-Fotoeingang; keine Veröffentlichung vor Bestätigung. Der neue
Entwurfsmodus ist in verschlüsseltem Storage V2 absichtlich nicht
verfügbar, bis dort Fotos **und** Entwurfsdaten mit der autoritativen
verschlüsselten Ablage verbunden sind. PDF-Erstellung bleibt lokal nutzbar.

Mit Python 3.13 und `pip install -e '.[test]'`:
```sh
python -m unittest -q tests.test_inventory_mixed_intake tests.test_inventory_catalog tests.test_inventory_intake_matches
node --test tests/inventory_intake_js.test.cjs
python -m tools.smoke_book_scans --loopback --evidence scanner-acceptance.json
python -m tools.smoke_scanner_catalog
```

Die beiden HTTPS-Prüfungen benötigen OpenSSL, Poppler und Tesseract (Englisch).
Die Testschrift ist in Pillow integriert; ein Systemfont ist nicht nötig. Der PDF-Test startet drei isolierte Instanzen, der Katalogtest eine. Alle Fotos sind synthetisch; die Katalog- und KI-Tests verwenden Fixtures und
führen keine Live-KI aus. Externe API-Zugangsdaten und ein physisches
Kameragerät sind nicht Teil der Prüfung.

`python -m tools.smoke_scanner_catalog` prüft dieselben Editionen über Anmeldung, echte HTTPS-Verbindung und echte lokale OCR. Provider- und Modellantworten stammen ausschließlich aus Fixtures. Erwartet: ergänzende Quellen nur für die belegte ISBN-Ausgabe, widersprechende OCR unabhängig von der Reihenfolge, kein Inventareintrag vor ausdrücklicher Bestätigung.

Der separate Chromium-Test im Workflow „Book scanner acceptance“ nutzt
eine lokale Testinstanz, Anmeldung und synthetische Codes im normalen
Barcode-Feld bei mobiler Bildschirmgröße. Er deckt Auswahl, Rückkehr mit
erhaltenem Ort, Barcode-Wechsel, absichtliche Bestätigung mit Wiederholung
und den nächsten Scan ab. Er prüft keine physische Kamera oder Scanner-Hardware.
In einer dafür eingerichteten Testumgebung:
```sh
python -m pip install -e '.[test,web-export]'
python -m playwright install --with-deps --no-shell chromium
SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED=1 python -m unittest -v tests.test_inventory_intake_browser
```
