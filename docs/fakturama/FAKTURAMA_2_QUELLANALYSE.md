# Fakturama 2 – technische Vollanalyse und Rekonstruktionsspezifikation

**Analysierter Quellstand:** lokaler Quellbaum `fakturama-2`, Produkt-/Bundle-Version überwiegend 2.2.0  
**Analysezeitpunkt:** 25.07.2026  
**Zweck:** Beschreibung des tatsächlichen Programmumfangs auf Quellcodeebene als Grundlage für Dokumentation, Wartung, Migration oder funktionsgleichen Nachbau  
**Abgrenzung:** Diese Spezifikation beschreibt den vorliegenden Quellstand. Vorhandene `target`-Artefakte wurden nur zur Plausibilisierung, nicht als maßgebliche Quelle verwendet.

## 1. Kurzfassung

Fakturama 2 ist keine einfache Rechnungsmaske, sondern eine modular aufgebaute Desktop-Unternehmensanwendung für kleine Unternehmen. Ihr Kern ist die Verwaltung von Kontakten, Produkten, Steuern, Zahlungs- und Versandarten, Angeboten, Aufträgen, Auftragsbestätigungen, Lieferscheinen, Rechnungen, Gutschriften, Mahnungen, Pro-forma-Rechnungen, Briefen sowie Einnahme- und Ausgabebelegen. Darum liegen Funktionen für Bestandsführung, Dokumentumwandlung, Druckvorlagen, PDF- und E-Rechnungen, Mailversand, Buchhaltungs- und Statistikexporte, CSV-Importe, Webshop-Import und Altdatenmigration.

Technisch ist Fakturama eine Eclipse-4-Rich-Client-Anwendung. Die Oberfläche basiert auf SWT/JFace, Eclipse e4 Dependency Injection und dem e4-Anwendungsmodell. Funktionen sind als OSGi-Bundles gekapselt. Persistiert wird über Jakarta Persistence 3.0 und EclipseLink 4; die Datenbankstruktur wird mit Liquibase weiterentwickelt. Unterstützte JDBC-Ziele sind eine lokal eingebettete HSQLDB sowie MariaDB und MySQL. Geldwerte werden nicht nur als `double`, sondern in der Berechnungs- und Anzeigeebene über JSR 354 (`javax.money`, Moneta) behandelt.

Für einen Nachbau sind fünf Eigenschaften zentral:

1. Dokumente bilden eine gemeinsame, vererbte Domäne mit typabhängigen Regeln und nicht neun voneinander isolierte Masken.
2. Rechnungswerte sind abgeleitete Ergebnisse aus Positionen, Rabatt, Versand, Steuerart, Netto-/Bruttomodus, Anzahlung und Zahlungen.
3. Das System arbeitet mit einer lokalen, zustandsbehafteten Workspace-/Präferenzwelt; Einstellungen sind teils Eclipse-Präferenzen, teils Datenbankwerte.
4. Druck ist eine Vorlagen-Pipeline: Geschäftsdaten werden in ODT-Vorlagen eingesetzt, daraus werden Ausgabedokumente erzeugt und optional PDF/E-Rechnung nachbearbeitet.
5. Die Integrationen sind überwiegend datei-, JDBC-, SMTP- und HTTP-basiert. Es existiert keine allgemeine öffentliche REST-Server-API.

## 2. Methodik und Belegquellen

Die Analyse beruht auf:

- allen Maven-POMs und Tycho-Profilen,
- OSGi-Manifesten und Declarative-Services-Dateien unter `META-INF` und `OSGI-INF`,
- `Application.e4xmi` und den Plug-in-Erweiterungsdefinitionen,
- 690 Java-Quelldateien,
- dem JPA-Persistenzdeskriptor und den Modellklassen,
- rund 2.600 Zeilen Liquibase-Änderungssätzen,
- Import-, Export-, Office-, Mail-, QR-, Webshop- und Migrationsteilen,
- vorhandenen Testmodulen und Hilfedateien.

Der Quellbaum umfasst rund 2.200 Dateien. Davon entfallen etwa 295 Java-Dateien auf die RCP-Anwendung, 128 auf das Modell, 41 auf reguläre Exporte, 40 auf E-Rechnungen, 38 auf gemeinsame Infrastruktur, 31 auf Importe, 31 auf Assistenten und 32 auf erweiterte Dialoge. Die Größenverteilung zeigt: Die meiste Fach- und UI-Logik liegt im RCP-Bundle, nicht in einem getrennten Backend.

## 3. Produktumfang aus Anwendersicht

### 3.1 Stammdaten

#### Kontakte und Adressen

Das System verwaltet Debitoren und Kreditoren als spezialisierte Kontakte. Ein Kontakt besitzt Identitäts-, Kommunikations-, Zahlungs-, Steuer- und Bankdaten sowie mehrere Adressen. Adressen können Verwendungsarten wie Rechnungs- und Lieferadresse tragen. Kontakte lassen sich kategorisieren, suchen, filtern, in Tabellen oder Baumstrukturen darstellen und in Dokumente übernehmen.

Fachlich relevante Daten sind unter anderem:

- Anrede, Titel, Vorname, Name und Firmenangaben,
- Kundennummer/Lieferantennummer,
- Anschriften mit Land und Adresszusätzen,
- Telefon, Mobiltelefon, Fax, E-Mail und Internetadresse,
- USt-IdNr. und steuerliche Angaben,
- Bankkonto,
- bevorzugte Zahlungsart,
- Rabatt und Zahlungsziel,
- Debitor-/Kreditorrolle,
- alternative Kontakte und Kontaktkategorien,
- Notizen und Zustands-/Zeitstempeldaten.

Die dokumentierte Anschrift eines Geschäftsdokuments ist als Snapshot zu verstehen. Spätere Änderungen am Kontakt dürfen ein bereits erzeugtes Dokument nicht semantisch umschreiben. `DocumentReceiver` und der `DocumentAddressManager` bilden diese Trennung explizit ab.

#### Produkte

Produkte enthalten mindestens Artikelnummer, Bezeichnung, Beschreibung, Preis, Steuer, Mengeneinheit, Kategorie und Lagerbestand. Hinzu kommen:

- Einkaufs-/Verkaufspreisinformationen,
- Gewicht,
- Lieferanten- und Webshopbezüge,
- Bilddaten bzw. Bildpfad,
- Optionen/Varianten,
- Staffelpreise (`ProductBlockPrice`),
- Bestandsveränderungsregeln,
- optionale Artikel und Kategorisierung.

Produkte können selektiert, dupliziert, importiert, exportiert und als Dokumentposition eingefügt werden. Eine Dokumentposition ist anschließend eine fachliche Momentaufnahme; Änderungen am Produktstamm dürfen nicht automatisch historische Dokumentpositionen verändern.

#### Steuersätze

Steuersätze (`VAT`) haben Namen, Beschreibung, Prozentwert, Kategorie und steuerliche Kennzeichnungen. Die Anwendung unterstützt:

- normale Umsatzsteuer,
- steuerfreie bzw. abweichende Sachverhalte,
- Zuordnung eines UNTDID-5305-Steuercodes,
- Steuerberechnung auf Positionen,
- automatische oder explizite Versandsteuer,
- Netto- und Bruttodokumente,
- Steuersummen je Steuersatz.

Die E-Rechnungslogik braucht neben dem Prozentwert den normierten Steuerkategoriecode. Im aktuellen 2.2-Schema wurde deshalb ein Feld für den UNTDID-5305-Code ergänzt.

#### Zahlungsarten

Zahlungsarten enthalten Bezeichnung, Zahlungsziel, Skonto-/Zahlungsinformationen, Kategorie und externe Codes. Die Anwendung nutzt sie für Fälligkeit, Dokumenttexte, Zahlstatus und E-Rechnung. UNTDID-4461-Übersetzer bilden interne Zahlungsarten auf standardisierte E-Rechnungscodes und zurück ab.

#### Versandarten

Versandarten besitzen Preis, Steuerzuordnung, Kategorie und Regeln zur steuerlichen Behandlung. Der Versand kann als zusätzlicher Dokumentwert gerechnet werden; die Steuer kann fest sein oder aus den Positionen automatisch abgeleitet werden.

#### Textbausteine

