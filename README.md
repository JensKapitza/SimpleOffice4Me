# SimpleOffice4Me

SimpleOffice4Me ist eine selbst betriebene Office-, Dokumenten- und
CRM-Anwendung auf Basis von Python und Flask. Sie bündelt Dokumente, Kontakte,
Kalender, Rechnungen und sichere Dateiübertragungen in einer Oberfläche.

## Funktionen

- Dokumentenverwaltung mit Eingangskorb, Volltextindex, Vorschauen, Versionen,
  Prüfsummen, Wiederherstellung und nachvollziehbarer Änderungshistorie
- WebDAV, CalDAV, CardDAV und optional SFTP/SSHFS für Desktop-Programme wie
  LibreOffice, Thunderbird und Dateimanager
- CRM-Kontaktübersicht mit Suche, Filtern, Kommunikationshistorie,
  Änderungshistorie, Benutzerzuordnung und CardDAV-Synchronisation
- lokale Adresssuche mit Vorschlägen ab drei Zeichen und automatischer
  Übernahme eindeutiger Angaben in Ort, Postleitzahl und Straße
- Kalender mit Serien, Erinnerungen, Einladungen, Status, Ressourcen,
  Buchungskonflikten und optionalem Google-Kalender-Abgleich
- Rechnungserstellung mit PDF/ZUGFeRD, Nummernkreisen, Vorlagen,
  CRM-Verknüpfung, Fälligkeiten und Zahlungshistorie
- zweisprachige Oberfläche in Deutsch und Englisch
- Audit-, Rechte- und Freigabefunktionen für den gemeinsamen Betrieb

## Zielbild

SimpleOffice4Me soll zu einer selbst betriebenen, einfachen Dokumentenverwaltung
ausgebaut werden. Die fachliche und technische Erweiterung ist in
[docs/PAPERLESS_ERWEITERUNG.md](docs/PAPERLESS_ERWEITERUNG.md) beschrieben.

