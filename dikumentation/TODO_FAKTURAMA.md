# TODO: Fakturama-Kompatibilität / Migration

Quelle dieser Aufgabenliste: `dikumentation/fakturamer` (technische Vollanalyse von Fakturama 2).

## Status

**Fakturama-Kompatibilität ist derzeit nicht umgesetzt.**

Vorhandene Funktionen in SimpleOffice4Me, die fachlich ähnlich aussehen (Kontakte, Rechnungen, Belege, Dokumente usw.), gelten **nicht automatisch als Fakturama-Implementierung**. Eine Aufgabe darf erst als erledigt markiert werden, wenn die jeweilige Fakturama-Semantik, Datenübernahme und die notwendigen Regressionstests ausdrücklich umgesetzt und geprüft wurden.

## Ziel

SimpleOffice4Me soll Fakturama-Daten möglichst vollständig übernehmen und die für eine Migration relevanten Arbeitsabläufe so abbilden, dass vorhandene Fakturama-Bestände ohne stillen Informationsverlust weiterverwendet werden können.

Dabei gilt:

- keine Änderung historischer Geschäftsdokumente durch spätere Stammdatenänderungen,
- Geldbeträge ausschließlich mit deterministischer Dezimalarithmetik und definierten Rundungsregeln,
- vollständige Herkunfts- und Migrationsprotokollierung,
- keine endgültige Dokumentnummer für Entwürfe,
- transaktionssichere Vergabe endgültiger Nummern,
- importierte Beziehungen und Dokumentketten müssen erhalten bleiben,
- Originaldaten müssen bei Importfehlern unverändert bleiben.

---

## 1. Fakturama-Importer

- [ ] Importformat und unterstützte Fakturama-Versionen festlegen.
- [ ] Direkten Import einer Fakturama-HSQLDB untersuchen und implementieren.
- [ ] Import aus MariaDB/MySQL-basierten Fakturama-Installationen unterstützen.
- [ ] Alternativen über vorhandene CSV-/XML-/Exportdateien unterstützen.
- [ ] Schema-/Versionsdetektion vor dem Import.
- [ ] Import zuerst als Vorschau/Dry-Run ausführen können.
- [ ] Importbericht mit Anzahl erfolgreich, übersprungen, korrigiert und fehlerhaft übernommener Datensätze.
- [ ] Wiederholbaren/idempotenten Import ermöglichen.
- [ ] Stabile Zuordnung `Fakturama-ID -> SimpleOffice-ID` speichern.
- [ ] Herkunft jedes importierten Objekts mit Quelle, alter ID, Importzeitpunkt und Quellversion protokollieren.
- [ ] Konfliktstrategie bei bereits existierenden Kontakten, Produkten und Dokumenten implementieren.
- [ ] Import niemals anhand von Anzeigenamen allein zusammenführen.
- [ ] Zeichensätze, Umlaute und Sonderzeichen aus Altbeständen testen.
- [ ] Datei-/Bild-/PDF-Pfade aus Fakturama vor jedem File-I/O normalisieren und gegen erlaubte Import-Roots prüfen.
- [ ] Keine Symlink-/Traversal-Ausbrüche beim Import externer Fakturama-Dateien zulassen.

## 2. Kontakte und Adressen

- [ ] Debitor-/Kreditorrollen übernehmen.
- [ ] Kunden- und Lieferantennummern übernehmen.
- [ ] Anrede, Titel, Vorname, Nachname und Firma abbilden.
- [ ] Mehrere Anschriften pro Kontakt übernehmen.
- [ ] Rechnungs- und Lieferadressen semantisch unterscheiden.
- [ ] Telefon, Mobiltelefon, Fax, E-Mail und Internetadresse übernehmen.
- [ ] USt-IdNr. und weitere Steuerdaten übernehmen.
- [ ] Bankdaten übernehmen.
- [ ] bevorzugte Zahlungsart übernehmen.
- [ ] kundenspezifischen Rabatt übernehmen.
- [ ] Zahlungsziel übernehmen.
- [ ] Kontaktkategorien übernehmen.
- [ ] Notizen übernehmen.
- [ ] alternative/verknüpfte Kontakte übernehmen.
- [ ] Zeitstempel und Soft-Delete-Status soweit sinnvoll erhalten.
- [ ] Empfänger-Snapshots historischer Geschäftsdokumente getrennt vom aktuellen Kontaktstamm speichern.