Textmodule sind kategorisierte, wiederverwendbare Texte. Sie können über Such-/Auswahldialoge in Dokumente eingesetzt werden, etwa als Einleitungs-, Schluss- oder Zusatztext.

#### Kontenarten

`ItemAccountType` und `ItemListTypeCategory` strukturieren Buchungs-/Belegpositionen. Sie dienen insbesondere der Ausgaben- und Belegerfassung sowie den Buchhaltungsexporten.

### 3.2 Geschäftsdokumente

Der gemeinsame Dokumenttyp `Document` wird durch konkrete JPA-Unterklassen spezialisiert. Die stabilen Typkennzahlen sind:

| ID | Typ | Wertpositionen | Summen/Preis | zahlungsrelevant | bestandsrelevant |
|---:|---|---|---|---|---|
| 1 | Brief | nein | nein | nein | nein |
| 2 | Angebot | ja | ja | nein | nein |
| 3 | Auftrag | ja | ja | nein | nach Konfiguration |
| 4 | Auftragsbestätigung | ja | ja | nein | nein |
| 5 | Rechnung | ja | ja | ja | nach Konfiguration |
| 6 | Lieferschein | ja | typischerweise ohne Preise druckbar | nein | nach Konfiguration |
| 7 | Gutschrift | ja | ja, negatives Vorzeichen | ja | umkehrend/konfigurationsabhängig |
| 8 | Mahnung | Referenz-/Textcharakter | typabhängig | ja | nein |
| 9 | Pro-forma-Rechnung | ja | ja | nein | nein |

Die Dokumenttypdefinition enthält zusätzlich UI-Namen, Pluralnamen, Menükennung, Adressbeschriftung, Vorzeichen und standardisierte Typcodes für den elektronischen Austausch.

Gemeinsame Dokumentinformationen sind:

- Dokumentnummer und Dokumentdatum,
- Bearbeitungs-/Liefer-/Leistungsdatum beziehungsweise Leistungszeitraum,
- Kunde und gespeicherter Empfänger,
- Rechnungs- und Lieferanschrift,
- Positionen und Reihenfolge,
- Einzel- und Gesamtrabatt,
- Netto-/Bruttomodus,
- Versandart und Versandwert,
- Zahlungsart und Fälligkeit,
- Anzahlung, bezahlter Betrag, Bezahlt-Status und Zahldatum,
- Einleitungs-, Schluss- und Zusatztexte,
- Kundenreferenz, Transaktions-ID und Webshop-ID,
- Quelldokument, Rechnungsreferenz und Dokumentkette,
- Druckvorlage, ODT-Pfad und PDF-Pfad,
- Druckstatus und Fortschritt,
- Gesamtwert,
- optionale E-Rechnungs-/Steuerinformationen,
- Audit-, Versions-, Gültigkeits- und Soft-Delete-Felder.

#### Positionen

Eine Dokumentposition kann Produktposition, freie Position, Textposition, Zwischensumme oder anderer `ItemType` sein. Sie enthält insbesondere:

- Positionsnummer/Reihenfolge,
- Artikelnummer, Name, Beschreibung,
- Menge und Einheit,
- Einzelpreis,
- Rabatt,
- Steuerobjekt und Steuersatz,
- Netto-/Bruttobeträge,
- optionale Kennzeichnungen,
- Produktreferenz,
- Gewichts-/Konteninformationen.

Die UI bietet Listenaufbau, Produkt- und Textauswahl, Verschieben nach oben/unten, Löschen, optionale Positionen sowie tabellarische Bearbeitung.

#### Berechnung

`DocumentSummaryCalculator`, `VoucherSummaryCalculator`, `AccountSummaryCalculator`, `VatSummarySetManager` und verwandte Klassen bilden eine eigene Berechnungsschicht. Ein kompatibler Nachbau muss mindestens folgende Reihenfolge deterministisch festlegen:

1. Menge × Einzelpreis je werttragender Position.
2. Positionsrabatt.
3. Rundung auf der fachlich definierten Stufe.
4. Trennung oder Rückrechnung von Netto und Steuer abhängig vom Netto-/Bruttomodus.
5. Gruppierung nach Steuersatz.
6. Dokumentrabatt.
7. Versand netto/brutto und Versandsteuer.
8. Vorzeichenkorrektur für Gutschriften.
9. Abzug von Anzahlungen und bereits bezahlten Beträgen.
10. Bildung von offenem Betrag, Steuersummen und Gesamtbetrag.

Geldbeträge verwenden JSR 354/Moneta. Ein Nachbau darf nicht blind binäre Gleitkommaarithmetik übernehmen; Währung, Rundungsmodus und Anzeigegenauigkeit müssen zentral und reproduzierbar sein.

#### Nummernkreise

`NumberGenerator`, `NumberRangeValuesPreferencePage` und `NumberRangeFormatPreferencePage` verwalten Nummernkreise und Formate. Pro Dokument-/Stammdatentyp können Präfix, laufende Nummer und Formatmuster gelten. Nummern dürfen erst nach einer fachlich definierten Aktion endgültig verbraucht werden; Parallelzugriffe müssen beim Nachbau transaktional geschützt sein.

#### Dokumentumwandlung

Dokumente können kopiert/dupliziert und über Handler in Folgedokumente überführt werden. Dabei sind Quellbezug, Referenzen, Positionen, Empfänger-Snapshot, Preise, Texte und Typregeln zu übernehmen. Typische Ketten sind Angebot → Auftrag/Bestätigung → Lieferschein → Rechnung → Mahnung oder Gutschrift. Der Quellcode unterscheidet `sourceDocument` und `invoiceReference`; diese Beziehungen dürfen beim Nachbau nicht in ein einziges Feld zusammengelegt werden.

#### Statusaktionen

Vorhandene Befehle decken unter anderem ab:

- Speichern und Schließen,
- Löschen/Soft Delete,
- Duplizieren,
- als bezahlt markieren,
- Rechnungsreferenz entfernen,
- Auftrag als bearbeitet/versandt/anderer Status markieren,
- Lagerbestand aktualisieren,
- Dokument erstellen und drucken,
- Webshopstatus synchronisieren.

### 3.3 Belege und Buchhaltung

`Voucher` und `VoucherItem` bilden Einnahme- und Ausgabebelege. Belege besitzen Typ, Datum, Nummer, Lieferant/Empfänger, Kategorie, Brutto-/Nettowerte, Steuerwerte, Zahlungsinformationen und mehrere Buchungspositionen. Die Anwendung hat getrennte Editoren und Listen für Einnahme- und Ausgabebelege.

Funktionen:

- Beleg anlegen, ändern, kategorisieren und löschen,
- mehrere Positionen je Beleg,
- Kontenart und Steuersatz je Position,
- Summierung nach Konten und Steuer,
- Import von Ausgaben,
- Kontist-CSV-Import,
- Export von Einnahmen, Ausgaben und Buchungslisten,
- zeitliche Filterung und Unbezahlt-Filter.

Die Anwendung ist damit eine vorbereitende Buchhaltungssoftware, aber aus dem Quellstand ist kein vollständiges Hauptbuch mit Soll/Haben-Journal, Abschlussbuchungen und revisionssicherer Festschreibung ableitbar.

### 3.4 Listen, Suche und Navigation

Die UI enthält:

- eine Navigationsansicht,
- Tabellenlisten für Kontakte, Dokumente, Produkte, Zahlungen, Versandarten, Steuern, Textbausteine und Belege,
- Kategorien als Baum,
- Filterzeilen und Matcher,
- sortier- und konfigurierbare Spalten,
- Auswahl- und Suchdialoge,
- mehrere gleichzeitig geöffnete Editor-Parts,
- Perspektiven, Toolbar und dynamische Menüs.

Technisch werden NatTable, GlazedLists, SWT/JFace-Databinding und Nebula-Widgets kombiniert. Die Datenlisten sind keine einfachen SWT-Tabellen: Layer-Stacks, Filteratoren, Matcher, Converter, LabelProvider und Painter steuern Auswahl, Formatierung, Checkboxen, Bilder und Statusdarstellung.

### 3.5 Einstellungen

Die Präferenzseiten umfassen mindestens:

- allgemeine Einstellungen,
- eigene Firma,
- Kontakte und Kontaktformat,
- Produkte,
- Dokumente und optionale Positionen,
- Nummernkreiswerte und Nummernformat,
- Büro-/OpenOffice-Ausgabe,
- Browser,
- Export,
- Toolbar,
- Webshop-Import und Webshop-Autorisierung,
- Maildienst,
- ZUGFeRD/E-Rechnung.

Einstellungen werden über Eclipse-Präferenzknoten, `FakturamaPreferenceStore`, Providerklassen und teilweise `PreferencesInDatabase` verwaltet. Sensible Werte können über Eclipse Equinox Security/Secure Storage laufen. Ein Nachbau braucht ein explizites Einstellungsmodell mit Scope: global, Workspace, Benutzer, Mandant und vertraulich.

## 4. Ein- und Ausgabe

### 4.1 Import

Das Import-Bundle registriert sieben Assistentenfamilien:

- klassischer Kontakt-CSV-Import,
- generischer Kontakt-CSV-Import mit Spaltenzuordnung,
- Debitor-Import,
- Kreditor-Import,
- klassischer Produkt-CSV-Import,
- generischer Produkt-CSV-Import mit Mapping,
- Ausgaben-/Kontist-Import.

Die CSV-Schicht basiert auf OpenCSV. Sie unterstützt:

- Auswahl der Eingabedatei,
- Zeichensatz-/Formatkonfiguration,
- Headererkennung,
- Quellspalte-zu-Zielfeld-Mapping,
- typisierte Konverter für Datum, Boolean und Dezimalzahlen,
- Importoptionen und Fortschrittsdialog,
- DTO/Bean-Zwischenmodelle,
- Validierung vor Persistierung.

Bei einem Nachbau müssen Locale-Aspekte ausdrücklich behandelt werden: Dezimalkomma, Tausendertrennzeichen, Datumsformat, CSV-Trennzeichen, Quote/Escape, leere Werte und Boolesche Schreibweisen.

### 4.2 Export

Das Export-Bundle enthält mindestens folgende fachliche Exporte:

- Konten-/Buchungsexport,
- Adressliste,
- Kontakt-Datenblatt,
- vCard-Kontaktexport,
- Produktliste,
- Textbausteine,
- Käuferumsätze,
- Käufer je Produkt,
- Verkaufsübersicht,
- verkaufte Mengen und Summen,
- Einnahmebelege,
- Ausgabebelege,
- kombinierte Einnahmen-/Ausgabenlisten,
- unbezahlte Dokumente,
- ODS/ODF-Tabellen über einen Calc-Exporter,
- CSV-Ausgabe.

Exporte werden über eigene Eclipse-Erweiterungspunkte `org.fakturama.wizards.exportWizards` und `importWizards` auffindbar gemacht. Damit ist die Import-/Exportoberfläche intern erweiterbar, ohne den RCP-Kern neu zu verdrahten.

### 4.3 Office- und Vorlagenausgabe

Der Druckpfad arbeitet mit ODF/ODT und dem ODF Toolkit:

1. Auswahl einer dokumenttypspezifischen Vorlage.
2. Kopieren der Vorlage in ein Arbeits-/Ausgabeverzeichnis.
3. Ermitteln und Ersetzen von Platzhaltern.
4. Einsetzen von Adressen, Dokumentkopf, Positionstabelle, Summen, Steuergruppen, Zahlungs- und Firmendaten.
5. Speichern eines ODT-Dokuments.
6. Optional Start/Ansteuerung einer Office-Anwendung und PDF-Erzeugung.
7. Optional PDF-Nachverarbeitung durch einen `IPdfPostProcessor`, etwa Einbettung einer E-Rechnung.

`TemplateFinder`, `TemplateProcessor`, `TemplateProcessorHelper`, `OfficeStarter`, `OfficeDocument` und `CreateOODocumentHandler` teilen diese Aufgaben. Die Ressourcen-Bundles liefern Vorlagen und Icons; ein `TemplateService` abstrahiert ihren Zugriff.

Für einen Nachbau ist eine vollständige Platzhalterliste direkt aus `TemplateProcessor` und den mitgelieferten Templates zu gewinnen. Wichtig sind wiederholte Tabellenbereiche, optionale Felder, Steuerzeilen, Seitenumbrüche, Bildersetzung und Locale-Formatierung. Ein einfaches String-Replacement über die ZIP-Datei einer ODT-Vorlage wäre nicht hinreichend.

### 4.4 PDF und elektronische Rechnung

Das E-Rechnungs-Bundle erzeugt XML und bettet dieses mit Apache PDFBox/XMPBox in PDF/A-kompatible Dokumente ein. Es enthält:

- abstrakte E-Rechnungserzeugung,
- Konverter vom Fakturama-Dokument in ein normiertes Rechnungsmodell,
- Factur-X/ZUGFeRD-Modellklassen,
- Konformitätsstufen,
- XML-Erzeugung via JAXB/EclipseLink MOXy,
- PDF-Metadaten und Embedded Files,
- kryptografische Bibliotheken Bouncy Castle als Laufzeitabhängigkeit,
- Präferenzen und Defaultwerte.

Relevante Standards/Codes im Quellstand:

- ZUGFeRD/Factur-X-artige hybride Rechnung,
- PDF/A-Erweiterungsschema,
- UNTDID 4461 für Zahlungsarten,
- UNTDID 5305 für Steuerkategorien,
- CEFACT-Codes für Einheiten und weitere Codelisten.

Der Nachbau muss unterscheiden zwischen fachlichem Rechnungsmodell, XML-Serialisierung, Profil/Conformance Level und PDF-Einbettung. Validierung gegen das jeweils unterstützte Schema ist als eigener Schritt vorzusehen.

### 4.5 QR-Zahlcodes

Es gibt zwei QR-bezogene Pfade:

- ZXing/QRGen für allgemeine QR-Code-Erzeugung,
- `qrbill-generator` für Schweizer QR-Rechnungen beziehungsweise Swiss-QR-Zahlteile.

`QRCodeService`, `QRKExportHandler` und `QRKPaymentTypes` erzeugen Zahlungsinformationen aus Rechnung, Bankverbindung, Empfänger und Betrag. Ein Nachbau muss IBAN, Referenztyp, Währung, Adressformat und Pflichtfelder nach dem jeweiligen QR-Verfahren validieren.

### 4.6 E-Mail

Das Connector-Bundle stellt `MailService` als OSGi Declarative Service bereit. Es verwendet Jakarta Mail/Angus Mail und Jakarta Activation. Der Dienst kann:

- SMTP-Konfiguration aus Präferenzen laden,
- Absender, Empfänger, CC/BCC und Betreff verarbeiten,
- Text und Anhänge als MIME-Nachricht zusammensetzen,
- Dokumentdateien anhängen,
- Transportverschlüsselung und Authentifizierung gemäß Einstellungen einsetzen,
- Erfolgs-/Fehlerdialoge anzeigen.

Die Schnittstelle ist intern als OSGi-Service gedacht, nicht als Netzwerkserver. Passwörter dürfen beim Nachbau nicht im Klartext in normalen Präferenzdateien landen.

### 4.7 Webshop

Das RCP-Bundle enthält eine HTTP-basierte Webshop-Importschicht:

- `IWebshopConnection` als Verbindungsabstraktion,
- `WebShopConnector` für Aufrufe,
- `AbstractWebshopImporter`,
- `WebShopDataImporter`,
- `WebShopStatusImporter`,
- `WebShopImportManager`,
- `OrderSyncManager`,
- `Synchronize` und `InterruptConnection`,
- Webshop- und Statusmapping-DAO,
- Autorisierungs- und Importpräferenzen.

Persistente Konfiguration liegt in `WebShop` und `WebshopStateMapping`. Importiert beziehungsweise synchronisiert werden typischerweise:

- Bestellungen,
- Kunden- und Adressdaten,
- Positionen/Produkte,
- Versand- und Zahlungsinformationen,
- Transaktions-/Shop-IDs,
- Shopdatum,
- externer und interner Bestellstatus.

Die Kommunikation nutzt Apache HttpClient 5. Verbindungsabbrüche können signalisiert werden. Statuswerte des Shops werden auf Fakturama-Zustände gemappt. Unter `org.fakturama.webshop.connectors` liegen historische/serverseitige Connector-Ressourcen für OSCommerce, XTCommerce und XTCModified; dieser Ordner ist im aktuellen Parent-POM jedoch kein eigenständiges gebautes Modul. Er ist deshalb als mitgelieferte Integrationsvorlage bzw. Legacy-Bestand zu bewerten, nicht automatisch als vollständig aktiviertes Produktfeature.

