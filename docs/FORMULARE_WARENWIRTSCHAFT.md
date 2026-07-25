# Formulare und Warenwirtschaft

## Grundsatz

SimpleOffice4Me verwaltet fachliche Daten als **Formulare mit Datensätzen**.
Ein Formular besteht aus Kennung, Feldern, Feldtypen, Pflichtregeln,
Auswahlwerten und optionalen Beziehungen. Die Oberfläche rendert die Eingabe-
und Bearbeitungsmaske aus dieser Definition. Neue Masken werden somit als
Formularvorlage ergänzt, nicht als fest programmierte Einzelseite.

Die erste Grundlage liefert `contact`, `product` und `invoice`. Eine Rechnung
ist damit ein Formular mit speziellen Feldern und Statuswerten, keine Ausnahme
im Datenmodell.

## Datenmodell

```text
Formularvorlage 1 ── * Formular-Datensatz
Formular-Datensatz * ── 1 anderer Formular-Datensatz (Beziehungsfeld)
Formular-Datensatz 1 ── * Historienereignis
```

- Formularvorlage: Felddefinition und Maskenbeschreibung.
- Datensatz: eingegebene Werte, Ersteller und Änderungszeitpunkt.
- Historie: jede Anlage und Änderung wird als Snapshot erfasst.
- Beziehung: ein `relation`-Feld speichert die Kennung eines Datensatzes einer
  anderen Vorlage, z. B. Rechnung → Kunde oder Produkt → Lieferant.

## Standardfeldtypen

`text`, `textarea`, `email`, `date`, `number`, `currency`, `select`,
`relation`.

Weitere Feldtypen wie Positionen, Datei, Bild, Adressblock, Formel, Steuer und
Nummernkreis werden als Erweiterungen derselben Definition ergänzt.

## Abbildung der Fachanforderungen

| Bereich | Formular bzw. Erweiterung |
|---|---|
| Kunden, Lieferanten, Ansprechpartner | `contact`, ergänzt um Kategorie, Bank-, Steuer- und Freigabefelder |
| Produkte, Varianten, Preise, Lager | `product`, ergänzt um Varianten- und Lagerbewegungsfelder |
| Angebot, Auftrag, Lieferschein, Gutschrift, Mahnung | weitere Vorlagen mit gemeinsamen Feldern und Prozessbeziehung |
| Rechnung | `invoice` mit Kunde, Nummer, Datum, Status, Fälligkeit und Beträgen |
| Positionen, Rabatte, Versand, Steuern | wiederholbare Unterformulare beziehungsweise berechnete Felder |
| PDF, E-Mail, E-Rechnung, QR-Code | Ausgaben/Aktionen einer Formularvorlage, nicht eigene Datenobjekte |

## Prozessregeln

1. Historische Geschäftsdokumente werden nach Freigabe als Snapshot gesperrt.
2. Ein Dokumentfluss ist über Beziehungsfelder nachvollziehbar, etwa Angebot →
   Auftrag → Lieferschein → Rechnung.
3. Lagerbewegungen und offene Beträge entstehen aus freigegebenen Formularen.
4. Kategorien, Steuern, Versandarten und Textbausteine werden ebenfalls als
   konfigurierbare Formulare geführt.

## Nächste Ausbaustufen

1. Wiederholbare Positions-Unterformulare und Berechnung von Rabatt, Steuer und
   Summen.
2. Nummernkreise, Statusübergänge und unveränderliche Freigabe-Snapshots.
3. PDF-/E-Rechnungs-Ausgabe, Versand, Zahlung und Lagerbewegung.
4. CSV/ODS/vCard-Import und -Export sowie Webshop- und Paketdienstadapter.