## 3. Produkte und Lager

- [ ] Artikelnummer übernehmen.
- [ ] Bezeichnung und Beschreibung übernehmen.
- [ ] Verkaufs- und Einkaufspreise übernehmen.
- [ ] Steuerzuordnung übernehmen.
- [ ] Mengeneinheiten übernehmen.
- [ ] Kategorien übernehmen.
- [ ] Lagerbestand übernehmen.
- [ ] Gewicht übernehmen.
- [ ] Lieferantenbezüge übernehmen.
- [ ] Webshopbezüge übernehmen.
- [ ] Produktbilder sicher übernehmen.
- [ ] Optionen/Varianten abbilden.
- [ ] Staffelpreise (`ProductBlockPrice`) übernehmen.
- [ ] Bestandsänderungsregeln der Dokumenttypen analysieren und abbilden.
- [ ] Historische Dokumentpositionen als Snapshot behandeln und nicht nachträglich aus aktuellen Produktdaten neu berechnen.

## 4. Steuern

- [ ] Fakturama-Steuersätze importieren.
- [ ] Name, Beschreibung und Prozentwert übernehmen.
- [ ] Steuerkategorien übernehmen.
- [ ] UNTDID-5305-Steuercodes übernehmen.
- [ ] steuerfreie und abweichende Steuerfälle abbilden.
- [ ] Versandsteuer-Regeln übernehmen.
- [ ] Netto-/Bruttomodus je Dokument übernehmen.
- [ ] Steuersummen je Steuersatz reproduzierbar berechnen.
- [ ] Rundungsverhalten gegen Fakturama-Testfälle verifizieren.

## 5. Zahlungsarten

- [ ] Zahlungsarten importieren.
- [ ] Zahlungsziel übernehmen.
- [ ] Skonto- und Zahlungstexte übernehmen.
- [ ] Kategorien übernehmen.
- [ ] externe/standardisierte Codes übernehmen.
- [ ] UNTDID-4461-Zahlungscodes für E-Rechnungen abbilden.
- [ ] Fälligkeit und Bezahlt-Status aus importierten Dokumenten korrekt rekonstruieren.

## 6. Versandarten

- [ ] Versandarten importieren.
- [ ] Versandpreis übernehmen.
- [ ] Steuerzuordnung übernehmen.
- [ ] Kategorien übernehmen.
- [ ] automatische Versandsteuer aus Dokumentpositionen unterstützen, wenn Fakturama dies so gespeichert/verwendet hat.

## 7. Textbausteine

- [ ] Fakturama-Textbausteine importieren.
- [ ] Kategorien übernehmen.
- [ ] Wiederverwendbare Einleitungs-, Schluss- und Zusatztexte abbilden.
- [ ] Zuordnung zu importierten Geschäftsdokumenten erhalten.

## 8. Geschäftsdokumente

Die Fakturama-Dokumenttypen dürfen nicht als voneinander unabhängige Dateninseln importiert werden. Gemeinsame Felder und Dokumentbeziehungen müssen erhalten bleiben.

- [ ] Brief importieren.
- [ ] Angebot importieren.
- [ ] Auftrag importieren.
- [ ] Auftragsbestätigung importieren.
- [ ] Rechnung importieren.
- [ ] Lieferschein importieren.
- [ ] Gutschrift importieren.
- [ ] Mahnung importieren.
- [ ] Pro-forma-Rechnung importieren.

Für alle relevanten Typen:

- [ ] Dokumentnummer übernehmen.
- [ ] Dokumentdatum übernehmen.
- [ ] Liefer-/Leistungsdatum übernehmen.
- [ ] Leistungszeiträume übernehmen.
- [ ] Kunden-/Empfänger-Snapshot übernehmen.
- [ ] Rechnungsanschrift übernehmen.
- [ ] Lieferanschrift übernehmen.
- [ ] Positionen und Reihenfolge übernehmen.
- [ ] Positionsrabatte übernehmen.
- [ ] Gesamtrabatt übernehmen.
- [ ] Netto-/Bruttomodus übernehmen.
- [ ] Versandart und Versandwert übernehmen.
- [ ] Zahlungsart übernehmen.
- [ ] Fälligkeit übernehmen.
- [ ] Anzahlungen übernehmen.
- [ ] bezahlten Betrag übernehmen.
- [ ] Bezahlt-Status und Zahldatum übernehmen.
- [ ] Einleitungs-, Schluss- und Zusatztexte übernehmen.
- [ ] Kundenreferenz übernehmen.
- [ ] Transaktions-ID übernehmen.
- [ ] Webshop-ID übernehmen.
- [ ] Quelldokument übernehmen.
- [ ] Rechnungsreferenz separat übernehmen.
- [ ] vorhandene Dokumentkette rekonstruieren.
- [ ] Druckstatus und vorhandene Ausgabeinformationen übernehmen, soweit sinnvoll.
- [ ] vorhandene PDF-Dateien sicher verknüpfen/importieren.
- [ ] E-Rechnungsinformationen erhalten.
- [ ] Audit-/Versions-/Soft-Delete-Daten soweit möglich erhalten.

## 9. Dokumentpositionen

- [ ] Produktpositionen unterstützen.
- [ ] freie Positionen unterstützen.
- [ ] Textpositionen unterstützen.
- [ ] Zwischensummen unterstützen.
- [ ] weitere Fakturama-`ItemType`-Varianten prüfen und abbilden.
- [ ] Positionsnummer/Reihenfolge erhalten.
- [ ] Artikelnummer, Name und Beschreibung als historischen Snapshot erhalten.
- [ ] Menge und Einheit übernehmen.
- [ ] Einzelpreis übernehmen.
- [ ] Rabatt übernehmen.
- [ ] Steuerobjekt und Steuersatz übernehmen.
- [ ] Netto-/Bruttobeträge verifizieren.
- [ ] optionale Positionen übernehmen.
- [ ] Produktreferenz nur zusätzlich zum Snapshot speichern.
- [ ] Gewichts-/Konteninformationen übernehmen, soweit vorhanden.

## 10. Fakturama-kompatible Berechnung

Eine importierte Rechnung muss bei gleicher Datenbasis dieselben fachlichen Summen ergeben wie Fakturama.

- [ ] zentrale Decimal/Money-Berechnungsschicht definieren.
- [ ] Menge x Einzelpreis.
- [ ] Positionsrabatt.
- [ ] definierte Zwischenrundung.
- [ ] Netto-/Steuerrückrechnung bei Bruttodokumenten.
- [ ] Gruppierung nach Steuersatz.
- [ ] Dokumentrabatt.
- [ ] Versand netto/brutto.
- [ ] Versandsteuer.
- [ ] negatives Vorzeichen für Gutschriften.
- [ ] Anzahlungen berücksichtigen.
- [ ] bereits gezahlte Beträge berücksichtigen.
- [ ] offenen Betrag bilden.
- [ ] Steuersummen bilden.
- [ ] Gesamtbetrag bilden.
- [ ] Golden-Master-Tests mit Fakturama-Beispieldokumenten erstellen.

## 11. Nummernkreise