Sicherheitsrelevant: Shop-URL, Zugangsdaten, TLS-Prüfung, Timeouts, Proxy, Zeichensatz, Wiederholungen und idempotenter Import. Webshop-ID/Transaktions-ID müssen Dubletten verhindern.

### 4.8 Paketdienst und Browser

`ParcelService`, `ParcelServiceManager`, `ParcelServiceFormFiller`, `ParcelServiceBrowserEditor` und `OpenParcelServiceHandler` öffnen Paketdienstseiten im eingebetteten Browser und befüllen Formulare beziehungsweise URLs mit Versanddaten. Daneben gibt es allgemeine Browser-Editoren und konfigurierbare interne/externe Browserunterstützung.

Dies ist eher Browserautomation/URL-Integration als eine stabile Carrier-REST-API. Bei einem Nachbau sollten Paketdienste als austauschbare Adapter mit klaren URL-/Formularparametern modelliert werden.

## 5. Technische Architektur

### 5.1 Laufzeit

- Java 17
- Eclipse 4 RCP
- Equinox OSGi
- SWT und JFace
- Eclipse e4 Dependency Injection und Event-/Context-System
- Declarative Services für infrastrukturelle Dienste
- Maven 3.9+ und Tycho 4.0.8 für den Build

Die Anwendung ist ein modularer Monolith: getrennte Bundles im selben Desktopprozess, gemeinsame Datenbank und gemeinsame Benutzeroberfläche. OSGi trennt Klassenräume und Services, bildet aber keine verteilten Microservices.

### 5.2 Schichten

```text
SWT/JFace/NatTable-Parts, Editoren, Dialoge, Wizards
                 |
Handler, Manager, Calculator, Importer/Exporter
                 |
DAO-Schicht + OSGi-Dienste + Präferenzdienste
                 |
JPA/EclipseLink + Liquibase + JDBC DataSourceFactory
                 |
HSQLDB lokal | MariaDB | MySQL

Seitliche Adapter:
ODT/OpenOffice | PDF/ZUGFeRD | SMTP | HTTP-Webshop |
Browser/Paketdienst | CSV/ODS/vCard | QR-Code
```

### 5.3 Zentrale Bundles

| Bundle | Verantwortung |
|---|---|
| `com.sebulli.fakturama.common` | Konstanten, Locale-/Formatdienste, Logging-Adapter, Geld-/Rundungsdienste, gemeinsame Ausnahmen und i18n |
| `com.sebulli.fakturama.model` | JPA-Modell, DTOs, EMF/Texo-Kompatibilität, DB-Update-Service, alte Modellklassen |
| `com.sebulli.fakturama.rcp` | Hauptanwendung, UI, DAOs, Berechnung, Handler, Präferenzen, Office- und Webshop-Orchestrierung |
| `com.sebulli.fakturama.resources` | Icons-/Templatezugriff und URL-Streamhandler |
| `com.sebulli.fakturama.resources.icons` | Grafikressourcen |
| `com.sebulli.fakturama.resources.templates` | mitgelieferte Vorlagen |
| `com.sebulli.fakturama.hsqlconnector` | eingebettete HSQLDB-Verbindung, Serveraktivierung und Backup |
| `org.fakturama.database.hsql` | OSGi-JDBC-Treiberregistrierung HSQLDB 2.7.2 |
| `org.fakturama.database.mariadb` | OSGi-JDBC-Treiberregistrierung MariaDB 3.2.x |
| `org.fakturama.database.mysql` | OSGi-JDBC-Treiberregistrierung MySQL Connector/J 9.x |
| `org.fakturama.import` | CSV-/Kontist-Importassistenten |
| `org.fakturama.export` | CSV-/ODF-/vCard- und Statistikexporte |
| `org.fakturama.wizards` | generische Wizard-Infrastruktur und Extension Registry |
| `org.fakturama.print.openoffice` | ODF-Navigation und OpenOffice-Ausgabehilfen |
| `org.fakturama.exporter.zugferd` | E-Rechnungs-XML und PDF/A-Einbettung |
| `org.fakturama.connectors` | Maildienst und Mailpräferenzen |
| `org.fakturama.qrcode` | QR-Code-Dienst |
| `org.fakturama.e4.ui.dialogs.ext` | erweiterte e4-Dialoge, About-/Installationsdialog |
| `org.fakturama.logging` | SLF4J/Logback-Laufzeit |
| `org.fakturama.help` | Eclipse-Hilfe/DocBook-Inhalte |
| `com.sebulli.fakturama.site` | Produktdefinition, P2-Site und Plattformpaketierung |
| `org.fakturama.migtool` | separates Kommandozeilen-/Migrationstool |

### 5.4 Interne Service-Schnittstellen

OSGi Declarative Services stellen unter anderem bereit:

- Datumsformatierung,
- Zahlenformatierung,
- Locale-Service,
- Logging,
- Monetary-Amount-Formatierung,
- Rundungsprovider,
- EntityManager-/Persistenzservice,
- Datenbankupdate-Service,
- Dokumentadressmanager,
- HSQL-Serveraktivierung,
- Template-Service,
- URL-Streamhandler,
- Exportservice,
- Importservice,
- Mailservice,
- QR-Code-Service,
- ZUGFeRD-Exporter,
- Paketdienst und Splash-Service.

Diese Dienste sind die wichtigsten internen Erweiterungsgrenzen. Für einen Nachbau außerhalb OSGi sollten sie als Interfaces mit Dependency Injection erhalten bleiben.

### 5.5 DAOs und Transaktionen

Die RCP-Schicht besitzt rund 29 DAO-Klassen. `AbstractDAO` bündelt EntityManager-Zugriff, Abfragen, Speichern, Löschen und Transaktionsbehandlung. Spezialisierungen existieren für:

- Adressen,
- Kontakte/Debitoren/Kreditoren,
- Dokumente und Dokumentempfänger,
- Produkte und Kategorien,
- Zahlungen, Versand und Steuern,
- Texte,
- Belege und Belegpositionen,
- Kontenarten,
- Webshops,
- Kategorien aller Domänen,
- alte Entitäten zur Migration.

Die Anwendung arbeitet mit RESOURCE_LOCAL-JPA-Transaktionen. Es gibt keinen Java-EE/JTA-Container. Service- oder DAO-Aufrufe, die mehrere Objekte ändern, müssen beim Nachbau in einer gemeinsamen Transaktion liegen; UI-Databinding allein ist keine Transaktionsgrenze.

### 5.6 Startsequenz

Die Startlogik verteilt sich auf Lifecycle-/Initializer-Klassen:

1. Eclipse/Equinox startet Produkt und Bundles.
2. Präferenz- und Loggingdienste werden verfügbar.
3. Workspace und Anwendungskontext werden initialisiert.
4. JDBC-Treiber/DataSourceFactory wird gewählt.
5. Bei lokaler HSQLDB wird der Server/die Datenbank aktiviert.
6. Liquibase prüft und aktualisiert die Struktur, sofern nicht explizit deaktiviert.
7. EclipseLink erzeugt den EntityManager.
8. Defaultwerte und Ersteinrichtungsdialog werden ausgeführt.
9. e4-Anwendungsmodell, Perspektive, Navigation, Menüs und Handler erscheinen.
10. Hintergrundinitialisierung und gegebenenfalls Migration laufen mit Fortschritts-/Fehleranzeige.

Ein Nachbau sollte DB-Migration vor Freigabe schreibender UI-Aktionen abschließen und fehlgeschlagene Migrationen hart sichtbar machen.

## 6. Datenmodell

### 6.1 Gemeinsame Entitätseigenschaften

`ModelObject`/`IEntity` bilden die gemeinsame Basis. Wiederkehrende Felder sind:

- `id` als Primärschlüssel,
- `version` für optimistische Versionierung,
- `name`,
- `dateAdded`,
- `modified` und `modifiedBy`,
- `deleted` für logisches Löschen,
- `validFrom` und `validTo`.

