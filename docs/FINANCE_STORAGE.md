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

Die optionale FinTS-Bibliothek wird über das Extra `banking` installiert. python-fints
ab Version 4 verlangt eine bei der Deutschen Kreditwirtschaft registrierte Produkt-ID;
diese muss als `SIMPLEOFFICE_FINTS_PRODUCT_ID` in der vertrauenswürdigen
Laufzeitkonfiguration des Dienstes hinterlegt werden. Sie ist eine Produktkennung,
keine Benutzer-PIN oder TAN. Ohne gültige Produkt-ID starten FinTS-Abrufe nicht und
zeigen eine entsprechende Konfigurationsmeldung.

Fordert die Bank für einen ausdrücklich gestarteten Saldoabruf eine TAN oder App-Freigabe,
kann dieser einzelne lesende Vorgang fortgesetzt werden. Der kurzlebige python-fints-Client-,
Dialog- und Retry-Zustand wird mit `SECRET_KEY` serverseitig verschlüsselt gespeichert,
an den angemeldeten Benutzer, die Bankverbindung und das zugeordnete Konto gebunden und
nach fünf Minuten gelöscht. Der zufällige Fortsetzungstoken wird nur gehasht gespeichert
und kann einmalig verwendet werden. Je Benutzer sind höchstens drei und insgesamt höchstens
100 Freigaben offen. Der Token wird nur als CSRF-geschütztes Formularfeld übertragen. Für
die Fortsetzung müssen PIN und gegebenenfalls TAN erneut eingegeben werden; beide bleiben
request-lokal und werden weder im Zustand noch in der Datenbank gespeichert. App-Freigaben
übermitteln keine TAN. Der gelesene Saldo bleibt flüchtig und erzeugt keine Buchung.

Dieser SCA-Resume gilt ausschließlich für den manuellen Kontostandsabruf, einschließlich
einer eventuell dafür benötigten Kontodiscovery. SCA-Anforderungen bei Dialoginitialisierung,
Umsatzimport oder anderen FinTS-Vorgängen werden nicht umgangen und bleiben fail-closed.
Deaktivierte Bankverbindungen dürfen keine Kontostands-, Kontoermittlungs- oder
Umsatzabrufe starten. Kontoauszüge können weiterhin manuell über CSV, CAMT oder MT940
importiert werden.

Regressionstests für Neuinstallation, additive Aktualisierung, erneutes Öffnen,
Mandantentrennung und Wiederholbarkeit:

```sh
python -m unittest tests.test_finance_store
```