- [ ] Fakturama-Nummernkreisdefinitionen importieren.
- [ ] Präfixe übernehmen.
- [ ] laufende Nummern übernehmen.
- [ ] Formatmuster übernehmen.
- [ ] getrennte Nummernkreise je Dokument-/Stammdatentyp ermöglichen.
- [ ] Entwürfe ohne endgültige Nummer behandeln.
- [ ] endgültige Nummern erst bei definierter Finalisierung verbrauchen.
- [ ] parallele Finalisierung transaktionssicher machen.
- [ ] vorhandene Fakturama-Nummern niemals beim Import neu vergeben.
- [ ] prüfen, ob nächste Nummer nach Migration korrekt fortgesetzt wird.

## 12. Dokumentumwandlung und Dokumentketten

- [ ] Angebot -> Auftrag unterstützen.
- [ ] Angebot/Auftrag -> Auftragsbestätigung unterstützen.
- [ ] Auftrag -> Lieferschein unterstützen.
- [ ] Auftrag/Lieferschein -> Rechnung unterstützen.
- [ ] Rechnung -> Mahnung unterstützen.
- [ ] Rechnung -> Gutschrift unterstützen.
- [ ] Pro-forma-Abläufe prüfen.
- [ ] Quellbezug und Rechnungsreferenz in getrennten Feldern erhalten.
- [ ] Positionen, Empfänger-Snapshot, Preise und Texte bei Umwandlung korrekt übernehmen.
- [ ] Dokumenthistorie der Umwandlungen auditierbar machen.

## 13. Statusaktionen

- [ ] Fakturama-Statuswerte auf SimpleOffice-Zustände abbilden.
- [ ] Bezahlt-Markierung übernehmen.
- [ ] Auftragsstatus übernehmen.
- [ ] Versand-/Bearbeitungsstatus übernehmen.
- [ ] Lagerbestandsänderungen reproduzieren bzw. beim Import nicht doppelt buchen.
- [ ] Soft Delete korrekt übernehmen.

## 14. Einnahmen- und Ausgabebelege

- [ ] Fakturama-`Voucher` importieren.
- [ ] `VoucherItem` importieren.
- [ ] Einnahme- und Ausgabebelege unterscheiden.
- [ ] Datum übernehmen.
- [ ] Belegnummer übernehmen.
- [ ] Lieferant/Empfänger übernehmen.
- [ ] Kategorien übernehmen.
- [ ] Brutto-/Nettowerte übernehmen.
- [ ] Steuerwerte übernehmen.
- [ ] Zahlungsinformationen übernehmen.
- [ ] mehrere Buchungspositionen je Beleg übernehmen.
- [ ] Kontenart je Position übernehmen.
- [ ] Steuersatz je Position übernehmen.
- [ ] Summierung nach Konten und Steuer reproduzieren.
- [ ] Unbezahlt-Status übernehmen.
- [ ] Dokument-/Dateianhänge mit Belegen verknüpfen.

## 15. Kontenarten / vorbereitende Buchhaltung

- [ ] `ItemAccountType` abbilden.
- [ ] `ItemListTypeCategory` abbilden.
- [ ] vorhandene Fakturama-Konten/Kategorien importieren.
- [ ] Buchungsexporte fachlich vergleichen.
- [ ] Einnahmen-/Ausgabenexport bereitstellen.
- [ ] Steuer-/Kontensummen exportieren.
- [ ] klar dokumentieren, dass daraus nicht automatisch eine vollständige doppelte Buchführung entsteht.

## 16. Druckvorlagen und Dokumentausgabe

- [ ] vorhandene Fakturama-ODT-Vorlagen erkennen/importieren.
- [ ] entscheiden, ob ODT-Vorlagen direkt unterstützt oder in ein SimpleOffice-Templateformat migriert werden.
- [ ] Fakturama-Platzhalter analysieren und konvertieren.
- [ ] Briefpapier-/Layoutdaten übernehmen.
- [ ] PDF-Ausgabe mit importierten historischen Dokumenten vergleichen.
- [ ] vorhandene ODT-/PDF-Pfade nicht ungeprüft verwenden.
- [ ] E-Rechnungs-Nachbearbeitung in die Ausgabe-Pipeline integrieren.