Schnelle, lokal erzeugte Vorschaubilder und PDF-/Office-Collagen laufen im getrennten
Indexdienst; Werkzeugerkennung, Installation, Grenzen und Deaktivierung stehen in
[docs/DOKUMENTVORSCHAU_INDEXDIENST.md](docs/DOKUMENTVORSCHAU_INDEXDIENST.md).

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
Sicher bereinigte HTML-Beschreibungen aus Thunderbird, die parallele
Reintextansicht und korrekte lokale Bearbeitungszeiten dokumentiert
[docs/KALENDER_HTML_BESCHREIBUNGEN.md](docs/KALENDER_HTML_BESCHREIBUNGEN.md).
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
Vollständige Teilbäume können für Inventarisierung und Synchronisation
[begrenzt und konsistent mit `PROPFIND Depth: infinity`](docs/WEBDAV_REKURSIVE_PROPFIND_RFC4918.md)
erfasst werden; Mitgliederzahl, Verschachtelung und Antwortgröße bleiben gegen
Überlastung geschützt.
Neue Dateien und Ordner erhalten außerdem
[plattformübergreifend sichere WebDAV-Namen](docs/WEBDAV_PORTABLE_DATEINAMEN.md):
Unicode-Normalisierung, Windows-Gerätenamen und Großschreibungs-/
Normalisierungskollisionen werden ohne stille Umbenennung geprüft.
Der inkrementelle Änderungsabgleich für kompatible Clients folgt
[RFC 6578](docs/WEBDAV_SYNC_RFC6578.md) und liefert große Bestände über
fortsetzbare, revisionsfeste Teil-Token statt unbeschränkter Antworten.
Schreibbare, atomare Datei- und Ordnereigenschaften für Desktop-Clients sind
unter [WebDAV-Eigenschaften nach RFC 4918](docs/WEBDAV_EIGENSCHAFTEN_RFC4918.md)
beschrieben.
Persistente Erstellungszeiten, HTTP-konforme Änderungszeiten und ausgewählte
Windows-/Office-WebDAV-Eigenschaften einschließlich sicherer COPY-/MOVE-
Semantik beschreibt
[WebDAV-Zeitstempel und MS-WDVME-Interoperabilität](docs/WEBDAV_ZEITSTEMPEL_MS_WDVME.md).
Optionale Speichergrenzen nach RFC 4331, 507-Fehler und der vollständige
Lebenszyklus von Office-Sperren einschließlich Refresh und gesperrter leerer
Ressourcen stehen in
[WebDAV-Speichergrenzen und robuste Locks](docs/WEBDAV_QUOTA_UND_LOCKS.md).
Die sichere Zuordnung von Lock-Token, ETags und Sync-Token zu genau der im
`If`-Header bezeichneten Ressource ist in
[WebDAV-Bedingungen nach RFC 4918](docs/WEBDAV_IF_HEADER_RFC4918.md) dokumentiert.
Die [einheitlichen HTTP-Vorbedingungen nach RFC 9110](docs/WEBDAV_HTTP_VORBEDINGUNGEN_RFC9110.md)
schützen PUT, DELETE, PROPPATCH, MKCOL, COPY und MOVE mit derselben ETag- und
Datumsreihenfolge vor verlorenen Änderungen.
Rekursive Ordnersperren, vererbte Lock-Discovery und Konfliktschutz für neue
Dateien nach RFC 4918 beschreibt
[WebDAV-Ordnersperren](docs/WEBDAV_COLLECTION_LOCKS_RFC4918.md).
Ganze Ordner lassen sich nach
[RFC 4918 serverseitig kopieren und verschieben](docs/WEBDAV_ORDNER_COPY_MOVE_RFC4918.md)
und [vorhandene Dateien mit Ziel-ETag oder Lock sicher per MOVE ersetzen](docs/WEBDAV_SICHERES_MOVE_ERSETZEN.md)
sowie
[Vorlagen und Dateien mit Ziel-ETag oder Lock sicher per COPY auf vorhandene Ziele übernehmen](docs/WEBDAV_SICHERES_COPY_ERSETZEN.md) und
[rekursiv löschen und einzeln wiederherstellen](docs/WEBDAV_ORDNER_LOESCHEN_RFC4918.md),
einschließlich Rechte-, Lock-, Quota-, Audit- und Rollback-Schutz.
Fortsetzbare Downloads, `If-Range`, bedingte GET-/HEAD-Anfragen und sichere
Mehrfachbereiche beschreibt
[WebDAV-Downloads nach RFC 9110](docs/WEBDAV_DOWNLOADS_RFC9110.md).
Kryptografisch prüfbare Uploads und vollständige sowie fortgesetzte Downloads
mit `Content-Digest`, `Repr-Digest` und sicherer Algorithmuswahl beschreibt
[WebDAV-Übertragungsintegrität nach RFC 9530](docs/WEBDAV_INTEGRITAET_RFC9530.md).
Getrennte, ablaufende Lese- und Schreibzugänge je Gerät sind in
[docs/WEBDAV_ZUGAENGE.md](docs/WEBDAV_ZUGAENGE.md) beschrieben.
Das [gemeinsame virtuelle Dateisystem mit vererbbaren Benutzerrechten und optionalem SFTP](docs/VIRTUELLES_DATEISYSTEM_SFTP.md)
stellt denselben erlaubten Dateibaum zusätzlich shellfrei für `sftp` und
`sshfs` bereit.
Wie diese Gerätezugänge zusätzlich auf genau einen Ordner begrenzt werden und
`COPY`, `MOVE`, Locks sowie Sync-Token nach dem Rechteprinzip aus RFC 3744
abgesichert sind, beschreibt
[docs/WEBDAV_ORDNERZUGAENGE_RFC3744.md](docs/WEBDAV_ORDNERZUGAENGE_RFC3744.md).
Die geschützte, rein lesende
[Principal- und Rechteerkennung nach RFC 3744/5397](docs/WEBDAV_PRINCIPAL_RECHTE_RFC3744_5397.md)
ermöglicht Desktop-Clients eine ressourcengenaue Read-only-Anzeige, ohne ACLs,
Freigaben oder Ordnergrenzen über WebDAV veränderbar zu machen.
Die bestätigte, hashgeprüfte Rückholung gelöschter Dateien und früherer
Dateiinhalte ohne stilles Überschreiben beschreibt
[docs/DATEI_WIEDERHERSTELLUNG.md](docs/DATEI_WIEDERHERSTELLUNG.md).
Der bestätigungspflichtige Import von Mail-Anhängen, Herkunftskennzeichnung,
Quarantäne und ClamAV-Betrieb sind in
[docs/ANHAENGE_CLAMAV.md](docs/ANHAENGE_CLAMAV.md) dokumentiert.
Wie neue und geänderte Dateien aus LibreOffice, FreeFileSync und Dateimanagern
optional vor jeder WebDAV-Veröffentlichung fail-closed mit ClamAV geprüft
werden, beschreibt
[docs/WEBDAV_UPLOADS_CLAMAV.md](docs/WEBDAV_UPLOADS_CLAMAV.md).
Die eigenständige Übertragung guter Offline-, Sidecar- und Suchkonzepte aus
TagSpaces ist in [docs/TAGSPACES_ANSAETZE.md](docs/TAGSPACES_ANSAETZE.md)
einschließlich Verbesserungen und Interoperabilitätsgrenzen beschrieben.
Die daraus abgeleitete, berechtigungsgebundene
[serverseitige WebDAV-Suche nach RFC 5323](docs/WEBDAV_SUCHE_RFC5323_TAGSPACES.md)
findet Namen, Größen, Zeitstempel und Dead-Property-Tags ohne Dateiinhalt oder
externe Suchdienste und liefert bei Schutzgrenzen keine irreführende Teilliste.

