# SimpleOffice4Me

Hallo!

Simple Office APP Python FLASK

## Zielbild

SimpleOffice4Me soll zu einer selbst betriebenen, einfachen Dokumentenverwaltung
ausgebaut werden. Die fachliche und technische Erweiterung ist in
[docs/PAPERLESS_ERWEITERUNG.md](docs/PAPERLESS_ERWEITERUNG.md) beschrieben.

Der erste dateibasierte Eingangskorb mit Prüfsummen, Chronik und einem
neu aufbaubaren Index ist umgesetzt. Die Installation und der erste Scan stehen
in [docs/ERSTER_START.md](docs/ERSTER_START.md).

Das Konzept für normale Sync-Clients, externe Archive und föderierte Kataloge
steht in [docs/SYNC_FEDERATION.md](docs/SYNC_FEDERATION.md).

Die flexible Kontaktverwaltung und Thunderbird-Anbindung sind in
[docs/KONTAKTE.md](docs/KONTAKTE.md) beschrieben.
Filter und Benutzerfarben für die nachvollziehbare Kontakt-Historie stehen in
[docs/KONTAKT_HISTORIE.md](docs/KONTAKT_HISTORIE.md).

Der interne Kalender und die CalDAV-Planung stehen in
[docs/KALENDER.md](docs/KALENDER.md). Die rein lesende Prüfung von
iCalendar-Dateien vor dem Import ist in
[docs/ICS_VORSCHAU.md](docs/ICS_VORSCHAU.md) dokumentiert.
Mehrere Kalender, Rechte, Thunderbird-Synchronisation und die umgesetzten
Anforderungen aus RFC 5545, 4791, 6578 und 6764 beschreibt
[docs/CALDAV_RFC_IMPLEMENTIERUNG.md](docs/CALDAV_RFC_IMPLEMENTIERUNG.md).
Serientermine mit RRULE, Ausnahmen, Zeitzonen, Buchungskonflikten und
CalDAV-Zeitbereichsabfragen sind in
[docs/KALENDER_SERIEN_RFC5545.md](docs/KALENDER_SERIEN_RFC5545.md) beschrieben.
Lokale Kalendererinnerungen mit VALARM, Bestätigung und standardkonformem
Snooze dokumentiert
[docs/KALENDER_ERINNERUNGEN_RFC5545_9074.md](docs/KALENDER_ERINNERUNGEN_RFC5545_9074.md).
Status, Zeitbelegung, Priorität, Ort, Ressourcen und sichere
Konferenzzugänge nach RFC 5545/7986 beschreibt
[docs/KALENDER_METADATEN_RFC5545_7986.md](docs/KALENDER_METADATEN_RFC5545_7986.md).
Der optionale, ausschließlich manuell gestartete Google-Kalender-Abgleich mit
inkrementellen Sync-Tokens und expliziter Konfliktentscheidung steht in
[docs/GOOGLE_KALENDER_SYNC.md](docs/GOOGLE_KALENDER_SYNC.md).
Der sichere Einladungs-, Antwort-, Absage- und Gegenvorschlagsablauf nach
RFC 5546 ist in
[docs/ITIP_RFC_TERMINPLANUNG.md](docs/ITIP_RFC_TERMINPLANUNG.md) beschrieben.
Die optionale lokale CalDAV-Terminplanung mit privaten Inbox-/Outbox-
Sammlungen und datensparsamer Free/Busy-Abfrage beschreibt
[docs/CALDAV_SCHEDULING_RFC6638.md](docs/CALDAV_SCHEDULING_RFC6638.md).

Fristen, sichere manuelle Aussonderung und das Modell für reale sowie virtuelle
Objekte stehen in [docs/FRISTEN_UND_OBJEKTE.md](docs/FRISTEN_UND_OBJEKTE.md).

Für den sicheren öffentlichen Betrieb von Freigabelinks und CardDAV siehe
[docs/PROXY_HTTPS.md](docs/PROXY_HTTPS.md).

Schreibendes Öffnen und Speichern von Dokumenten mit LibreOffice über WebDAV
ist in [docs/LIBREOFFICE_WEBDAV.md](docs/LIBREOFFICE_WEBDAV.md) beschrieben.
Hierarchische Dateiverwaltung mit Nautilus, Windows Explorer, Finder und einem
eingehängten FreeFileSync-Ziel ist in
[docs/WEBDAV_DATEIVERWALTUNG.md](docs/WEBDAV_DATEIVERWALTUNG.md) dokumentiert.
Der inkrementelle Änderungsabgleich für kompatible Clients folgt
[RFC 6578](docs/WEBDAV_SYNC_RFC6578.md).
Getrennte, ablaufende Lese- und Schreibzugänge je Gerät sind in
[docs/WEBDAV_ZUGAENGE.md](docs/WEBDAV_ZUGAENGE.md) beschrieben.
Die bestätigte, hashgeprüfte Rückholung gelöschter Dateien und früherer
Dateiinhalte ohne stilles Überschreiben beschreibt
[docs/DATEI_WIEDERHERSTELLUNG.md](docs/DATEI_WIEDERHERSTELLUNG.md).
Der bestätigungspflichtige Import von Mail-Anhängen, Herkunftskennzeichnung,
Quarantäne und ClamAV-Betrieb sind in
[docs/ANHAENGE_CLAMAV.md](docs/ANHAENGE_CLAMAV.md) dokumentiert.
Die eigenständige Übertragung guter Offline-, Sidecar- und Suchkonzepte aus
TagSpaces ist in [docs/TAGSPACES_ANSAETZE.md](docs/TAGSPACES_ANSAETZE.md)
einschließlich Verbesserungen und Interoperabilitätsgrenzen beschrieben.

## Start und Update

`start.sh` (Linux), `start.bat` (Windows) und `start.command` (macOS) erzeugen
die lokale Python-Umgebung, installieren die Anwendung und starten den
Ersteinrichtungs-Assistenten. Anschließend läuft die Anwendung mit dem
produktionsgeeigneten Waitress-WSGI-Server statt mit Flasks Entwicklungsserver.
Konfiguration und Sicherheitsgrenzen stehen unter
[Produktionsbetrieb mit Waitress](docs/PRODUKTIONSBETRIEB.md). Updates laufen
mit `update.sh` oder `update.bat`
über ein sicheres `git pull --ff-only`.

## Teststand

Test-Commit vom 25.07.2026.


NEED FLASK to work
NEED Python3, firefox, chromium, imagemagic, opencv
