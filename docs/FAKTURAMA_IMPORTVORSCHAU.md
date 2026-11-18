# Fakturama-Rechnungen vor der Migration prüfen

Unter **Einstellungen → Integrationen & Dienste → Datenmigration** können
Administratoren einen vorbereiteten Fakturama-Rechnungsexport prüfen. Dort
steht eine leere CSV-Vorlage zum Download bereit. Die Vorschau zeigt die
Zuordnung der Quelldaten, Beträge und historischen Rechnungsadressen.
Der Upload und die Vorschau werden nicht in der Geschäftsablage gespeichert.
Im Audit stehen der technische Vorgang und sein Ergebnis.

## Quelle und Vorbereitung

Dieses Profil ist eine CSV-Projektion für SimpleOffice, Version 1. Es basiert
auf den Rechnungstabellen und Empfängerfeldern aus der Analyse des lokalen
Fakturama-2.2-Quellstands `768b78e6`. Die Analyse belegt die Felder und
Beziehungen. Das unterstützte CSV-Layout ist unten definiert. Native CSV-,
ODS- und Datenbanksicherungen benötigen eine vorherige Übertragung in dieses
Layout. Der Testbestand wurde synthetisch erstellt.

1. Eine lesbare Kopie des Fakturama-Bestands verwenden. In der installierten
   Version die Rechnungen aus dem Modell `Invoice` und dem zugehörigen
   Datensatz `Document` ermitteln. Die Quellanalyse benennt `FKT_INVOICE` und
   `FKT_DOCUMENT` als Tabellen für diese beiden Modelle.
2. Für jede Rechnung genau einen historischen Rechnungsempfänger aus
   `Document.receiver` beziehungsweise `FKT_DOCUMENTRECEIVER` in die Vorlage
   übertragen. Die Adressrolle in den Quelldaten prüfen. Aktuelle Anschriften
   aus dem Kontaktstamm dürfen einen historischen Empfänger nicht ersetzen.
3. Die IDs der Quellobjekte unverändert als Text übernehmen. Bei Zahlwerten
   Punkt oder Komma als Dezimaltrennzeichen verwenden und Tausendertrennzeichen
   entfernen. Für die Belegwährung den im Quellbestand dokumentierten
   dreistelligen Code eintragen, auch wenn die Währung für alle Zeilen gilt.
4. Die CSV als UTF-8 mit Semikolon speichern und hochladen. Ein optionales
   UTF-8-BOM wird erkannt. Die Datei darf bis zu 200 Belegzeilen und 256 KiB
   enthalten.

## Profil und Feldzuordnung

Die erste Zeile der heruntergeladenen Vorlage enthält das Profil. Ab der
zweiten Zeile folgen die Spaltenüberschriften und danach die Datenzeilen.

```csv
simpleoffice-fakturama-invoices;1;2.2.0
document_id;document_number;document_date;currency;total_value;receiver_id;buyer_name;buyer_street;buyer_postal_code;buyer_city;buyer_country;origin_contact_id;origin_address_id;source_document_id;invoice_reference_id;paid_value
```

| CSV-Spalten | Quelle und Bedeutung |
|---|---|
| `document_id`, `document_number`, `document_date` | ID des Rechnungsdatensatzes, ursprüngliche Belegnummer und `Document.documentDate` als `YYYY-MM-DD` |
| `currency`, `total_value`, `paid_value` | Quellwährung, `Document.totalValue` und optional `Document.paidValue`. Die Vorschau übernimmt die Beträge als `Decimal` mit maximal zwölf Stellen vor und vier nach dem Trennzeichen. Sie berechnet keine neuen Summen. |
| `receiver_id`, `buyer_name` | ID des `DocumentReceiver` und der als Rechnungsempfänger ausgewiesene Name aus dessen Snapshot, gegebenenfalls aus Firma und Namensfeldern zusammengesetzt |
| `buyer_street`, `buyer_postal_code`, `buyer_city`, `buyer_country` | `street`, `zip`, `city`, `countryCode` des historischen Empfängers. Land und Währung verwenden zweistellige bzw. dreistellige Codes in Großbuchstaben. Die Vorschau prüft deren Schreibweise. |
| `origin_contact_id`, `origin_address_id` | Optionale `DocumentReceiver.originContactId` und `originAddressId`, getrennt von der Snapshot-ID |
| `source_document_id`, `invoice_reference_id` | Optionale Quell-IDs aus `Document.sourceDocument` und `Document.invoiceReference`. Beide bleiben getrennt. Die Vorschau löst Verweise nicht auf. |

Die Spaltenreihenfolge ist frei. Zusätzliche Spaltennamen dürfen bis zu 64
ASCII-Zeichen aus Kleinbuchstaben, Ziffern und Unterstrichen enthalten und
beginnen mit einem Kleinbuchstaben. Insgesamt sind höchstens 40 Spalten und
240 Zeichen pro Feld vorgesehen. Fehlende Pflichtwerte und befüllte
unbekannte Spalten erscheinen als **Zuordnung offen**. Doppelte Dokument-IDs
aus lesbaren Zeilen werden pro Datei als **Abgelehnt** markiert.
Ungültige Datums- oder Zahlenwerte und Zeilen mit abweichender Spaltenzahl
werden ebenfalls abgelehnt. Syntaktisch ungültige Dateien und andere
Profilversionen werden als Ganzes zurückgewiesen. **Erkannt** bedeutet, dass
die Zeile alle für diese Vorschau vorgesehenen Felder zuordnen konnte.

Der Beispieldatensatz enthält je eine erkannte, eine unvollständige und eine
ungültige Zeile. [Synthetische CSV-Datei](../tests/fixtures/fakturama/invoices-v1.csv)
