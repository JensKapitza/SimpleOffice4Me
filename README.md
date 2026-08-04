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

Der interne Kalender und die CalDAV-Planung stehen in
[docs/KALENDER.md](docs/KALENDER.md).

Fristen, sichere manuelle Aussonderung und das Modell für reale sowie virtuelle
Objekte stehen in [docs/FRISTEN_UND_OBJEKTE.md](docs/FRISTEN_UND_OBJEKTE.md).

Für den sicheren öffentlichen Betrieb von Freigabelinks und CardDAV siehe
[docs/PROXY_HTTPS.md](docs/PROXY_HTTPS.md).

## Start und Update

`start.sh` (Linux), `start.bat` (Windows) und `start.command` (macOS) erzeugen
die lokale Python-Umgebung, installieren die Anwendung und starten den
Ersteinrichtungs-Assistenten. Updates laufen mit `update.sh` oder `update.bat`
über ein sicheres `git pull --ff-only`.

## Teststand

Test-Commit vom 25.07.2026.


NEED FLASK to work
NEED Python3, firefox, chromium, imagemagic, opencv
