# Einheitliches Objektmodell

SimpleOffice4Me behandelt physische Gegenstände fachlich als **Objekte**. Inventar und Bibliothek sind keine getrennten Datenbestände, sondern spezialisierte Arbeitsabläufe über demselben `ObjectStore`.

## Gemeinsame Objektbasis

Jedes Buch, Regal, Werkzeug, Elektrogerät, Möbelstück, Fahrzeug, Ersatzteil oder andere Inventargut besitzt dieselbe Kernidentität:

- permanente Objekt-ID und numerische Anzeigenummer
- Name und Objekttyp
- Status
- Kennung / Barcode / ISBN / weitere Identifikatoren
- Standort
- Tags und freie Attribute
- Dokumentverknüpfungen und Notizen
- optional Rechnungs-/Artikelstammdaten

Ein **Buch** ist daher ein Objekt mit Buchattributen wie ISBN, Autor oder Verlag. Ein **Regal** ist ein Objekt vom Typ `shelf`, das zusätzlich von der Bibliothek als strukturierter Standort verwendet wird.

## Gemeinsame Objektakte

Querschnittsfunktionen gehören nicht einzelnen Fachmodulen, sondern zur Objektakte:

### Bilder und lokale Analyse

Bilder werden pro Objekt abgelegt. Sie können unmittelbar bei der Erfassung, später aus der Objektakte oder zusammen mit einer Zustandsaufnahme hinzugefügt werden.

Die Analyse bleibt lokal:

- Bildformat, Auflösung, Orientierung und einfache Qualitätsmerkmale über Pillow
- **ML-OCR über RapidOCR mit ONNX Runtime als Standard auf Desktop-Systemen**
- Tesseract nur noch als lokaler Fallback, wenn die ML-Engine fehlt, fehlschlägt oder keinen Text erkennt
- ML-Ergebnisse enthalten zusätzlich Textblöcke, Koordinaten und Konfidenzwerte
- erkannte Modell-/Seriennummern als Attributvorschläge
- erkannte Begriffe wie CE, DGUV, VDE, GS, RoHS oder WEEE als Hinweise und Such-Tags
- OCR-Ausschnitt und Schlüsselwörter werden im Objekt suchbar gemacht

Die ML-Modelle laufen lokal; Bilder oder erkannte Texte werden für die OCR nicht an externe Dienste übertragen. Die Desktop-Anwendung bündelt RapidOCR und ONNX Runtime als normale Python-Abhängigkeiten. Android verwendet weiterhin seinen eigenen plattformspezifischen Paket-/OCR-Weg und kann später dasselbe Ergebnisformat liefern.

Das gemeinsame OCR-Ergebnis ist unabhängig von der Engine aufgebaut und enthält mindestens:

- verwendete Engine (`rapidocr` oder Fallback `tesseract`)
- Status
- Volltext
- Anzahl erkannter Zeichen
- mittlere Konfidenz, sofern die Engine sie liefert
- erkannte Textblöcke mit Bounding-Boxen, sofern verfügbar
- Informationen über einen eventuell verwendeten Fallback

Damit kann die Oberfläche später erkannte Bereiche direkt auf dem Objektfoto markieren, ohne das Datenmodell erneut ändern zu müssen.

### Zustand und Alterungsverlauf

Ein Objekt kann beliebig viele Zustandsaufnahmen besitzen. Jede Aufnahme enthält:

- Datum
- Zustandsklasse
- Bewertung 1–5
- Freitext / Beobachtung
- optionales Zustandsfoto

Die Historie wird nicht überschrieben. Dadurch lassen sich Verschleiß, Schäden, Reparaturbedarf und allgemeine Alterung nachvollziehen.

### Prüfungen und Konformität

Konformitäts- und Prüfinformationen werden getrennt von der allgemeinen Zustandshistorie geführt. Typische Einträge sind beispielsweise:

- CE-Kennzeichnung / Konformitätsunterlagen
- DGUV Vorschrift 3
- VDE
- GS
- RoHS
- WEEE
- Leiter-/Trittprüfung
- Kalibrierung
- allgemeine Sichtprüfung

Ein Nachweis kann Referenz, Datum, Gültigkeit, Notiz, Dokument-ID und Foto/Prüfplakette enthalten.

**CE wird dabei als Kennzeichnung/Nachweis modelliert, nicht automatisch als wiederkehrende Prüfung.** Wo eine echte wiederkehrende Prüfung erforderlich oder gewünscht ist, kann aus demselben Dialog eine Fälligkeit mit Intervall angelegt werden. Diese nutzt die bestehende Aufgaben-/VTODO-Infrastruktur und erscheint damit in der normalen Aufgabenverwaltung.

Die Anwendung dokumentiert Nachweise und Termine; sie entscheidet nicht automatisch, welche gesetzliche oder normative Prüfung für einen konkreten Gegenstand verpflichtend ist.

## Inventar

Der Inventarbereich ist die schnelle mobile Erfassungsoberfläche für Objekte:

- Barcode / ISBN
- NFC
- Kamera
- Hersteller, Modell, Seriennummer
- Buchmetadaten
- Foto
- Prüfung / Wiedervorlage

Das erzeugte Inventargut ist unmittelbar ein `ObjectStore`-Objekt. Es existiert kein zweiter Inventarstamm.

## Bibliothek

Die Bibliothek ist eine Fachansicht auf Objekte:

- Bücher sind normale Objekte mit Buchattributen.
- Bibliotheksstandorte und Regale sind normale Objekte vom Typ `shelf`.
- Die Bibliotheksdaten halten nur Hierarchie, Barcode-Etikett, Zuordnung und Druckereinstellungen.
- Die Zuordnung eines Buchs oder anderen Objekts zu einem Regal wird in die allgemeinen Objektfelder gespiegelt und bleibt damit global suchbar.
- Bestehende, vor dieser Vereinheitlichung angelegte Regale können über „Bestehende Regale als Objekte abgleichen“ verlustfrei in den ObjectStore verknüpft werden.

## Verantwortlichkeiten

| Bereich | Kanonische Verantwortung |
| --- | --- |
| Identität, Stammdaten, Attribute, Tags, Dokumente | `ObjectStore` |
| Bilder, ML-OCR, Zustand, Konformität, Prüfverlauf | gemeinsame Objektakte / bestehender Inventory-Sidecar |
| Schnellerfassung, Barcode, NFC, Buch-Metadaten | Inventar-Workflow |
| Regalhierarchie, Zuordnung, Etikettendruck | Bibliotheks-Workflow |
| Fälligkeiten und Wiederholungen | VTODO / Aufgabenverwaltung |

Damit können weitere Fachansichten später dieselbe Objektbasis verwenden, ohne neue parallele Stammdaten einzuführen.
