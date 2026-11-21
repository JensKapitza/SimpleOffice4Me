# E-Rechnungen in SimpleOffice4Me – Bestands- und Gap-Analyse (AP1)

Stand: 21.11.2026 · Auftrag: XRechnung-Erzeugung, Validierung, Nachweisführung
Basis: `origin/main`, Branch `feature/e-invoicing-xrechnung` · Kein Auto-Merge.

Grundsatz des Auftrags: **ein Rechnungsobjekt, eine Rechnungsnummer, mehrere Ausgabeformate.**
Diese Analyse folgt strikt Phase 1: Es wurden ausschließlich Bestand geprüft und
Lücken dokumentiert; es gab keine Verhaltensänderung.

## 1. Rechnungsmodell und Datenfelder (vorhanden)

Quelle: `app/business_documents.py` (`_invoice_row_from_form`,
`save_invoice_draft`, `finalize_invoice`), `app/business_document_generation.py`.

- Rechnungsobjekt als JSON-Datensatz mit `invoice_id` (UUID), `invoice_number`,
  `contact_id`, `issue_date`, `service_date`, `due_date`, `currency`,
  `payment_terms`, `seller` (Snapshot inkl. Bankverbindung via
  `invoice_seller_snapshot`), `buyer` (Snapshot inkl. Adresse, USt-IdNr.),
  `lines`, `totals`, `status`, `payments`, `history`, `template_id`,
  `reference_invoice_id/_number`, `task_id`, `settlement`, Zeitstempel/Akteure.
- Positionen mit Menge/Einheit (UN/CEFACT-Codes, `INVOICE_UNITS`), Netto-Einzelpreis,
  Steuersatz, Summen; Summenbildung ausschließlich mit `Decimal`
  (`_build_invoice_lines`, `_invoice_totals`, inkl. `vat_groups` je Steuersatz).
- Statusmodell: `draft` → `open`/`partial`/`paid`/`overdue`/`credited`/
  `written_off` (`invoice_state`, ohne Snapshot-Mutation); Zahlungen,
  Gutschriften (eigener Nummernkreis `GS-`, mit Originalreferenz),
  Ausbuchungen, Kundenkredit-Verrechnung.
- Entwurfs-Schutz: Versionskonflikt-Prüfung (`draft_updated_at`), Bearbeitung
  nur im Draft-Status, Dateisperren, Audit-Historie (`history` + Dokumentspeicher).
- Tests: `tests/test_business_documents.py` (62 Tests), u. a. parallele
  Finalisierung, Referenzpflicht, Entwurfsnummern ohne Sequenzverbrauch.

## 2. Nummernkreis und Finalisierung (vorhanden, zu erweitern)

- Gesperrter Jahres-Nummernkreis `YYYY-NNNN` (`_invoice_number`, Datei-Lock,
  atomares JSON) und separater Gutschriftenkreis; Entwurfsnummern (`DRAFT-…`)
  verbrauchen keine Nummer.
- `finalize_invoice` läuft unter Rechnungs-Lock (nicht-blockierend, erkennt
  Doppelläufe) plus Projekt-Billing-Lock; Fehlschlag protokolliert
  `finalization_failed` mit Timings und lässt einen editierbaren Entwurf zurück
  (Tests vorhanden).
- **Lücke:** Kein eingefrorener Rechnungs-Snapshot als eigenständiges,
  versioniertes Artefakt; der Datensatz bleibt technisch das Arbeitsobjekt
  (Zahlungen/Gutschriften werden angehängt). Für den Auftrag ist ein
  unveränderlicher Finalisierungs-Snapshot mit SHA-256 zu ergänzen, aus dem
  alle Formate erzeugt werden.

## 3. ZUGFeRD-Erzeugung und PDF/A-3 (vorhanden, nicht ersetzen)

