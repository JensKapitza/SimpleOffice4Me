# Barcode in der gemischten Erfassung (Android)

In der gebündelten Android-App **Inventar → Bücher und Gegenstände erfassen →
Barcode scannen** antippen. Nur die lokale eingebettete Erfassung bietet diese
Schaltfläche an. Sie prüft nach dem Antippen, ob der Google Code Scanner
bereits vorhanden ist, und fordert weder Installation noch zusätzliche
Berechtigungen an. Bei fehlendem Scanner oder nach Abbruch stehen Barcode-
Eingabe und Fotoauswahl direkt daneben bereit.

Der erkannte Code steht anschließend lediglich im Eingabefeld. Das Scannen
speichert nichts und sucht nicht im Katalog. Erst **Privaten Entwurf anlegen**
sendet die Eingabe an die App. EAN-8, EAN-13 (einschließlich ISBN-13) und UPC-A
werden nur mit passender Prüfziffer übernommen. ISBN-10, weitere Formate oder
ein nicht erkennbarer Code können weiterhin wie bisher manuell eingegeben
werden, soweit die jeweilige Eingabe gültig ist. Die Fotoauswahl und die
separaten Barcode-Aktionen für Einkauf und die bisherige Inventarseite bleiben
unverändert. Nach Eingabeänderung, Verlassen/Neuladen der Seite oder Austausch
des Formulars kann ein früheres Ergebnis die aktuelle Eingabe nicht ersetzen.

## Lokale Prüfung / Offline verification

From the checkout root with the test extra and Node installed:

```sh
PYTHONPATH=.:tests python -m unittest -q \
  tests.test_inventory_intake_native_contract tests.test_inventory_mixed_intake \
  tests.test_inventory_intake_matches tests.test_frontend_route_contract \
  tests.test_template_accessibility
node --test tests/inventory_intake_native_scan.test.cjs
```

The signed-in Flask page and staged-draft endpoints are exercised using local
fixtures. The JS suite runs the shipped controller with real in-process
message ports and fixture DOM events, not an Android WebView. The Android
session unit plus platform-stub smoke check do not execute Play services.

With an existing working Playwright Chromium installation (do not install it
for the offline check), use the optional authenticated browser scenario:

```sh
SIMPLEOFFICE_INTAKE_BROWSER_REQUIRED=1 PYTHONPATH=.:tests \
  python -m unittest -v tests.test_inventory_intake_native_browser
```

It defaults to installed Playwright Chromium; set
`SIMPLEOFFICE_INTAKE_CHROMIUM` to an already installed executable if needed.
It uses an actual signed-in browser and a fake scanner port. The existing
Android APK build workflow separately checks the Java tests and bundles the
exact HTML/JS assets in its debug packages. That does not verify a physical
scan, browser permissions or Play services on a device.