Das Vorhandensein von `deleted`, Gültigkeitszeiträumen und Version zeigt, dass physisches Löschen nicht die einzige Semantik ist. Listen müssen gelöschte Datensätze standardmäßig ausfiltern, Referenzen historischer Dokumente aber erhalten.

### 6.2 Entitätsgruppen und Tabellen

#### Kategorien

`AbstractCategory` nutzt eine selbstreferenzierende Elternbeziehung und wird für Kontakt-, Produkt-, Steuer-, Versand-, Text-, Beleg- und Listenarten spezialisiert. Kategorien bilden hierarchische Bäume.

Wichtige Tabellen: `FKT_CATEGORY` plus diskriminierte/abgeleitete Kategorien.

#### Kontakte

- `Contact` als Basistyp,
- `Debitor` und `Creditor` als Rollen/Spezialisierungen,
- `Address` als 1:n-Beziehung,
- `BankAccount`,
- `ContactType` an Adressen,
- `ContactCategory`.

Adressverwendungen werden seit Schema 2.1 über `FKT_ADDRESS_CONTACTTYPES` abgebildet. Kontakt und Adresse wurden in der 2.1-Migration wesentlich restrukturiert; temporäre Tabellen in Liquibase belegen eine Datenumschichtung.

#### Dokumente

`FKT_DOCUMENT` enthält die gemeinsamen Felder. Konkrete Typen verwenden Joined Inheritance mit Primärschlüssel-Fremdschlüsseln:

- `FKT_LETTER`,
- `FKT_OFFER`,
- `FKT_ORDER`,
- `FKT_CONFIRMATION`,
- `FKT_INVOICE`,
- `FKT_DELIVERY`,
- `FKT_CREDIT`,
- `FKT_DUNNING`,
- `FKT_PROFORMA`.

Weitere Tabellen:

- `FKT_DOCUMENTITEM`,
- `FKT_DOCUMENTRECEIVER`,
- `FKT_INDIVIDUALDOCUMENTINFO`.

Wichtige Relationen:

- Dokument 1:n Position,
- Dokument 1:n/1:1 Empfängerinformationen je Implementierung,
- Position n:1 Produkt,
- Position n:1 Steuer,
- Dokument n:1 Zahlung,
- Dokument n:1 Versand,
- Dokument n:1 Steuerbefreiungsreferenz,
- Dokument n:1 Quelldokument,
- Dokument n:1 Rechnungsreferenz.

#### Produkte

- `FKT_PRODUCT`,
- `FKT_PRODUCTOPTIONS`,
- `FKT_PRODUCTBLOCKPRICE`,
- Produktkategorie über `FKT_CATEGORY`,
- Steuerreferenz auf `FKT_VAT`.

#### Belege

- `FKT_VOUCHERS`,
- `FKT_VOUCHERITEMS`,
- Referenzen auf Belegkategorie, Steuer und Kontenart.

#### Konfiguration und Organisation

- `Tenant`,
- `User`,
- `Role`,
- `UserProperty`,
- `WebShop`,
- `WebshopStateMapping`.

Der Modellbestand enthält Mandanten- und Benutzerentitäten. Die sichtbare Desktoparchitektur wirkt dennoch primär workspaceorientiert; ein Nachbau darf daraus nicht ohne weitere Prüfung vollwertige Mehrmandantenfähigkeit oder serverseitige Benutzerisolation ableiten.

### 6.3 Weitere Kerntypen

- `Payment`: Zahlungsart, Zahlungsziel, Skonto-/Text-/Codeinformationen.
- `Shipping`: Versandart, Preis, Steuer, Kategorie.
- `VAT`: Steuersatz, Kategorie und Normcode.
- `TextModule`: kategorisierter Text.
- `CEFACTCode`: normierte Codelisteneinträge.
- `ItemAccountType`: Beleg-/Buchungskonto.
- `IndividualDocumentInfo`: zusätzliche dokumentindividuelle Daten.
- `ProductOptions`: Produktvarianten/-optionen.
- `ProductBlockPrice`: mengenabhängiger Preis.

### 6.4 Datenbankmigration

Liquibase ist die autoritative Schemaschicht:

- `db.changelog-master.xml` bindet Versionsdateien ein,
- 2.0 legt das Grundschema an,
- 2.1 führt umfangreiche Struktur- und Datenmigrationen aus,
- 2.2 ergänzt unter anderem Empfänger- und Steuerstandardfelder.

`DbUpdateService` beschafft eine Verbindung entweder über die interne DB-Verbindung oder eine OSGi `DataSourceFactory` und führt Liquibase aus. Die automatische EclipseLink-DDL-Erzeugung ist absichtlich nicht aktiv. Der Systemschalter `NODBUPDATE` kann Updates überspringen; dies ist nur für kontrollierte Betriebsfälle geeignet.

### 6.5 Altdaten

Eine zweite Persistence Unit `origin-datasource` mappt Tabellen einer Fakturama-Version vor 2.0:

- Kontakte,
- Dokumente und Positionen,
- Produkte,
- Zahlungen,
- Versand,
- Steuern,
- Ausgaben und Einnahmebelege,
- Texte und Properties.

`MigrationManager`, `OldEntitiesDAO`, alte Modellklassen und das separate `org.fakturama.migtool` übertragen diese Daten. `CamelCaseSessionCustomizer` gleicht alte Namenskonventionen aus.

## 7. Externe und interne Schnittstellen

| Schnittstelle | Richtung | Protokoll/Format | Authentisierung | Zweck |
|---|---|---|---|---|
| HSQLDB | bidirektional | JDBC/HSQL | lokal/DB-Zugang | Standarddatenbank, Backup |
| MariaDB | bidirektional | JDBC | Benutzer/Passwort | externe Mehrbenutzer-DB |
| MySQL | bidirektional | JDBC | Benutzer/Passwort | externe Mehrbenutzer-DB |
| SMTP | ausgehend | SMTP(S), MIME | Benutzer/Passwort | Dokumentmail mit Anhängen |
| Webshop | bidirektional | HTTP(S), connectorabhängige Payload | Shopzugang | Bestellimport und Statussync |
| Paketdienst | ausgehend | URL/Formular im Browser | dienstabhängig | Versandlabel/-erfassung |
| Office | lokal | ODT/ODF, Prozess-/Dateiintegration | keine | Dokumenterstellung/PDF |
| Dateisystem | bidirektional | ODT, PDF, CSV, ODS, vCard, Bilder | OS-Rechte | Import/Export/Ablage |
| E-Rechnung | ausgehend | XML + PDF/A-Anhang | keine | ZUGFeRD/Factur-X |
| QR | ausgehend | PNG/Bild bzw. QR-Payload | keine | Zahlungs-QR |
| Eclipse P2 | eingehend | HTTP(S) Update Repository | optional | Produktupdates |
| OSGi DS | intern | Java-Service Registry | keine | lose gekoppelte Module |
| e4 Extension Points | intern | XML/Java-Klassen | keine | Import-/Export-Wizards |
| Eclipse Events/Context | intern | In-Process Events/DI | keine | UI-Kommunikation |

Nicht vorhanden ist im analysierten Kern:

- ein eingebetteter REST- oder GraphQL-Server,
- eine öffentlich dokumentierte Remote-API,
- ein Message Broker,
- eine Cloudpflicht,
- ein separates Webfrontend.

### 7.1 Fehler- und Timeoutverhalten

Bei externen Schnittstellen müssen Fehler in vier Kategorien getrennt werden:

- Konfigurationsfehler,
- temporäre Netzwerk-/Transportfehler,
- fachliche Ablehnung/Validierungsfehler,
- teilweise erfolgreiche Operation.

Insbesondere beim Webshopimport ist Idempotenz zwingend. Beim Mailversand darf „SMTP akzeptiert“ nicht mit „Empfänger hat erhalten“ verwechselt werden. Beim PDF/Office-Pfad muss ein vorhandenes ODT von einem erfolgreich erzeugten PDF unterschieden werden.

## 8. Benutzeroberfläche und Bedienmodell

Das `Application.e4xmi` ist die deklarative Hauptdefinition. Es beschreibt Fenster, Perspektiven, Parts, Menüs, Toolbars, Commands und Handler. Die Handler sind per e4-DI zustandslos oder kurzlebig; Editoren halten ein gebundenes Domänenobjekt und Dirty-/Save-Zustand.

