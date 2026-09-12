# Inventar schneller mit Amazon und eBay ergänzen

## Ziel

Bei der mobilen Inventarerfassung sollen vorhandene Produktdaten genutzt werden können, statt Titel, Hersteller, Beschreibung und Preis jedes Mal vollständig von Hand einzutragen. Das gilt nicht nur für Bücher, sondern auch für CDs, DVDs, Werkzeuge, Elektrogeräte und andere Gegenstände mit ISBN, EAN/Barcode oder eindeutigem Namen.

Die Erfassungsseite bietet deshalb zwei zusätzliche Datenquellen:

- Amazon Product Advertising API 5.0
- eBay Browse API

Je Anbieter werden maximal die ersten drei Treffer geladen. Der erste Treffer wird in der Oberfläche als **Bester Treffer** hervorgehoben. Daten werden erst nach einem ausdrücklichen Klick auf **Daten übernehmen** in das Formular geschrieben.

## Warum nicht automatisch den ersten Treffer speichern?

EAN, ISBN und Produktnamen sind meist eindeutig genug für eine gute Sortierung, aber nicht immer für eine automatische Inventarentscheidung. Unterschiedliche Auflagen, Bundles, Größen oder gebrauchte Varianten können denselben oder einen sehr ähnlichen Titel haben.

Darum gilt:

1. suchen,
2. bis zu drei Treffer anzeigen,
3. ersten Treffer hervorheben,
4. Benutzer wählt den passenden Treffer,
5. erst dann werden Felder übernommen.

Damit bleibt die Schnellerfassung schnell, ohne falsche Stammdaten still zu speichern.

## Suchreihenfolge

Die Oberfläche verwendet als Suchbegriff bevorzugt:

1. eine gültige ISBN,
2. danach Barcode/EAN,
3. danach den Objektnamen.

Dadurch funktionieren Bücher und Medien besonders gut, während normale Gegenstände weiterhin über EAN oder Namen gesucht werden können.

## Übernommene Felder

Soweit der Anbieter sie liefert, können übernommen werden:

- Objektname/Titel
- Autor bzw. Mitwirkende
- Hersteller/Marke
- Modell
- Kategorie bzw. Bindung/Produkttyp
- Beschreibung/Merkmale
- ISBN
- Barcode/EAN
- Preis/Richtwert
- Währung
- Preisquelle
- Metadatenquelle

Bereits ausgefüllte wichtige Textfelder werden standardmäßig geschützt. Mit **Vorhandene Felder überschreiben** kann der Benutzer ausdrücklich erlauben, bestehende Werte zu ersetzen.

## Amazon konfigurieren

Amazon stellt Produktdaten nicht als frei nutzbare anonyme Such-API bereit. SimpleOffice4Me verwendet deshalb die offizielle Product Advertising API 5.0 und speichert deren Zugangsdaten nicht im Inventar oder im Browser.

Erforderliche Umgebungsvariablen:

```text
SIMPLEOFFICE_AMAZON_ACCESS_KEY
SIMPLEOFFICE_AMAZON_SECRET_KEY
SIMPLEOFFICE_AMAZON_PARTNER_TAG
```

Verwendet wird der deutsche Marketplace `www.amazon.de` über `webservices.amazon.de` und die Region `eu-west-1`.

Sind keine Amazon-Zugangsdaten konfiguriert, bleibt die Schaltfläche trotzdem sichtbar. Statt Produktdaten still zu erfinden oder HTML-Seiten zu scrapen, bietet SimpleOffice4Me dann einen normalen Amazon-Suchlink an.

## eBay konfigurieren

Für eBay wird die offizielle Browse API verwendet. Der Server bezieht dafür ein OAuth-App-Token und hält dieses nur kurzzeitig im Arbeitsspeicher.

Erforderliche Umgebungsvariablen:

```text
SIMPLEOFFICE_EBAY_CLIENT_ID
SIMPLEOFFICE_EBAY_CLIENT_SECRET
```

Optional:

```text
SIMPLEOFFICE_EBAY_MARKETPLACE_ID=EBAY_DE
```

Ohne konfigurierte eBay-App-Zugangsdaten wird ebenfalls auf die normale eBay-Suche zurückgefallen.

## Datenschutz und Sicherheit

Marketplace-Zugangsdaten bleiben ausschließlich serverseitig. Der Browser erhält weder Amazon-Secret-Key noch eBay-Client-Secret oder OAuth-Token.

Weitere Schutzmaßnahmen:

- HTTPS für beide offiziellen APIs
- begrenzte Antwortgröße
- kurze Netzwerk-Timeouts
- maximal drei Treffer pro Anfrage
- serverseitiges Rate-Limit pro Benutzer und Anbieter
- keine automatische Speicherung eines Suchtreffers
- externe Links werden im Browser auf Amazon-/eBay-Domains beschränkt
- Fehlerantworten enthalten keine Zugangsdaten

## Mobile Aktionsleiste

Auf Android-Geräten kann eine feste oder klebende Schaltflächenleiste von der Systemnavigation oder einer eingeblendeten Tastatur verdeckt werden. Die Inventarerfassung berücksichtigt deshalb:

- `env(safe-area-inset-bottom)`
- `window.visualViewport`
- zusätzliche Formular-Unterkante
- große, auf schmalen Bildschirmen untereinander angeordnete Aktionsbuttons

Dadurch bleiben **Ins Inventar übernehmen**, **Amazon Daten suchen** und **eBay Daten suchen** auch auf kleinen Android-Displays erreichbar.

## Warum offizielle APIs statt Scraping?

Direktes Auslesen der Amazon- oder eBay-Webseiten wäre technisch fragil: Seitenaufbau, Bot-Schutz und HTML-Struktur können sich jederzeit ändern. Die offiziellen APIs liefern strukturierte Daten, klar definierte Authentifizierung und stabile Felder. Der normale Suchlink bleibt als Fallback erhalten, wenn noch keine API-Zugangsdaten eingerichtet wurden.
