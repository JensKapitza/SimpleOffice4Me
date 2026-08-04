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
Der sichere Einladungs-, Antwort-, Absage- und Gegenvorschlagsablauf nach
RFC 5546 ist in
[docs/ITIP_RFC_TERMINPLANUNG.md](docs/ITIP_RFC_TERMINPLANUNG.md) beschrieben.

Fristen, sichere manuelle Aussonderung und das Modell für reale sowie virtuelle
Objekte stehen in [docs/FRISTEN_UND_OBJEKTE.md](docs/FRISTEN_UND_OBJEKTE.md).

Für den sicheren öffentlichen Betrieb von Freigabelinks und CardDAV siehe
[docs/PROXY_HTTPS.md](docs/PROXY_HTTPS.md).

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
