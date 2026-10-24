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

## FinTS-Kontostände und Umsatzabruf

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

Beim ausdrücklich gestarteten Kontenabruf liest die Oberfläche zusätzlich die von
python-fints bereitgestellten Bank-/Kontofähigkeiten für Salden und Umsätze sowie die
unterstützten TAN-Verfahren aus. Angezeigt werden nur diese begrenzten Lesefähigkeiten
und TAN-Verfahrensnamen/-Kennungen mit Versions-/Prozessangaben. Pro-Konto-Fähigkeiten
werden nur mit der bereits für den Kontenabruf verwendeten Remote-Konto-ID verbunden;
Besitzernamen und rohe Konto-/Bankparameter werden nicht in das Fähigkeitsprofil übernommen.
Das Profil wird nicht persistiert.
Fehlende Angaben werden als „nicht ermittelt“ angezeigt und nicht als Unterstützung
ausgegeben. Meldet die Bank für ein Konto eine Leseoperation ausdrücklich als nicht
unterstützt, wird der Abruf vor dem entsprechenden Bibliotheksaufruf abgelehnt; die
gültige Bankverbindung bleibt für andere unterstützte Lesezugriffe nutzbar. Die
Fähigkeitsantwort ist `no-store`.

Fordert die Bank für einen ausdrücklich gestarteten Saldo- oder Umsatzabruf eine TAN oder
App-Freigabe, kann der einzelne lesende Vorgang fortgesetzt werden. Das umfasst auch eine
TAN-Herausforderung beim Abruf der Kontenliste. Der kurzlebige python-fints-Client-, Dialog-
und Retry-Zustand wird mit `SECRET_KEY` serverseitig verschlüsselt gespeichert und an den
angemeldeten Benutzer, die Bankverbindung und die aktuelle lokale/entfernte Kontozuordnung
gebunden. Transaktionsfreigaben binden außerdem den ursprünglichen Abrufzeitraum sowie die
bereits erfolgreich synchronisierten Konten, damit ein Resume keine Kontoänderung übernimmt
und diese Konten nicht erneut abruft. Der unveränderte Umsatzzeitraum ist auf höchstens 370
Tage begrenzt.

Herausforderungen verfallen nach fünf Minuten. Der decoupled App-Freigabemodus wird separat
im authentifizierten Payload gehalten, da python-fints ihn nicht im Retry-Blob serialisiert.
Der zufällige Fortsetzungstoken wird nur gehasht gespeichert und kann einmalig verwendet
werden. Je Benutzer sind höchstens drei und insgesamt höchstens 100 Freigaben offen. Der
Token wird ausschließlich als CSRF-geschütztes Formularfeld übertragen. Für die Fortsetzung
müssen PIN und gegebenenfalls TAN erneut eingegeben werden; beide bleiben request-lokal und
werden weder im Zustand noch in der Datenbank gespeichert. App-Freigaben übermitteln keine
TAN. Umsatzdaten werden erst nach vollständiger Rückgabe und Normalisierung für das jeweilige
Konto importiert; davor erfolgreich abgeschlossene Konten bleiben erhalten und werden beim
Resume übersprungen.

Andere FinTS-Operationen bleiben fail-closed; insbesondere werden keine Zahlungsaufträge
ausgeführt. Deaktivierte Bankverbindungen dürfen keine Kontostands-, Kontoermittlungs- oder
Umsatzabrufe starten. Kontoauszüge können weiterhin manuell über CSV, CAMT oder MT940
importiert werden. Das additive Datenbankschema migriert bestehende SCA-Challenge-Zeilen beim
regulären Öffnen, ohne gespeicherte Challenges zu verwerfen.

Regressionstests für Neuinstallation, additive Aktualisierung, erneutes Öffnen,
Mandantentrennung und Wiederholbarkeit:

```sh
python -m unittest tests.test_finance_store
```

## Browsergestützter Santander-Export

Der Browser-Export ist als sichtbarer, nutzerinitiierter Ablauf konfiguriert. Die
Bankbrowser-Einstellung, Export-Timeout, Importlimit und Vorschau-Aufbewahrung werden
im Finance-Settings-Store gespeichert und revisionssicher auditiert. Das Timeout beträgt
standardmäßig 300 Sekunden und kann zwischen 30 und 1800 Sekunden eingestellt werden.
Das Importlimit beträgt standardmäßig 25 MiB; Administratoren können es auf 1 bis 25 MiB
begrenzen. 25 MiB bleibt zusätzlich die feste Parser-Obergrenze. Vorschauen werden nach
24 Stunden gelöscht; die Aufbewahrung ist zwischen 1 Stunde und 7 Tagen einstellbar und
wird auch beim Import-Commit geprüft. Änderungen gelten ohne Neustart. Ein Browser kann
nur lokal auf dem ausführenden Rechner gestartet werden; bei Serverbetrieb öffnet er sich
nicht auf einem entfernten Client. Die Einstellungen lassen sich revisionssicher auf ihre
Standardwerte zurücksetzen. Importformate bleiben auf CSV, CAMT.052/CAMT.053 und MT940
beschränkt.
Login und Bankfreigabe bleiben bei Santander, Cookies und Bankzugangsdaten werden nicht
dauerhaft gespeichert. Der Download wird vor Vorschau und bestätigtem Import geprüft.
