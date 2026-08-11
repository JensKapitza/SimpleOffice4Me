# Dokumentationsübersicht

## Kontakte und Kalender

- [Kontaktverwaltung, vCard und CardDAV](KONTAKTE.md)
- [Globale Kontakt-Änderungshistorie](KONTAKT_AUDIT.md)
- [Kalender, Buchungen, ICS und CalDAV-Planung](KALENDER.md)
- [Mehrere Kalender und CalDAV: RFC-Auswertung und Umsetzung](CALDAV_RFC_IMPLEMENTIERUNG.md)
- [Serientermine, Ausnahmen und Zeitzonen nach RFC 5545](KALENDER_SERIEN_RFC5545.md)
- [Lokale Erinnerungen nach RFC 5545 und RFC 9074](KALENDER_ERINNERUNGEN_RFC5545_9074.md)
- [Terminmetadaten und Konferenzzugänge nach RFC 5545/7986](KALENDER_METADATEN_RFC5545_7986.md)
- [Optionaler Google-Kalender-Abgleich mit Sync-Token und Konfliktschutz](GOOGLE_KALENDER_SYNC.md)
- [iTIP-Terminplanung und Einladungen nach RFC 5546](ITIP_RFC_TERMINPLANUNG.md)
- [CalDAV Scheduling, Inbox/Outbox und Free/Busy nach RFC 6638](CALDAV_SCHEDULING_RFC6638.md)

Weitere fachliche und betriebliche Dokumente liegen in diesem Verzeichnis; die
wichtigsten Einstiegspunkte sind zusätzlich im [Projekt-README](../README.md)
verlinkt.

## Dateien und Desktop-Integration

- [Hierarchische WebDAV-Dateiverwaltung](WEBDAV_DATEIVERWALTUNG.md)
- [Effizienter WebDAV-Änderungsabgleich nach RFC 6578](WEBDAV_SYNC_RFC6578.md)
- [WebDAV-Eigenschaften und Metadaten nach RFC 4918](WEBDAV_EIGENSCHAFTEN_RFC4918.md)
- [WebDAV-Speichergrenzen nach RFC 4331 und robuste Locks](WEBDAV_QUOTA_UND_LOCKS.md)
- [Ressourcengenaue WebDAV-If-Bedingungen nach RFC 4918](WEBDAV_IF_HEADER_RFC4918.md)
- [Rekursive WebDAV-Ordnersperren nach RFC 4918](WEBDAV_COLLECTION_LOCKS_RFC4918.md)
- [WebDAV-Ordner rekursiv kopieren und verschieben nach RFC 4918](WEBDAV_ORDNER_COPY_MOVE_RFC4918.md)
- [Vorhandene WebDAV-Dateien sicher per MOVE ersetzen](WEBDAV_SICHERES_MOVE_ERSETZEN.md)
- [WebDAV-Ordner rekursiv und wiederherstellbar löschen nach RFC 4918](WEBDAV_ORDNER_LOESCHEN_RFC4918.md)
- [Fortsetzbare WebDAV-Downloads und HTTP-Validatoren nach RFC 9110](WEBDAV_DOWNLOADS_RFC9110.md)
- [WebDAV-Übertragungsintegrität mit Content- und Repr-Digest nach RFC 9530](WEBDAV_INTEGRITAET_RFC9530.md)
- [Getrennte WebDAV-Gerätezugänge](WEBDAV_ZUGAENGE.md)
- [Ordnergebundene Gerätezugänge nach RFC 3744](WEBDAV_ORDNERZUGAENGE_RFC3744.md)
- [Sichere Datei- und Inhaltswiederherstellung](DATEI_WIEDERHERSTELLUNG.md)
- [LibreOffice über WebDAV](LIBREOFFICE_WEBDAV.md)
- [Bestätigte Mail-Anhänge und ClamAV](ANHAENGE_CLAMAV.md)
- [ClamAV-Prüfung vor WebDAV-Uploads](WEBDAV_UPLOADS_CLAMAV.md)
- [Eigenständig umgesetzte Ansätze aus TagSpaces](TAGSPACES_ANSAETZE.md)

## Betrieb

- [Produktionsbetrieb mit Waitress](PRODUKTIONSBETRIEB.md)
- [HTTPS und Reverse Proxy](PROXY_HTTPS.md)