Zentrale Editorarten:

- Kontakt/Debitor/Kreditor,
- Produkt,
- Dokument,
- Zahlung,
- Versand,
- Steuer,
- Textmodul,
- Einnahmebeleg,
- Ausgabebeleg,
- Listen-/Browser-/Bildeditor.

Wichtige Interaktionsregeln:

- Auswahl in Listen öffnet einen Editor mit Entitäts-ID.
- Neue Objekte erhalten Defaultwerte aus Präferenzen.
- Änderungen setzen Dirty-State.
- Schließen fragt bei ungespeicherten Änderungen.
- Speichern validiert und persistiert in einer Transaktion.
- Listen reagieren auf Änderungen und aktualisieren ihre Eventlisten.
- Löschen erfolgt fachlich kontrolliert und meist logisch.
- ParamConverter übertragen String-Commandparameter in Zahl, Boolean, Dokument oder Entität.

`ClosePartAddon`, `WindowCloseListenerAddon`, `SaveablePartPromptDialog` und Save-/Close-Handler verhindern stillen Datenverlust. Diese UX-Semantik gehört zum funktionalen Umfang.

## 9. Abhängigkeiten und ihre konkrete Aufgabe

| Bibliothek/Framework | Aufgabe |
|---|---|
| Eclipse e4/Equinox | Desktopplattform, DI, Lifecycle, Commands, OSGi |
| SWT/JFace | native Widgets, Dialoge, Databinding |
| NatTable | große editier-, filter- und formatierbare Tabellen |
| GlazedLists | beobachtbare, filter-/sortierbare Listen |
| Eclipse Nebula | Datum, formatierte Felder, Gruppen, Bilder, MultiChoice, TreeMapper |
| Jakarta Persistence 3.1 / EclipseLink 4 | ORM, Abfragen, Persistenz |
| Liquibase 4.25 | versionierte DB-Struktur und Datenmigration |
| HSQLDB 2.7 | eingebettete Standarddatenbank |
| MariaDB Connector/J | MariaDB-JDBC |
| MySQL Connector/J | MySQL-JDBC |
| JSR 354 / Moneta | Geldwerte, Währungen, Rundung |
| Apache Commons Lang/Text/IO/Collections/Codec/Validator | String-, Datei-, Collection-, Codec- und Validierungshilfen |
| Apache HttpClient 5 | Webshop-HTTP |
| OpenCSV | CSV-Import/-Export |
| ODF Toolkit | ODT/ODS-Verarbeitung |
| Apache POI | Tabellen-/Excel-nahe Assistentenfunktionen |
| PDFBox/XMPBox | PDF/A, Anhänge und Metadaten |
| JAXB/MOXy | XML-Bindung der E-Rechnung |
| Bouncy Castle | kryptografische PDF/XML-Laufzeitunterstützung |
| Jakarta Mail/Angus | SMTP und MIME |
| ZXing/QRGen | QR-Code-Erzeugung |
| qrbill-generator | Swiss-QR-Rechnung |
| SLF4J/Logback | Logging |
| JNA | native Integrationshilfen |
| JUnit 5/Mockito | Tests |

## 10. Build, Paketierung und Betrieb

### 10.1 Build

Der Parent baut Eclipse-Plug-ins, Features, Produkt und P2-Repository mit Tycho. Voraussetzungen:

- JDK 17,
- Maven mindestens 3.9,
- Zugriff auf die in Target/POM definierten Repositories,
- plattformspezifisches Profil.

Profile existieren für Windows und weitere Zielplattformen im Parent-POM. `com.sebulli.fakturama.site` enthält `.product`, Installationsressourcen, Lizenz und App-Paketierung. Das Ergebnis ist keine einzelne Fat-JAR, sondern ein Eclipse-RCP-Produkt mit Bundles, Launcher und Plattformfragmenten.

### 10.2 Konfiguration und Datenablage

Der Betrieb benötigt:

- Eclipse-Workspace/Instanzpfad,
- Präferenzspeicher,
- Datenbankkonfiguration,
- Vorlagen- und Dokumentverzeichnisse,
- optional Mail-/Shop-/Office-Konfiguration,
- Logausgabe,
- Backupziel.

Die lokale HSQL-Komponente enthält einen `BackupManager`. Für einen produktiven Nachbau sind zusätzlich Wiederherstellungstest, konsistente Sicherung externer Dokumentdateien und Versionierung der DB-Migrationen erforderlich.

### 10.3 Logging

SLF4J/Logback wird OSGi-tauglich gebündelt. Ein Fakturama-Logger adaptiert zusätzlich Eclipse-/EclipseLink-Kontexte. Logs müssen Start, DB-Migration, Import/Export und externe Connectorfehler mit Ursachen enthalten, dürfen aber keine Passwörter oder vollständigen vertraulichen Payloads protokollieren.

## 11. Sicherheits-, Datenschutz- und Qualitätsbewertung

### 11.1 Schutzbedarf

Die Anwendung verarbeitet personenbezogene Daten, Adressen, Bankverbindungen, Steuerinformationen, Rechnungs- und Umsatzdaten. Daraus folgen:

- Zugriffsschutz auf Workspace, DB und Dokumentverzeichnisse,
- verschlüsselte Transportwege für DB, SMTP und Webshop,
- geschützte Secrets,
- Backupverschlüsselung,
- Lösch-/Aufbewahrungskonzept,
- Protokollierung ohne unnötige personenbezogene Inhalte.

### 11.2 Technische Risiken

- Desktop-/Workspacezustand erschwert parallele Nutzung.
- Externe MySQL/MariaDB macht aus der UI noch keine konfliktfreie Mehrbenutzeranwendung.
- Optimistische Sperrung muss konsequent behandelt werden.
- ODT-/Officeautomation ist von lokaler Officeinstallation und Vorlagenqualität abhängig.
- Browser-Formularintegration kann bei Webseitenänderungen brechen.
- Webshop-Connectoren sind shopspezifisch und teilweise Legacy.
- Geldberechnung darf nicht zwischen UI, Export und E-Rechnung divergieren.
- Soft Delete und historische Referenzen müssen in jeder Abfrage korrekt berücksichtigt werden.
- E-Rechnungsstandards entwickeln sich weiter; der vorliegende Stand ist nicht automatisch für zukünftige gesetzliche Anforderungen ausreichend.

### 11.3 Tests

Vorhanden sind Modell-, Common- und RCP-Testbundles mit JUnit 5 und Mockito, aber der Produktionscode ist deutlich größer als der sichtbare Testumfang. Für einen Nachbau sind mindestens erforderlich:

- Golden-Master-Tests für Netto/Brutto, Rabatt, Versand und alle Steuersätze,
- Dokumenttyp- und Konvertierungstests,
- DB-Migrationstests von jedem unterstützten Ausgangsschema,
- DAO-Tests auf allen drei DB-Systemen,
- Importtests für Locale-/CSV-Varianten,
- Snapshot-Tests für ODT/PDF/XML,
- Schema-/Validator-Tests der E-Rechnung,
- Idempotenztests für Webshopimporte,
- SMTP-/HTTP-Adaptertests mit Testservern,
- UI-Smoke-Tests für Save/Close/Dirty-State.

## 12. Rekonstruktionsplan für einen funktionsgleichen Nachbau

### Phase 1: Verträge einfrieren

Vor Implementierung sind festzuhalten:

- alle Entitäten, Felder, Nullregeln, Defaults und Enumerationswerte,
- Dokumenttyp-IDs und standardisierte Codes,
- Geld-/Rundungsregeln,
- Nummernformate,
- Soft-Delete-/Auditsemantik,
- Import-/Exportformate,
- ODT-Platzhalter,
- E-Rechnungsschema und Profile,
- Webshop-Payloads und Statusmapping.

Diese Werte sind Kompatibilitätsverträge. Umbenennen in der neuen UI ist möglich; semantisches Zusammenlegen ist riskant.

### Phase 2: Domäne und Datenbank

Empfohlene Reihenfolge:

1. gemeinsame Entitätsbasis und Kategorien,
2. Steuer, Zahlung, Versand, Kontenarten,
3. Kontakt/Adresse/Bankkonto,
4. Produkt/Option/Staffelpreis,
5. Dokumenthierarchie/Position/Empfänger,
6. Belege/Belegpositionen,
7. Benutzer/Mandant/Webshop,
8. Migration und Seed-/Defaultdaten.