## 17. E-Rechnung

- [ ] vorhandene Fakturama-E-Rechnungsdaten erkennen.
- [ ] ZUGFeRD/XRechnung-relevante Felder mappen.
- [ ] UNTDID-5305-Steuerkategorien berücksichtigen.
- [ ] UNTDID-4461-Zahlungsarten berücksichtigen.
- [ ] Verkäufer-/Käuferdaten vollständig übernehmen.
- [ ] Rechnungsreferenzen übernehmen.
- [ ] Zahlungsinformationen übernehmen.
- [ ] vorhandene E-Rechnungsdateien importieren und validieren.
- [ ] neu erzeugte E-Rechnungen mit EN16931-Regeln testen.

## 18. E-Mail

- [ ] Fakturama-Mailkonfiguration nur nach ausdrücklicher Auswahl migrieren.
- [ ] Passwörter niemals als Klartext übernehmen oder speichern.
- [ ] SMTP-Einstellungen abbilden.
- [ ] Dokumentversand per E-Mail abbilden.
- [ ] vorhandene E-Mail-Texte/Vorlagen übernehmen, soweit vorhanden.
- [ ] Versandereignisse in SimpleOffice protokollieren.

## 19. CSV-/Dateiimporte

- [ ] Fakturama-Kontaktimport analysieren und kompatible Feldzuordnung anbieten.
- [ ] Produktimport analysieren.
- [ ] Ausgabenimport analysieren.
- [ ] Kontist-CSV-Import abbilden oder als separates kompatibles Importprofil anbieten.
- [ ] Spaltenmapping mit Vorschau und Fehlerbericht bereitstellen.
- [ ] Importprofile speicherbar machen.

## 20. Exporte

- [ ] Kontakt-Export.
- [ ] Produkt-Export.
- [ ] Dokumentlisten-Export.
- [ ] Einnahmen-Export.
- [ ] Ausgaben-Export.
- [ ] Buchungslisten-Export.
- [ ] Steuer-/Statistikexporte aus der Fakturama-Analyse einzeln erfassen und priorisieren.
- [ ] CSV-Zeichensatz, Trennzeichen, Dezimalformat und Datumsformat konfigurierbar machen.

## 21. Webshop-Integration

- [ ] Fakturama-Webshopmodell und verwendete HTTP-Schnittstellen vollständig erfassen.
- [ ] Webshop-Kundenimport prüfen.
- [ ] Webshop-Bestellimport prüfen.
- [ ] Produkt-/Bestandsbezug prüfen.
- [ ] Webshopstatus-Synchronisation prüfen.
- [ ] externe IDs dauerhaft speichern.
- [ ] Wiederholungsimporte idempotent machen.
- [ ] HTTP-Ziele mit SSRF-Schutz und erlaubten Hosts absichern.

## 22. Suche, Listen und Filter

- [ ] importierte Fakturama-Kategorien in SimpleOffice-Suche verfügbar machen.
- [ ] Dokumenttypfilter bereitstellen.
- [ ] Bezahlt/Unbezahlt-Filter bereitstellen.
- [ ] zeitliche Filter bereitstellen.
- [ ] Kunden-/Lieferantenfilter bereitstellen.
- [ ] Produkt-/Dokumentsuche über alte Fakturama-IDs ermöglichen.

## 23. Altdatenmigration und Nachvollziehbarkeit

- [ ] jeden Importlauf mit eindeutiger Import-ID protokollieren.
- [ ] Quellsystem `Fakturama` und Quellversion speichern.
- [ ] originale Primärschlüssel soweit möglich als Migrationsmetadaten erhalten.
- [ ] unveränderten Quellwert bei Konvertierungen auditierbar halten.
- [ ] Warnungen bei nicht abbildbaren Feldern erzeugen.
- [ ] keine Daten still verwerfen.
- [ ] Importbericht dauerhaft speichern.
- [ ] Rollback-/Storno-Strategie für fehlgeschlagene Importläufe definieren.