- `ZUGFERD_VERSION = "2.5.2"`, `ZUGFERD_PROFILE = "EN16931"`.
- Ablauf: Template-PDF → Ghostscript-PDF/A-3 → CII-XML (`_cii_xml`) einbetten
  (`factur-x.xml`) → Validierung (`_validate_hybrid`: Well-formed,
  veraPDF, Mustang-Hybrid-Validator) → Status (`validated`, sonst
  `pdf_only`/`validation_failed` mit Fehlertext, kein harter Abbruch).
- Das CII-XML enthält bereits: Nummer, Typ 380, Daten, Positionen (Menge,
  Preise, Steuer je Zeile), Verkäufer/Käufer mit Adresse, Verkäufer-USt-IdNr.
  (VA), IBAN/BIC, Steueraufschlüsselung, Zahlungsbedingungen/Fälligkeit, Summen.
- Editor (`templates/documents/invoice_detail.html`) zeigt ZUGFeRD-Status,
  Version/Profil und Validierungsdetails.
- Tests: u. a. `test_einvoice_profile.py`, `test_zugferd_storage_boundary.py`.

## 4. KoSIT-XRechnung-Validator (vorhanden, einbinden)

- `app/xrechnung_validation.py`: Offline-Validierung über gepinnte
  KoSIT-Runtime (`tools/install_xrechnung_validator.py` mit Versionen),
  `defusedxml`-Vorprüfung, 32-MiB-Limit, Timeout 1–300 s, strukturierte
  Ergebnis-Dicts (`validated`/`acceptable`/Grund/Versionen), keine
  Nutzdaten in Logs.
- Tests: `tests/test_xrechnung_validator.py` (9 Tests).
- **Lücke:** Der Validator ist noch nicht in den Rechnungs-Freigabeablauf
  eingebunden (kein XRechnung-Artefakt, kein Freigabe-Gate, keine Anzeige
  im Editor).

## 5. Kunden-, Firmen- und Bankstammdaten (teilweise vorhanden)

- Vorhanden: Firmenstammdaten + Bankkonten (`business_settings`,
  `selected_company_bank_account`), Verkäufer-Snapshot inkl. IBAN/BIC/BlZ,
  Bank, USt-IdNr., Steuernummer; Käufer-Snapshot inkl. strukturierter Adresse
  und USt-IdNr.; Zahlungsbedingungen, Fälligkeit, Währung.
- **Lücken (AP2):** Käuferreferenz BT-10 / Leitweg-ID, elektronische Adressen
  mit Scheme-IDs (Käufer/Verkäufer), Bestell-/Vertragsreferenzen,
  Leistungszeitraum (nur Stichtag `service_date`), Steuerbefreiungsgründe
  und -kategorien über S/Z hinaus, Kontenzuordnung. Pflichtfeldprüfung
  profil-/empfängerabhängig fehlt.

## 6. Buchhaltung und Archivierung (teilweise vorhanden)

- Vorhanden: genau eine Forderung je Rechnung (`invoice_state`,
  `collectible_outstanding`), Zahlungszuordnung, Gutschriften mit
  Originalreferenz, Audit-Historie, Dokumentenablage mit SHA-256-Verifikation
  (`customer_document_archive`, Hash-Abgleich), v3-Finance-Sync
  (`_v3_finance_sync`).
- **Lücken (AP5):** kein einheitliches Rechnungs-Archivpaket (Snapshot +
  ZUGFeRD-PDF + XRechnung-XML + Validierungsergebnisse + Validatorversionen +
  Hashes + Zeitpunkte/Akteure/Versandnachweise); kein Abgleich
  PDF↔XML-Inhalte; kein Validator-Versionsnachweis am Beleg; GoBD-Gesamtkonzept
  ist Organisationsaufgabe (Technik liefert nur Bausteine).

## 7. Gap-Matrix zum Entwicklungsumfang

