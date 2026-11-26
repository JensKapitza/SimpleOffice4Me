# Buchscanner: private Ausgabe und gezielte Freigabe

Unter **Bibliothek → Buchseiten scannen** wählst du Bilder oder fotografierst mit der Dateiauswahl des Geräts. Sortiere, drehe oder entferne Seiten vor **Privates PDF erstellen**. Die Auswahl bleibt bis zum Export im Browser-Tab. Genutzte Bilder werden nicht in Dokumente importiert. Der PDF-Download ist lokal, ohne Cloud oder OCR. Für den vorhandenen Reader bzw. dessen lokale OCR kannst du die Datei anschließend bewusst in Dokumente importieren. Eine bestehende allgemeine Dokumentfreigabe kann dann auch für dieses importierte Dokument gelten.

Administratoren können ein zuletzt erzeugtes, unverändertes PDF im selben Browser innerhalb von 15 Minuten erneut auswählen, genau einen konfigurierten HTTPS-Peer auswählen und dessen Freigabe ausdrücklich bestätigen. Übermittle dem Administrator des ausgewählten Peers den Angebotscode getrennt. Dieser Peer wählt den Absender und holt die PDF-Kopie bewusst ab; der Code allein ist keine Berechtigung. Der Scanner speichert oder teilt beim Empfänger nichts automatisch weiter.

Die Angebote erscheinen weder im normalen Dokumentenkatalog noch in der Schnellsuche oder im S3-Overlay. Die lokalen V1-Snapshots sind an den Besitzer und den ausgewählten Peer gebunden; ein S3-Zugang ersetzt weder die Peer-Berechtigung noch die Prüfung auf Ablauf und Widerruf. Sie sind eine Stunde verfügbar, können vorher widerrufen oder bewusst ersetzt werden. Ersetzen gilt nur für ein eigenes aktives Angebot an denselben Empfänger. Ein Widerruf verhindert neue Abrufe, holt aber keine heruntergeladenen oder bereits in Übertragung befindlichen Kopien zurück. Ein inaktives Angebot wird auf unterstütztem Launcher nach dem Neustart und regelmäßig geprüft; zusätzlich läuft beim Öffnen der Freigabeseite ein fälliger Aufräumvorgang. Dies betrifft nur die privaten Scanner-Snapshots, nicht die Originale. Per Besitzer höchstens drei aktive Angebote und 32 MiB; instanzweit acht und 64 MiB. In 15 Minuten maximal fünf neue Angebote pro Besitzer bzw. 20 insgesamt. Erneutes Anzeigen/Wiederverwenden eines unveränderten aktiven Angebots, Abrufen und Widerrufen verbraucht kein neues Angebot.

Bei aktiven Freigaben zeigt der Sender **Noch keine Abrufantwort erfasst**
oder **Abruf beantwortet** mit der ersten erfassten Antwortzeit in UTC.
Das heißt nur, dass der Sender nach Rechte- und Integritätsprüfung eine
vollständige PDF-Antwort vorbereitet hat. Ob der andere Server sie vollständig
erhalten hat, ob der Browser sie gespeichert oder jemand sie in den Reader
importiert hat, lässt sich daraus nicht ablesen. Der Empfänger bekommt das
PDF weiterhin erst nach seiner eigenen Größen- und SHA-256-Prüfung als Download.
Der Zeitstempel wird nur neben der zugehörigen Freigabe gespeichert und
verschwindet beim Widerruf, Ersetzen oder Aufräumen nach Ablauf. Für bereits
vor diesem Update erfolgte Abrufe wird nichts rückwirkend erfasst.

**Grenzen:** JPEG/PNG-Einzelseiten, höchstens 24 Seiten, 32 MiB und insgesamt 64 Megapixel pro Scan. Keine automatische Zuweisung eines physischen Bibliotheksobjekts. Freigabe aus dem privaten Snapshot ist derzeit nur in Storage V1 möglich. V2 muss eine separate, getestete, verschlüsselte Speicherung ohne normale Dokumentprojektion und mit kontrolliertem Lebenszyklus erhalten. Beim Wechsel zu V2 werden bestehende V1-Angebote sofort nicht mehr abrufbar; ihr lokaler Inhalt kann bis zum Widerruf oder bis zur Aufräumprüfung nach Ablauf vorhanden sein. Widerrufe alte Angebote vor dem Wechsel oder warte auf deren Bereinigung: Das normale Migrationsbackup kopiert den bisherigen Datenordner und kann noch V1-Inhalte enthalten. Das lokale Erstellen und Herunterladen eines privaten Scan-PDF funktioniert auch unter V2.

## Reproduzierbare Abnahme

Für die lokale Variante Linux, OpenSSL, Python, Poppler und Tesseract mit Englisch bereitstellen. Die synthetischen Scanbilder werden aus der mit Pillow gelieferten Schrift erzeugt; es wird kein Systemfont gesucht oder heruntergeladen. Nur synthetische Seiten, Zertifikate und Konten werden erzeugt.

```sh
python -m pip install -e '.[test]'
python -m tools.smoke_book_scans --loopback
python -m unittest tests.test_library_book_scan tests.test_library_book_scan_shares tests.test_v2_authorization
node --test tests/library_book_scanner.test.cjs
```

Der Loopback-Lauf startet drei unabhängige App-Prozesse mit echtem verifiziertem TLS. Erwartet: PDF in korrekter Reihenfolge, erfolgreiche signierte Abholung durch den gewählten Peer, keine Einträge oder Bytes für den anderen normalerweise gekoppelten Peer, nächster Abruf nach Widerruf verweigert, echte manuelle Übernahme in Reader. Die erste Abrufantwort bleibt beim
Neustart des Senders sichtbar; ein Wiederholungsabruf ändert sie nicht. Für OCR des Test-PDF werden nur Poppler und Tesseract auf den synthetischen Seiten aufgerufen. Kein Hardware- oder Browser-Nachweis.

Für den Docker-Lauf muss der Checkout zusätzlich die echte Federation-Deployment-Fixture enthalten (siehe [Docker-Dokumentation](../deploy/docker/README.md)); kein Ersatz durch den Loopback-Test:

```sh
scratch="$(mktemp -d)"
python -m pip install --target "$scratch/html" 'beautifulsoup4==4.15.0'
docker build -t simpleoffice4me:book-scan .
python -m tools.smoke_book_scans --docker-image simpleoffice4me:book-scan \
  --html-deps "$scratch/html" --artifacts "$scratch/evidence"
```

Der Docker-Lauf deckt die zwei Container und ihre reguläre HTTPS-Route ab. Die dritte konfigurierte Gegenstelle wird gesondert mit `--loopback` geprüft. Der GitHub-Actions-Workflow **Book scanner acceptance** führt beide Prüfungen in getrennten Jobs aus, ohne einen ausgefallenen Docker-Job durch Loopback zu ersetzen. Die Jobs archivieren Stufen und synthetische Ergebnisdaten auch bei Fehlschlägen; kein Rohmitschnitt von HTTP, keine Zugangsdaten und kein Scan-PDF werden hochgeladen.