## Start und Update

Nach der ersten Anmeldung führt **Einrichten** schrittweise durch HTTPS,
getrennte App-Passwörter, Ordnerrechte sowie die Anbindung von Windows und
Linux per WebDAV, CalDAV, CardDAV und SFTP/SSHFS. Die vollständige
[Erststart- und Desktop-Anleitung](docs/ERSTSTART_UND_DESKTOP_SETUP.md) kann im
Assistenten auch als persönliche, geheimnisfreie Textdatei heruntergeladen
werden.

`start.sh` (Linux), `start.bat` (Windows) und `start.command` (macOS) erzeugen
die lokale Python-Umgebung, installieren die Anwendung und starten den
Ersteinrichtungs-Assistenten. Anschließend läuft die Anwendung mit dem
produktionsgeeigneten Waitress-WSGI-Server statt mit Flasks Entwicklungsserver.
Konfiguration und Sicherheitsgrenzen stehen unter
[Produktionsbetrieb mit Waitress](docs/PRODUKTIONSBETRIEB.md). Updates laufen
mit `update.sh` oder `update.bat`
über ein sicheres `git pull --ff-only`.
Der [getrennte Dokumentindex](docs/PERFORMANCE_INDEXDIENST.md) hält Login,
Dashboard und WebDAV auch bei mehr als 50.000 Dateien reaktionsfähig und dokumentiert
Ressourcenpriorisierung, Fehlerverhalten und Abschaltung.
Die [schnelle Ansicht einzelner Dokumente](docs/SCHNELLE_DATEIANSICHT.md) verhindert
Vollbestandsläufe, Hashing und große Metadaten-Schreibvorgänge beim Öffnen einer Datei.

`restart.sh` startet die Anwendung neu und berücksichtigt aktive Indexer- und
SSH/SFTP-Dienste. Ein erneuter Aufbau lokaler Indizes kann ohne erneuten
Download der Quelldaten ausgelöst werden.

## Voraussetzungen

- Python 3
- ein aktueller Browser, zum Beispiel Firefox oder Chromium
- optionale Systemwerkzeuge für Dokumentvorschau, OCR, Bildverarbeitung,
  Virenprüfung und SSH/SFTP gemäß den verlinkten Betriebsanleitungen

## Tests

Die automatisierten Tests werden bei Pull Requests über GitHub Actions
ausgeführt. Funktionsbezogene Tests befinden sich im Verzeichnis `tests/`.
