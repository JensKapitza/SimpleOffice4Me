# E-Rechnung: Bedienung und Fehlerbehebung (XRechnung)

Eine Rechnung, eine Nummer, zwei Formate: Das ZUGFeRD-PDF (hybrid, mit
eingebetteter XML) und die XRechnung-XML entstehen aus demselben
eingefrorenen Rechnungsdatensatz. Die Freigabe ist ein bewusster,
protokollierter Akt nach der Finalisierung.

## Ablauf

1. **Entwurf anlegen** wie gewohnt (Positionen, Preise, Steuern).
2. **E-Rechnungswerte ergänzen** (Rechnungseditor):
   - Käuferreferenz (BT-10, z. B. Leitweg-ID) – Pflicht.
   - Elektronische Adresse des Verkäufers (BT-27, Scheme + Kennung) – Pflicht.
   - Optional: E-Adresse des Käufers, Bestell-/Vertragsreferenz,
     Leistungszeitraum, Steuerkategorie je Position, Befreiungsgrund,
     Kontenzuordnung.
3. **Vorabprüfung** (Schaltfläche auf der Rechnungsseite): Prüflauf ohne
   Speicherung – zeigt feldgenau, was noch fehlt oder ungültig ist.
4. **Finalisieren**: vergibt die endgültige Rechnungsnummer, friert den
   Snapshot ein (SHA-256) und erzeugt das PDF.
5. **Freigeben** (nur nach Finalisierung): erzeugt die XRechnung-XML aus
   dem eingefrorenen Snapshot, prüft sie mit dem KoSIT-Validator, gleicht
   die kaufmännischen Inhalte mit dem Archiv-PDF ab und legt XML plus
   Prüfprotokoll revisionssicher ab.
6. **Herunterladen**: PDF (bestehender Download) und XRechnung-XML
   (E-Rechnungs-Karte); **Protokoll** einsehen (Prüfer, Ergebnis, Hashes).

## Grundsätze

- Erneute Freigabe derselben Rechnung liefert dasselbe Ergebnis
  (keine neue Nummer, keine zweite Forderung).
- Korrekturen nach Finalisierung nur über Gutschrift/Korrekturbeleg
  mit eigener Nummer und Originalreferenz – niemals durch Ändern des
  Snapshots.
- Zahlungen werden der einen offenen Forderung zugeordnet; Freigabe und
  Zahlungseingang ändern einander nicht.

## Fehlerbehebung

| Meldung | Ursache | Abhilfe |
|---|---|---|
| Käuferreferenz (BT-10) fehlt | Pflichtfeld leer | Im Entwurf ergänzen, erneut finalisieren, dann freigeben |
| Elektronische Adresse des Verkäufers fehlt | BT-27 unvollständig | Scheme-ID (z. B. EM) und Kennung in den Firmendaten/im Entwurf pflegen |
| USt-IdNr. ungültiges Format | z. B. Tippfehler | Format Ländercode + Zeichen (z. B. DE123456789); Leerzeichen sind erlaubt |
| KoSIT-Validator nicht verfügbar | Java oder Validator fehlt | Java 11+ installieren, `tools/install_xrechnung_validator.py` ausführen; ohne Prüfer gibt es keine Freigabe |
| KoSIT-Prüfung abgelehnt | Regelverstoß in der XML | Protokoll lesen, Feld im Entwurf korrigieren, erneut finalisieren |
| Snapshot-Hash-Abweichung | Datei nachträglich verändert | Freigabe blockiert; Datei aus Backup wiederherstellen, Vorgang dokumentieren |
| PDF↔Abgleich weicht ab | Generator-/Datenfehler | Nicht freigeben; Administrator informieren (Protokoll sichern) |
| Archivierte XRechnung weicht ab (Download blockiert) | XML nachträglich verändert | Download bleibt gesperrt; aus Backup wiederherstellen |
| Zugriff verweigert / nicht gefunden | Keine Kontaktberechtigung | Freigabe durch berechtigte Person anfordern |

## Technische Nachweise (für Prüfer)

- Protokolldatei je Rechnung: Validatorversionen (XRechnung 3.0.2,
  KoSIT 1.6.3, Config 2026-08-31), KoSIT-Ergebnis, SHA-256 von Snapshot,
  XML und Archiv-PDF, Zeitstempel, Benutzer, Prüfschritte.
- Rechnungsverlauf: Ereignisse `snapshot_finalized`, `issued`,
  `einvoice_released`; Audit-Einträge enthalten nur technische
  Minimaldaten (Nummern, Hashes), keine Inhaltsdaten.
- Validatorbasis: siehe `docs/XRECHNUNG_VALIDATION.md`;
  Lückenanalyse: siehe `docs/E_RECHNUNG_GAP_ANALYSE.md`.
