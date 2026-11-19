# Schnelle Ansicht einzelner Dokumente

## Problem und Ursache

Die Detailansicht einer einzelnen Datei führte bisher mehrere Arbeiten über den
gesamten Dokumentbestand aus:

- Beziehungen bauten eine Zuordnung aus allen Sidecar-Dateien auf;
- Versionen suchten ihre Serie durch alle Sidecars;
- die Fristenberechnung baute den vollständigen Beziehungsgraphen auf;
- das eingebettete Logbuch öffnete potenziell alle Audit-Ereignisse;
- jeder Lesezugriff schrieb das komplette Dokument-Sidecar einschließlich
  extrahiertem Text und OCR-Daten zweimal neu.

Damit wuchs die Antwortzeit linear mit dem Gesamtbestand. Bei rund 50.000
Dateien dominierte diese Arbeit das Laden einer einzelnen Datei.

## Neue Auslegung

- `document_listing` enthält Versionsserie und Versionsnummer.
- `document_relationship` enthält die für Navigation und transitive
  Aufbewahrung benötigten Kanten.
- Versionen werden über einen indizierten Gleichheitsfilter geladen.
- Beziehungen öffnen ausschließlich ausdrücklich referenzierte Sidecars.
- Transitive Fristen werden mit einer rekursiven SQLite-Abfrage nur für die
  erreichbare Komponente berechnet.
- Das vollständige Audit-Log wird nicht in die erste HTML-Antwort eingebettet,
  sondern über einen sichtbaren Link gezielt geladen.
- Lesezugriffe landen in einem kleinen, auf 200 Einträge begrenzten
  Zugriffssidecar und weiterhin vollständig im append-only Scannerlog. Große
  OCR- oder Volltextmetadaten werden beim Ansehen nicht neu geschrieben.

Das Öffnen startet weder Hashing noch Textextraktion, OCR oder einen Scan. Die
Originaldatei wird erst durch den separaten Vorschauabruf des Browsers gelesen.

## Konsistenz, Sicherheit und Rechte

Die SQLite-Tabellen sind löschbare Projektionen. Der Indexdienst füllt sie auch
für unveränderte Dateien auf. Änderungen an Tags, Beziehungen, Status oder
Versionen aktualisieren die Projektion unmittelbar. Der fokussierte Datensatz
wird beim Aufruf zusätzlich aktualisiert.

Bestehende Rechte, Aufbewahrungsregeln und WebDAV-Sperren bleiben unverändert.
Die Fristenprüfung lädt alle über `propagates_retention` erreichbaren Dokumente
in beiden Kantenrichtungen. Ein unvollständiger Erstindex kann noch nicht
erfasste eingehende Kanten erst nach deren Verarbeitung berücksichtigen.

Zugriffsereignisse bleiben mit Benutzer und Zeitpunkt im Scannerlog erhalten.
Das kleine Zugriffssidecar dient der schnellen Anzeige von `seen_by`,
`found_by` und den letzten 200 Zugriffen. Es enthält keine Dateiinhalte.

## Migration und Rückwärtskompatibilität

Die Schemaänderung ist additiv. Alte SQLite-Indizes erhalten die neuen Spalten
und Tabellen automatisch. Dokumentdateien, Freigaben, Aufbewahrungsfristen und
externe Schnittstellen ändern sich nicht. Bis der normale Indexlauf alle alten
Sidecars gesehen hat, kann eine ältere Versionsreihe schrittweise erscheinen.

Zur Rückkehr kann der Commit zurückgenommen und `index.sqlite3` gelöscht
werden. Der Index wird aus unveränderten Dateien und Sidecars neu erzeugt. Die
kleinen Zugriffssidecars beeinflussen den Dokumentinhalt nicht.

## Tests und Grenzen

Der Lasttest legt 50.147 Indexeinträge an und öffnet genau ein Dokument. Er
bricht ab, falls dabei `_all_documents()`, Inhalts-Hashing, das vollständige
Logbuch oder ein großes Dokument-Sidecar aufgerufen wird. Weitere Tests prüfen
transitive Fristen, Versionsreihen, Zugriffsaudit und Schema-Migration.

Das globale Logbuch sowie bewusst gestartete bestandsweite Fristen- und
Bereinigungsberichte bleiben Gesamtbestandsoperationen. Sie liegen nicht im
kritischen Pfad der einzelnen Dokumentansicht.

## Datei oder Pfad senden

In der Dokumentliste die Datei aufklappen oder ihre Detailansicht öffnen und **Senden …** wählen.

| Auswahl | Was kommt an? |
| --- | --- |
| QR oder Ton, nur Pfad | Der relative Pfad im Dokumentbestand, z. B. `Projekte/Brief.pdf`. |
| QR oder Ton, Datei | Ein Dateipaket mit Dateiname und Prüfsumme, sofern es in das gewählte Medium passt. QR fasst hier bis zu 1.100 Dateibytes. Bei Ton zählen Dateiname und Paketdaten zum Limit von 128 UTF-8-Bytes. Größere Inhalte werden über einen geschützten Download-Link angeboten. |
| Geschützter Datei-Link | Ein Link zur Originaldatei. Das Zielgerät benötigt Zugang zum Server und Leserechte. Beim Aufruf über localhost kann ein laufender HTTPS-LAN-Proxy die erreichbare Adresse liefern. |
| Chat | Den Raum aufklappen, **Logischer Pfad als Text** oder **Dokument als Chat-Anhang** wählen und senden. |

Zum Empfangen einer Datei **QR & Ton** öffnen. Gelesenen QR-Text in das Empfangsfeld einfügen oder den Tonempfang starten und auf beiden Geräten dasselbe Frequenzband und dieselbe Fehlerkorrektur wählen. Nach erfolgreicher Prüfung lässt sich das Dateipaket herunterladen. Ein übertragener Download-Link lässt sich am Zielgerät öffnen. Wer einen selbstständigen Datei-QR scannt oder seinen Ton empfängt, erhält eine Kopie.