Die neue Schemaimplementierung sollte migrationsfähig bleiben. Ein einmaliges „Schema aus ORM generieren“ reicht nicht.

### Phase 3: Fachservices

Zuerst ohne UI implementieren und testen:

- Money-/Rundungsservice,
- Dokument- und Steuersummen,
- Nummerngenerator,
- Dokumentumwandlung,
- Fälligkeit/Zahlstatus,
- Lagerbestandsbewegung,
- Adress-Snapshot,
- Soft Delete und Audit,
- Such-/Filterservices.

### Phase 4: UI

Danach:

- Navigation und Listen,
- Stammdateneditoren,
- Dokumenteditor mit Positionsgrid,
- Belegeditor,
- Einstellungen,
- Auswahl-/Suchdialoge,
- Dirty-/Save-/Close-Verhalten,
- Rollen-/Berechtigungsregeln soweit tatsächlich genutzt.

### Phase 5: Dokumentpipeline

In einer isolierten Pipeline:

```text
Persistiertes Dokument
 -> unveränderliches Ausgabe-DTO
 -> formatierte Platzhalter/Tabellen
 -> ODT
 -> PDF
 -> optional E-Rechnungs-XML + PDF/A-Einbettung
 -> Ablage
 -> optional Mail
```

Damit erzeugen UI, Druck und E-Rechnung dieselben Beträge.

### Phase 6: Integrationen

Adapter nacheinander:

1. CSV-Import/-Export,
2. ODS/vCard,
3. SMTP,
4. QR,
5. Webshop,
6. Paketdienst,
7. P2-Update oder Ersatzmechanismus.

Jeder Adapter braucht einen klaren Port, Konfiguration, Timeout, Fehlerobjekt und Integrationstest.

### Phase 7: Migration und Abnahme

Eine Vergleichsmigration muss:

- Datensatzanzahlen je Entität vergleichen,
- Summen über Rechnungen/Belege vergleichen,
- Fremdschlüssel und Dokumentketten prüfen,
- Dateien/Pfade auflösbar machen,
- Stichproben vollständig rendern,
- Webshop-IDs und Nummernkreise erhalten.

## 13. Abnahmekriterien

Ein Nachbau ist erst funktional gleichwertig, wenn:

- alle neun Dokumenttypen angelegt, geändert, gesucht, gedruckt und korrekt umgewandelt werden,
- Beträge für repräsentative Bestandsdaten centgenau übereinstimmen,
- Kontakt- und Produkt-Snapshots historische Dokumente nicht verändern,
- Nummern bei Parallelzugriff eindeutig bleiben,
- HSQLDB-Daten verlustfrei migriert werden können,
- MariaDB/MySQL-Betrieb mit Transaktions- und Konflikttests funktioniert,
- alle registrierten Import-/Exportassistenten fachlich ersetzt sind,
- ODT/PDF und E-Rechnung dieselben Summen enthalten,
- Mailanhänge, QR-Daten und Webshopstatus korrekt sind,
- Soft-Delete-, Gültigkeits- und Auditfelder erhalten bleiben,
- fehlerhafte externe Operationen keine halbfertigen fachlichen Zustände hinterlassen.

## 14. Präzise Grenzen der Analyse

Der Quellcode belegt die vorhandenen Klassen und Integrationspfade. Nicht automatisch belegt sind:

- dass jeder historische Webshop-Connector im ausgelieferten Produkt aktiv ist,
- dass jede Klasse über das aktuelle e4-Modell erreichbar ist,
- dass jedes E-Rechnungsprofil zum Analysezeitpunkt rechtlich aktuell ist,
- dass Mandanten- und Rollenklassen eine vollständig durchgesetzte Mehrmandanten-/Berechtigungsarchitektur ergeben,
- dass alle `target`-Artefakte exakt aus dem aktuellen Working Tree erzeugt wurden.

Für eine 1:1-Reimplementierung sind deshalb zusätzlich Laufzeittests mit einer realen Fakturama-Installation, Beispieldatenbank, Vorlagen und Exportreferenzen nötig. Die vorliegende Spezifikation ist die detaillierte statische Referenz; dynamische Golden Masters ergänzen sie.

## 15. Quellcode-Landkarte

Die wichtigsten Einstiege für eine Fortsetzung der Analyse sind:

- Build: `fakturama-2/Fakturama-Parent/pom.xml`
- Produkt: `Fakturama-Parent/com.sebulli.fakturama.site/com.sebulli.fakturama.product`
- UI-Modell: `Fakturama-Parent/com.sebulli.fakturama.rcp/Application.e4xmi`
- Haupt-UI: `Fakturama-Parent/com.sebulli.fakturama.rcp/src/main/java`
- Modell: `Fakturama-Parent/com.sebulli.fakturama.model/src/main/java/com/sebulli/fakturama/model`
- Persistenz: `Fakturama-Parent/com.sebulli.fakturama.model/META-INF/persistence.xml`
- Migrationen: `Fakturama-Parent/com.sebulli.fakturama.model/src/main/resources/changelog`
- gemeinsame Konstanten: `Fakturama-Parent/com.sebulli.fakturama.common/src/main/java/com/sebulli/fakturama/misc/Constants.java`
- Dokumenttypen: `.../common/.../misc/DocumentType.java`
- DAOs: `.../rcp/.../dao`
- Berechnung: `.../rcp/.../calculate`
- Dokumenteditor: `.../rcp/.../parts/DocumentEditor.java`
- Vorlagen: `.../rcp/.../office`
- Import: `Fakturama-Parent/org.fakturama.import/src/main/java`
- Export: `Fakturama-Parent/org.fakturama.export/src/main/java`
- E-Rechnung: `Fakturama-Parent/org.fakturama.exporter.zugferd/src/main/java`
- Mail: `Fakturama-Parent/org.fakturama.connectors/src/org/fakturama/connectors/mail`
- Webshop: `.../rcp/.../webshopimport`
- QR: `Fakturama-Parent/org.fakturama.qrcode`
- Altversion: `.../model/.../oldmodel` und `.../rcp/.../migration`

## 16. Schlussfolgerung

Der fachliche Kern von Fakturama ist ein konsistentes Netz aus Stammdaten, Dokumenthistorie, Berechnung, Ausgabe und Integrationen. Die sichtbare Rechnungsmaske ist nur der Einstieg. Ein erfolgreicher Nachbau muss insbesondere Dokumentvererbung, Empfänger-Snapshots, Geldrundung, Steuergruppen, Nummernkreise, Soft Delete, DB-Migration, Vorlagen und externe Idempotenz als erstklassige Domänenprobleme behandeln.

Die sinnvollste moderne Zielarchitektur wäre weiterhin modular, auch wenn Eclipse RCP ersetzt wird: ein frameworkunabhängiger Domänenkern, transaktionale Persistenzports, unveränderliche Ausgabe-DTOs und getrennte Adapter für Office, PDF/E-Rechnung, Mail, Webshop, QR und Dateien. Damit lässt sich der vorhandene Funktionsumfang erhalten, ohne die stärkste historische Kopplung – UI, OSGi-Kontext und Persistenz im selben Bundleverbund – erneut zu übernehmen.

## 17. Feldinventar der wichtigsten Persistenzobjekte

Dieser Abschnitt benennt die im Quellmodell tatsächlich vorhandenen fachlichen Felder. `null` ist dabei häufig von `false` oder `0` unterscheidbar. Ein Nachbau sollte Nullwerte daher nicht ohne Migrationsregel durch primitive Defaults ersetzen.

### Contact

`alias`, `categories`, `company`, `customerNumber`, `title`, `firstName`, `gender`, `birthday`, `addresses`, `discount`, `payment`, `reliability`, `useNetGross`, `vatNumber`, `vatNumberValid`, `website`, `webshopName`, `supplierNumber`, `gln`, `mandateReference`, `bankAccount`, `useSalesEqualizationTax`, `note`, `registerNumber` plus Basisfelder.

Bemerkenswert sind GLN, Mandatsreferenz, USt-ID-Prüfstatus und Verkaufsausgleichsteuer. Diese Felder werden in einer reduzierten Kontaktimplementierung leicht übersehen, sind aber für E-Rechnung, SEPA und bestimmte Steuerfälle relevant.

