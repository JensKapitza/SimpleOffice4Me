# Scanner: externe Katalogbelege / External catalog evidence

Der Katalogaufruf ist eine optionale, ausdrücklich ausgelöste Suche. An die
jeweilige Quelle geht nur der normalisierte Barcode, keine Fotos, OCR-Texte,
Titel aus dem Entwurf, lokalen Notizen oder Kontodaten. Der Katalogdienst
liefert Vorschläge, speichert aber keinen Entwurf und kein Inventarobjekt.
Katalogfelder behalten ihre Quellenkennungen auch bei identischem Text.

- **Open Library:** Die dokumentierte [Books API](https://openlibrary.org/dev/docs/api/books)
  kann ohne Registrierung zu einer ISBN Daten liefern. Ein Antwortschlüssel,
  der die Anfrage nur wiederholt, genügt nicht: Im Editionsdatensatz muss eine
  passende ISBN stehen. Open Library allein ist ein prüfbarer Vorschlag.
- **Google Books:** Administrierende können bewusst einen auf die
  [Google Books API](https://developers.google.com/books/docs/v1/using)
  beschränkten Anwendungsschlüssel als `SIMPLEOFFICE_GOOGLE_BOOKS_API_KEY` setzen.
  Keine OAuth-Anmeldung, keine Kalenderdaten. Ohne diesen Schlüssel wird Google
  nicht aufgerufen. Hinterlege den Schlüssel in der Serverumgebung (bei
  Docker/Compose als Umgebungsvariable des SimpleOffice-Containers) und starte
  die Anwendung mit der aktualisierten Umgebung neu. In der Stapelerfassung
  stehen dann Open Library und Google Books vor der Zustimmung. Die App zeigt
  den Schlüssel nicht an. Nur ausdrücklich angegebene passende ISBNs zählen.
- **Buchtitel und Ausgabe:** Eine Empfehlung braucht zwei unabhängige Quellen
  mit derselben ISBN und demselben normalisierten Titel sowie übereinstimmenden
  Ausgabenmerkmalen (Verlag und Jahr; abweichende vorhandene Angaben führen zur
  Prüfung). Ein optional lokal beobachteter Titel wird nur im Prozess verglichen.
  Teilauskünfte, alternative Ausgaben und Fehler bleiben sichtbar. Keine
  stillschweigende Übernahme oder Speicherung.
- **Andere EAN/GTIN:** EAN-8/-13, UPC-A und GTIN-14 werden mit Prüfziffer validiert.
  Ein Produktadapter muss den exakten Code im Ergebnis melden; Variante, Größe
  und Einheit werden ausdrücklich abgeglichen, nicht aus einem ähnlichen Titel
  geraten. Ohne für diesen Zweck freigegebenen Anbieter bleibt manuelle Eingabe.
  Die vorhandene Angebots-/Stichwortsuche wird nicht aufgerufen. Ein App-Key für
  [eBay Browse](https://developer.ebay.com/develop/api/buy/browse_api) allein
  belegt keine Freigabe für diesen konkreten Inventarzweck
  ([API License](https://developer.ebay.com/develop/api-license-agreement)).
  Kein Amazon-Adapter, kein Preisimport, kein Seiten-Scraping.

Offline, bei API-Ausfall oder bei Widerspruch bleiben Code und manuell
eingetragene Daten verfügbar. Ein begrenzter lokaler Arbeitsspeicher-Cache
(128 normalisierte Buchantworten bis zehn Minuten) enthält keine Nutzereingaben
oder Schlüssel im Klartext. Vorhandene Alt-Lookups bleiben unverändert.

**English:** Explicit opt-in lookups send only a validated public identifier.
Independent edition-specific evidence can be suggested, not silently applied.
Missing or conflicting evidence falls back to manual review. Google Books
requires a separately configured app key; non-book GTINs support only explicitly
approved product adapters. No raw photos, local text, credentials from other
integrations, marketplace scraping or prices are used.
