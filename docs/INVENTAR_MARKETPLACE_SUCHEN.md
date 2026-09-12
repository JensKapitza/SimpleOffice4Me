# Inventar schneller mit Amazon und eBay ergänzen

## Ziel

Bei der mobilen Inventarerfassung sollen vorhandene Produktdaten genutzt werden können, statt Titel, Hersteller, Beschreibung und Preis jedes Mal vollständig von Hand einzutragen. Das gilt für Bücher, CDs, DVDs, Werkzeuge, Elektrogeräte und andere Gegenstände mit ISBN, EAN/Barcode oder eindeutigem Namen.

Die Amazon-/eBay-Suche wird ausschließlich durch einen ausdrücklichen Benutzerklick gestartet. Es gibt keinen Hintergrundcrawler und keine periodische Massenabfrage.

## Standardpfad ohne API-Schlüssel

Für den normalen Betrieb sind keine Amazon- oder eBay-API-Schlüssel erforderlich.

Nach einem Klick auf **Amazon Daten suchen** oder **eBay Daten suchen** ruft SimpleOffice4Me genau eine öffentliche Suchergebnisseite des gewählten deutschen Marketplace ab und liest daraus maximal drei sichtbare Treffer. Unterstützt werden insbesondere Titel, Ergebnislink und soweit vorhanden Preis/Währung.

Die Abfrage hat feste Grenzen:

- nur `https://www.amazon.de` bzw. `https://www.ebay.de`
- keine vom Benutzer vorgegebenen Zielhosts
- 8 Sekunden Netzwerk-Timeout
- maximal 2 MiB Antwortgröße
- maximal drei Treffer
- bestehendes serverseitiges Rate-Limit pro Benutzer/Anbieter
- keine Hintergrundschleife
- kein Umgehen von CAPTCHA-, Robot- oder Schutzseiten
- keine automatischen Redirect-Ketten auf fremde Hosts

Wenn eine Seite nicht auslesbar ist oder der Anbieter den direkten Abruf blockiert, bleibt der normale Amazon-/eBay-Suchlink als Fallback verfügbar.

## Optionale offizielle APIs

Sind bereits Zugangsdaten vorhanden, bleiben die offiziellen APIs als strukturierter Fallback nutzbar:

- Amazon Product Advertising API 5.0
- eBay Browse API

Der Ablauf ist damit:

1. Benutzer startet die Suche.
2. Öffentliche Marketplace-Suche wird einmalig abgefragt.
3. Sind daraus keine Treffer auslesbar und eine offizielle API ist konfiguriert, wird die API als Fallback verwendet.
4. Maximal drei Treffer werden angezeigt.
5. Erst ein ausdrücklicher Klick auf **Daten übernehmen** füllt das Formular.

## Suchreihenfolge

Die Oberfläche verwendet als Suchbegriff bevorzugt:

1. eine gültige ISBN,
2. danach Barcode/EAN,
3. danach den Objektnamen.

Damit wird bei Büchern möglichst die konkrete Ausgabe gesucht statt nur ein allgemeiner Werktitel.

## Titel und bewusst ausgewählte Treffer

Ein Marketplace-Treffer wird ausdrücklich vom Benutzer ausgewählt. Deshalb ist dessen Titel die Identität des ausgewählten Produkts und darf einen zuvor aus einer anderen Metadatenquelle geladenen Titel ersetzen.

Das behebt insbesondere Fälle, in denen eine ISBN-Datenquelle den Originaltitel eines Werkes liefert, während Amazon/eBay für die konkrete deutsche Ausgabe den deutschen Titel anzeigen.

Andere bereits gepflegte Felder behalten weiterhin die bestehende Überschreibschutz-Logik, sofern sie nicht ausdrücklich ersetzt werden.

## Übernommene Felder

Soweit die jeweilige Quelle sie liefert, können übernommen werden:

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

Öffentliche Suchseiten liefern meist weniger strukturierte Felder als die offiziellen APIs. Fehlende Werte werden nicht erfunden.

## Optionale Amazon-API-Konfiguration

```text
SIMPLEOFFICE_AMAZON_ACCESS_KEY
SIMPLEOFFICE_AMAZON_SECRET_KEY
SIMPLEOFFICE_AMAZON_PARTNER_TAG
```

Verwendet wird der deutsche Marketplace. API-Zugangsdaten bleiben ausschließlich serverseitig.

## Optionale eBay-API-Konfiguration

```text
SIMPLEOFFICE_EBAY_CLIENT_ID
SIMPLEOFFICE_EBAY_CLIENT_SECRET
```

Optional:

```text
SIMPLEOFFICE_EBAY_MARKETPLACE_ID=EBAY_DE
```

Der OAuth-App-Token verbleibt serverseitig und wird nur kurzzeitig im Arbeitsspeicher gehalten.

## Datenschutz und Sicherheit

- keine Marketplace-Zugangsdaten im Browser oder Inventar
- HTTPS-only für Marketplace-Aufrufe
- feste Host-Allowlist
- Antwortgrößen- und Timeout-Limits
- Rate-Limit pro Benutzer und Anbieter
- keine automatische Speicherung eines Suchtreffers
- externe Ergebnislinks auf Amazon-/eBay-Domains beschränkt
- keine CAPTCHA-/Anti-Bot-Umgehung
- Fehlerantworten enthalten keine Zugangsdaten

## Mobile Aktionsleiste

Die Inventarerfassung berücksichtigt auf Android weiterhin:

- `env(safe-area-inset-bottom)`
- `window.visualViewport`
- zusätzliche Formular-Unterkante
- große, auf schmalen Bildschirmen untereinander angeordnete Aktionsbuttons

Dadurch bleiben **Ins Inventar übernehmen**, **Amazon Daten suchen** und **eBay Daten suchen** auch auf kleinen Android-Displays erreichbar.

## Wartbarkeit

HTML-Strukturen von Marketplace-Seiten können sich ändern. Die Parser sind deshalb bewusst klein und isoliert und durch Regressionstests mit repräsentativen Suchergebnissen abgesichert. Wenn Amazon/eBay ihr Markup ändern, fällt die Anwendung auf den normalen Suchlink bzw. eine konfigurierte offizielle API zurück, statt falsche Daten zu erzeugen.