### Address

`street`, `cityAddon`, `city`, `zip`, `countryCode`, `contact`, `localConsultant`, `email`, `mobile`, `phone`, `additionalPhone`, `fax`, `contactTypes`, `addressAddon` plus Basisfelder.

Kommunikationsdaten liegen damit zumindest teilweise an der Adresse, nicht ausschließlich am Kontakt. Das erlaubt standortbezogene Ansprechpartnerdaten.

### BankAccount

`accountHolder`, `bankCode`, `bankName`, `bic`, `iban` plus Basisfelder. Die Kontonummer ist im modernen Modell durch IBAN repräsentiert; `bankCode` bleibt als Integer-Altfeld erhalten.

### Document

`additionalInfo`, `addressFirstLine`, `billingType`, `customerRef`, `deposit`, `documentDate`, `dueDays`, `invoiceReference`, `items`, `itemsRebate`, `message`, `message2`, `message3`, `netGross`, `noVatReference`, `odtPath`, `orderDate`, `paidValue`, `paid`, `payDate`, `payment`, `pdfPath`, `printed`, `printTemplate`, `progress`, `receiver`, `serviceDate`, `shipping`, `shippingAutoVat`, `shippingValue`, `sourceDocument`, `tara`, `totalValue`, `transactionId`, `webshopDate`, `webshopId`, `vestingPeriodStart`, `vestingPeriodEnd`, `version` plus Basisfelder.

`tara` ist ein zusätzliches Gewichts-/Verpackungsfeld. `additionalInfo` ist ein eigenes Objekt, nicht nur ein Freitext. Drei getrennte Nachrichtenfelder spiegeln die Positionierung in der Vorlage. `receiver` ist eine Liste, wodurch verschiedene Empfänger-/Adressrollen möglich bleiben.

### DocumentItem

`noVat`, `itemRebate`, `itemNumber`, `product`, `quantity`, `originQuantity`, `weight`, `optional`, `picture`, `price`, `quantityUnit`, `itemVat`, `gtin`, `posNr`, `itemType`, `vestingPeriodStart`, `vestingPeriodEnd`, `supplierItemNumber`, `description` plus Basisfelder.

`originQuantity` erhält die Ausgangsmenge neben der bearbeiteten Menge. GTIN und Lieferantenartikelnummer unterstützen elektronischen Austausch. Ein Leistungszeitraum kann pro Position vorliegen, nicht nur am Dokument.

### DocumentReceiver

`alias`, `customerNumber`, `consultant`, `company`, `title`, `firstName`, `street`, `cityAddon`, `city`, `zip`, `countryCode`, `manualAddress`, `billingType`, `originContactId`, `originAddressId`, `gender`, `email`, `mobile`, `phone`, `fax`, `supplierNumber`, `gln`, `mandateReference`, `vatNumber`, `description`, `nameAddon` plus Basisfelder.

Die `origin...Id`-Felder erlauben Rückverfolgung zum Stammobjekt, während die übrigen Felder den unveränderlichen Dokument-Snapshot darstellen. `manualAddress` unterstützt eine vollständig manuell formatierte Anschrift.

### Product

`block1` bis `block5`, `categories`, `attributes`, `picture`, `itemNumber`, `supplierItemNumber`, `price1` bis `price5`, `quantity`, `quantityUnit`, `sellingUnit`, `vat`, `webshopId`, `weight`, `gtin`, `costPrice`, `allowance`, `blockPrices`, `cdf01` bis `cdf03`, `note`, `description` plus Basisfelder.

Neben der Liste moderner Blockpreise existieren fünf feste Mengen-/Preisstaffeln. `cdf01..03` sind kundendefinierbare Felder. Ein kompatibler Import muss beide Staffelpreisrepräsentationen berücksichtigen.

### Payment

`discountDays`, `discountValue`, `netDays`, `category`, `paidText`, `unpaidText`, `depositText`, `code`, `description` plus Basisfelder. Zahlungsarten steuern somit nicht nur Termine, sondern auch zustandsabhängige Ausgabetexte.

### Shipping

`autoVat`, `shippingValue`, `shippingVat`, `categories`, `code`, `description` plus Basisfelder. `autoVat` und `shippingVat` dürfen nicht verwechselt werden: das erste ist die Berechnungsstrategie, das zweite eine konkrete Steuerreferenz.

### VAT

`taxValue`, `category`, `salesEqualizationTax`, `description`, `code` (`UNTDID5305`) plus Basisfelder.

### Voucher und VoucherItem

Beleg: `account`, `voucherDate`, `discounted`, `doNotBook`, `documentNumber`, `items`, `voucherNumber`, `paidValue`, `totalValue`, `voucherType`.

Position: `accountType`, `vat`, `price`, `posNr`, `itemVoucherType`.

`doNotBook` ist ein expliziter Ausschlussstatus. Dokumentnummer und Belegnummer sind zwei verschiedene Identifikatoren. Der Belegtyp existiert sowohl am Kopf als auch an der Position.

### WebShop und WebshopStateMapping

Webshop: `webshopVendor`, `webshopVersion`, `stateMapping`. Mapping: `webshopState`, `fakturamaOrderState`. Verbindungsdaten liegen überwiegend in Präferenzen; die DB-Entität beschreibt Hersteller, Version und Statusabbildung.

### Gemeinsame Basis

Nahezu jede Entität wiederholt `name`, `dateAdded`, `modifiedBy`, `modified`, `id`, `deleted`, `validFrom`, `validTo`; Dokumente besitzen zusätzlich ein explizites `version`. Die Wiederholung stammt aus dem generierten EMF/Texo-/JPA-Modell und ist für Tabellen- und Migrationskompatibilität relevant.

## 18. Webshop-Protokoll auf Request-Ebene

Der aktuelle `WebShopConnector` erzeugt POST-Anfragen an eine konfigurierte `scriptURL`. Der Body ist `application/x-www-form-urlencoded` in UTF-8 und enthält immer:

- `username`,
- `password`,
- `action`,
- anschließend beliebige aktionsspezifische Schlüssel/Werte.

Zusätzlich kann HTTP Basic Authentication gesetzt werden. Deren Benutzer/Passwort sind getrennt von den drei Formfeldern. Öffentliche GET-Anfragen können ebenfalls diesen Authorization-Header erhalten. Der ältere `URLConnection`-Pfad:

- akzeptiert auch `file://` als Quelle,
- aktiviert Input,
- verwendet 4.000 ms Connect-Timeout,
- aktiviert Output außer für `file://`,
- setzt bei Bedarf Basic Auth.

Diese Zweiteilung bedeutet, dass ein Nachbau zwei Zugangsebenen abbilden muss: Webserver-Schutz und applikationsseitiger Shop-Login. Passwörter im Formularbody setzen zwingend HTTPS voraus. Die `action` ist der eigentliche RPC-Selektor; die Schnittstelle ist also formularbasierter Remote Procedure Call und keine ressourcenorientierte REST-API.

`IWebshopConnection` zeigt zusätzlich den Prozessvertrag: Ausführung mit vorbereitender Aktion, Ergebnistext, Flags für Produkt-/Bestellabruf, persistente Menge noch zu synchronisierender Aufträge, Zugriff auf alle beteiligten DAOs, Eclipse-Kontext und ein generisches Datenobjekt. Für einen sauberen Nachbau sollte dieses breite Interface in Transport, Import-Use-Case, Repositoryports und Synchronisationsspeicher zerlegt werden.

## 19. Reproduzierbarkeit

Der analysierte Git-Commit des eingebetteten Fakturama-Repositories ist `768b78e6`. Bereits vor beziehungsweise unabhängig von dieser Dokumentation waren dort zwei lokale Änderungen sichtbar:

- `Fakturama-Parent/.settings/org.eclipse.m2e.core.prefs`
- `org.fakturama.migtool/.factorypath`

Sie wurden durch die Analyse nicht verändert. Die Dokumentation wurde aus dem Arbeitsbaum gelesen und kann daher in Randfällen Inhalte dieser lokalen Konfigurationsänderungen widerspiegeln; der analysierte Produktionsquellcode selbst war davon nicht betroffen.
