# FinTS-Banking: unterstützte Banken, Limits und Sicherheitsmodell

## Unterstützte Banken

Jede Bank mit FinTS/HBCI-PIN/TAN-Zugang über die Bibliothek `fints`
(`fints>=4.2,<6`). TAN-Verfahren (z. B. photoTAN, pushTAN, chipTAN,
Ein-Schritt-PIN) werden pro Bank aus den gemeldeten Fähigkeiten erkannt;
das aktive Verfahren wird in der UI benannt. Meldet eine Bank einen
Lesezugriff als nicht unterstützt, wird die Funktion mit verständlicher
Meldung deaktiviert statt still zu scheitern.

## Funktionsumfang (lesend)

- Konten entdecken, Kontostände und verfügbare Salden abfragen.
- Umsätze mit Zeitraum/Pagination abrufen; idempotente Buchungen mit
  Dublettenerkennung (`duplicate_state`), Wiederholungen mit Backoff.
- Rechnungsabgleich ausschließlich als Vorschlag mit Begründung; die
  Zuordnung braucht eine ausdrückliche Bestätigung und prüft, dass der
  Vorschlag noch aktuell ist.
- Mapping-Hinweise für unverknüpfte Verbindungen (nur lesend).

## Grenzen

- **Strikt lesend:** Der Adapter enthält keine Transfer-Methoden; es gibt
  keine SEPA-Überweisung, keinen Zahlungsauftrag, keine Depotabfrage.
- **Keine Banktransaktion ohne aktive Nutzerbestätigung** – schon deshalb,
  weil schreibende Transaktionen gar nicht existieren.
- SCA-Sitzungen sind kurzlebig: Challenge-Zustand begrenzt (120 KiB),
  Einmal-Token mit Verbrauch (`consume`), Sitzungsbindung an
  Vorgang/Konto/Zeitraum/Schritt.

## Sicherheitsmodell

- PIN/TAN sind request-lokale Geheimnisse: keine Speicherung in DB,
  Logs, API-Antworten oder dauerhaften Sitzungen. Die Ablage verweigert
  Felder mit verbotenen Bezeichnern (`pin`, `tan`, `password`, `secret`)
  per ValueError; Verbindungs-Metadaten enthalten nur Kennungen ohne
  Geheimnisse.
- Challenge-Zustände liegen begrenzt und verschlüsselt im Store und
  werden einmalig verbraucht; abgelaufene/fremde Token schlagen fehl.
- Audit-Einträge enthalten Vorgangs-IDs und Fehlerklassen, keine
  Geheimnisse und keine vollständigen Nutzdaten.
- Timeouts, Wiederholungsgrenzen und klare Fehlermeldungen bei
  nicht unterstützten Bankfunktionen (fail-closed).

## Testen ohne Bank

144 Tests laufen gegen `MockBankClient` (Konten, Salden, Fähigkeiten,
keine Transfers): TAN-Status, Timeouts, Fehler, Dubletten,
Berechtigungen und Geheimnisfreiheit. Keine produktive Bankverbindung
nötig. Siehe `tests/test_finance_fints*.py`.