| AP | Vorhanden | Fehlend | Anzupassen |
|---|---|---|---|
| AP1 Analyse | – | – | dieses Dokument |
| AP2 Datenmodell | Adressen, USt-IdNr., IBAN/BIC, Zahlungsbedingungen, Positionen, Steuersätze | BT-10/Leitweg-ID, E-Adressen+Schemes, Steuerkennungen/-befreiungsgründe, Bestell-/Vertragsrefs, Leistungszeitraum, Kontenzuordnung | Formularfelder + Validierung (keine erfundenen Defaults) |
| AP3 Generator (UBL 2.1) | CII-Bausteine als Referenz, Summenlogik (Decimal) | eigenständiger XRechnung-Export, Namensräume/Kennungen, Rabatt-/Zuschlag-/Rundungsregeln | – (neu, aber auf Snapshot aufsetzend) |
| AP4 Validierung | defusedxml, KoSIT-Wrapper, ZUGFeRD-/PDF-A-Kette, Editor-Statusanzeige | Freigabe-Gate, KoSIT-Anbindung, PDF↔XML-Abgleich, UUID-/Nummernprüfung, feldbezogene Fehler | Editor um XRechnung-Status + Prüfprotokoll erweitern |
| AP5 Archiv/Buchhaltung | Forderung/OP, Zahlungen, Gutschriften, Audit, SHA-Verifikation, v3-Sync | Archivpaket, Validierungsnachweise, Versandnachweise, GoBD-Konzept (org.) | – |
| AP6 UI | Editor, Detail mit ZUGFeRD-Status, Download, Templates | Pflichtanzeige, Formatauswahl, Vorabprüfung, Freigabevorgang, XML-Download, Prüfprotokoll | Editor-Templates + Routen |

## 8. Testplan-Abdeckung (T01–T20)

Bereits abgedeckt (Bestand, zu erhalten): T03 (Nummernkreis-Lock + Tests),
T10 (Fehlerprotokoll + Entwurf), T12 (Gutschriftenkreis + Referenz),
T14 (Berechtigungsprüfungen + 403), T16 (veraPDF/Mustang-Kette),
T18 (62 Bestandstests als Regressionsschirm), T20 (genau ein OP via
`invoice_state`, Zahlungstests). Neu zu bauen: T01/T02/T06–T09/T11
(Generator + Abgleich), T04/T05 (Pflicht-/Steuerlogik), T13 (Hash-Manipulation
am Archivpaket), T15 (KoSIT positiv), T17 (Validator fehlt → kein Erfolg),
T19 (Idempotenz nach Timeout) sowie Negativtests (ungültiges/gefährliches XML,
große Dateien, Validator-Timeout).

## 9. Technische Bewertung und Risiken

- **Kein zweites Rechnungssystem nötig:** Modell, Nummernkreis, Decimal-Logik,
  ZUGFeRD-Kette, KoSIT-Wrapper und Editor-Status sind tragfähige Fundamente.
- **Kritischste Neubauteile:** UBL-Generator mit korrekten Summen-/Steuerregeln
  und der PDF↔XML-Abgleich (Rundungskonsistenz ohne stille Korrekturen).
- **Bekannte Einschränkung:** ZUGFeRD fällt bei fehlenden Werkzeugen/Fehlern
  auf `pdf_only` zurück; für XRechnung gilt strikter: ohne erfolgreiche
  Validierung keine Erfolgsmarkierung (Auftrag AP4).
- **Plattformen:** Linux primär; Windows-Pfad-Tests für neue Dateioperationen
  einplanen; Java-Abhängigkeit (KoSIT/Mustang) dokumentiert als optionale
  Komponente mit ehrlichem Nicht-verfügbar-Status.

## 10. Vorgeschlagene Phasen (Zustimmung vorbehalten)

- Phase 2a: Snapshot-Artefakt + AP2-Felder + Migration (Bestandsdaten bleiben
  lesbar; keine Pflicht nachträglich für alte Belege).
- Phase 2b: UBL-Generator + Summen-/Steuerregeln + Referenzrechnungen.
- Phase 3: Validierungs-Gate + Abgleich + Archivpaket + Editor (AP4–AP6).
- Phase 4: Testplan T01–T20 + Negativtests + Referenzvalidierung.
- Phase 5: Abnahme, PR zur Freigabe (kein Auto-Merge).