## 24. Validierung gegen echte Fakturama-Daten

- [ ] anonymisierten Fakturama-Testbestand erstellen.
- [ ] Kontakte mit mehreren Adressen testen.
- [ ] Produkte mit Staffelpreisen testen.
- [ ] Netto- und Bruttorechnung testen.
- [ ] mehrere Steuersätze in einer Rechnung testen.
- [ ] Versandsteuer testen.
- [ ] Rabatt auf Position und Gesamtdokument testen.
- [ ] Anzahlung testen.
- [ ] Teilzahlung testen.
- [ ] Gutschrift testen.
- [ ] Mahnung testen.
- [ ] Dokumentkette testen.
- [ ] Lageränderungen testen.
- [ ] Einnahmen-/Ausgabebelege testen.
- [ ] E-Rechnung testen.
- [ ] Sonderzeichen und sehr lange Texte testen.
- [ ] fehlende verknüpfte Dateien testen.
- [ ] beschädigte Datenbank-/Exportdaten kontrolliert ablehnen.

## 25. UI für Migration

- [ ] Administration -> Datenmigration -> Fakturama vorsehen.
- [ ] Quelle auswählen.
- [ ] Verbindung/Dateien testen.
- [ ] erkannte Fakturama-Version anzeigen.
- [ ] zu importierende Bereiche auswählbar machen.
- [ ] Dry-Run anbieten.
- [ ] Konflikte vor Import anzeigen.
- [ ] Fortschritt und Fehler anzeigen.
- [ ] Abschlussbericht anzeigen.
- [ ] direkten Sprung zu importierten Kontakten, Produkten, Rechnungen und Belegen ermöglichen.

## Priorität

### P0 – Voraussetzung für eine ernsthafte Fakturama-Migration

- [ ] Importer + Versions-/Schemaerkennung
- [ ] stabile ID-Zuordnung und Audit-Protokoll
- [ ] Kontakte/Adressen
- [ ] Produkte/Steuern/Zahlungsarten
- [ ] Rechnungen, Gutschriften und Positionen
- [ ] Fakturama-kompatible Geld-/Rundungsberechnung
- [ ] Nummernkreise
- [ ] Einnahmen-/Ausgabebelege
- [ ] sichere Übernahme vorhandener Dateien/PDFs
- [ ] Golden-Master-/Migrations-Tests

### P1 – vollständiger Geschäftsablauf

- [ ] Angebote, Aufträge, Auftragsbestätigungen und Lieferscheine
- [ ] Dokumentumwandlung und Dokumentketten
- [ ] Mahnungen und Pro-forma-Rechnungen
- [ ] Lagerbestand
- [ ] Druckvorlagen
- [ ] E-Rechnung
- [ ] Exporte

### P2 – erweiterte Kompatibilität

- [ ] Webshop-Integration
- [ ] Mailmigration/-vorlagen
- [ ] Fakturama-spezifische CSV-Importprofile
- [ ] weitere Statistiken und Spezialexporte
- [ ] weitergehende UI-Kompatibilität

---

## Definition of Done

Die Fakturama-Aufgabe gilt **nicht** bereits dann als umgesetzt, wenn SimpleOffice4Me eine ähnlich benannte Funktion besitzt.

Ein Teilbereich kann erst abgehakt werden, wenn:

1. Fakturama-Quelldaten korrekt erkannt werden,
2. alle relevanten Felder explizit gemappt sind,
3. nicht abbildbare Felder sichtbar gemeldet werden,
4. IDs und Beziehungen erhalten bleiben,
5. Datei-I/O sicher gegen Traversal und Symlink-Ausbruch ist,
6. Geld-/Steuerwerte reproduzierbar sind,
7. ein Wiederholungsimport keine Duplikate erzeugt,
8. automatisierte Regressionstests vorhanden sind,
9. der Importbericht die Übernahme nachvollziehbar dokumentiert.

Bis dahin bleibt der jeweilige Punkt offen.
