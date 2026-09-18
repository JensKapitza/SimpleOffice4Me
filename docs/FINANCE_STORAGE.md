# Finanzdatenbank: Initialisierung und Updates

Der gemeinsame Finanzkern speichert Daten in
`.simpleoffice-meta/finance.sqlite3` unter dem Dokumentenverzeichnis.
`FinanceStore` initialisiert das Schema beim Öffnen mit `CREATE TABLE IF NOT EXISTS`.
Bestehende Konten, Buchungen und Audit-Einträge bleiben dabei erhalten.

Das Schema umfasst auch wiederkehrende Verpflichtungen (`finance_obligation`),
bestätigte Buchungszuordnungen (`finance_transaction_match`), Bankverbindungsmetadaten
(`finance_bank_connection`) und den Bearbeitungsstand je Steuerjahr (`finance_tax_year`).
Eindeutige Schlüssel sichern wiederholte Zuordnungen und Bankverbindungsregistrierungen
ab. Bankverbindungen enthalten keine PIN, TAN oder Passwörter; entsprechende Eingaben
werden vom Store abgewiesen.

Bei einem Update von einem Stand ohne diese vier Tabellen genügt der nächste reguläre
Zugriff auf den Finanzkern. Die SQLite-Datei nicht löschen oder manuell ersetzen.
Vor Updates die bestehenden Sicherungsverfahren für das Dokumentenverzeichnis verwenden.
Bei Schreib- oder Dateiberechtigungsfehlern müssen die Zugriffsrechte des ausführenden
SimpleOffice-Benutzers geprüft werden.

Regressionstests für Neuinstallation, additive Aktualisierung, erneutes Öffnen,
Mandantentrennung und Wiederholbarkeit:

```sh
python -m unittest tests.test_finance_store
```
