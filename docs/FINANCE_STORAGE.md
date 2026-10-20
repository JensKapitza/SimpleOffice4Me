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

## FinTS-Kontostände

Nach Konfiguration einer FinTS-Bankverbindung, dem Kontenabruf und der manuellen
Zuordnung eines Bankkontos zu einem lokalen Konto kann die Finance-Oberfläche einen
Kontostand ausdrücklich und nur lesend abrufen. Die PIN wird für den einzelnen
POST-Aufruf benötigt, nicht gespeichert und nicht in die Antwort aufgenommen. Die
Saldoantwort wird mit `no-store` ausgeliefert und nicht als Buchung persistiert. Der
gebuchte Saldo wird angezeigt; ein verfügbarer Saldo erscheint nur, wenn die Bank bzw.
die FinTS-Bibliothek ihn separat liefert.

Die optionale FinTS-Bibliothek wird über das Extra `banking` installiert. Fordert die
Bank eine TAN oder App-Freigabe, wird der Abruf sicher beendet; TAN-Eingabe und
Wiederaufnahme eines Challenge-Dialogs sind derzeit nicht implementiert. Deaktivierte
Bankverbindungen dürfen keine Kontostands-, Kontoermittlungs- oder Umsatzabrufe starten.
Kontoauszüge können weiterhin manuell über CSV, CAMT oder MT940 importiert werden.

## Browsergestützter Santander-Export

Der Browser-Export ist als sichtbarer, nutzerinitiierter Ablauf konfiguriert. Die
Bankbrowser-Einstellung und ihr Timeout werden im Finance-Settings-Store gespeichert
und revisionssicher auditiert. Der Standardwert beträgt 300 Sekunden; Administratoren
können 30 bis 1800 Sekunden wählen. Der Zeitwert gilt ab dem nächsten Export und
erfordert keinen Neustart. Ein Browser kann nur lokal auf dem ausführenden Rechner
gestartet werden; bei Serverbetrieb öffnet er sich nicht auf einem entfernten Client.
Login und Bankfreigabe bleiben bei Santander, Cookies und Bankzugangsdaten werden nicht
dauerhaft gespeichert. Der Download wird vor Vorschau und bestätigtem Import geprüft.

Regressionstests für Neuinstallation, additive Aktualisierung, erneutes Öffnen,
Mandantentrennung und Wiederholbarkeit:

```sh
python -m unittest tests.test_finance_store
```
