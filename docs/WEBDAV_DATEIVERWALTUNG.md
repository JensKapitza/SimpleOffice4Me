# Dateiverwaltung über WebDAV

## Zweck und Nutzen

SimpleOffice stellt neben dem direkten LibreOffice-Link einen hierarchischen,
schreibenden WebDAV-Dateibaum bereit. Berechtigte Benutzer können damit
Dokumente in Nautilus/GNOME Files, Windows Explorer, macOS Finder und anderen
WebDAV-Clients öffnen, anlegen, bearbeiten, kopieren, umbenennen, verschieben
und kontrolliert löschen. FreeFileSync kann einen vom Betriebssystem
eingehängten WebDAV-Ordner wie einen lokalen Ordner vergleichen.

Die sichtbare Adresse lautet:

```text
https://simpleoffice.example/webdav/files/BENUTZERNAME
```

Sie enthält weder Kennwort noch Sitzungstoken. Getrennte Gerätezugänge mit
Lese- oder Schreibumfang, Ablaufdatum und einzeln widerrufbaren App-Passwörtern
sind in [WEBDAV_ZUGAENGE.md](WEBDAV_ZUGAENGE.md) beschrieben. Jedes Passwort
wird nur einmal unmittelbar nach der Erzeugung angezeigt.

## Schnellstart

1. Ein Dokument öffnen und **In LibreOffice bearbeiten** wählen.
2. Einen Gerätezugang mit passendem Rechteumfang anlegen, das App-Passwort
   kopieren und getrennt speichern.
3. Die angezeigte **Wurzeladresse** kopieren.
4. Benutzername, App-Passwort und Wurzeladresse im gewünschten Client
   eintragen.

### LibreOffice

Für eine einzelne Datei kann die ebenfalls angezeigte Dokumentadresse direkt
unter **Datei → Öffnen** verwendet werden. Für den gesamten Bestand:
**Datei → Remote öffnen → Dienste verwalten → WebDAV**. Speichern erfolgt mit
`Strg+S`; LibreOffice-Sperren und ETags schützen parallele Änderungen.

### Nautilus / GNOME Files

Unter **Andere Orte → Mit Server verbinden** die HTTPS-Wurzeladresse einfügen.
GNOME beschreibt den Ablauf in der offiziellen Anleitung
[Browse files on a server or network share](https://help.gnome.org/gnome-help/nautilus-connect.html).

### macOS Finder

**Gehe zu → Mit Server verbinden** wählen, Wurzeladresse einfügen und mit dem
App-Passwort anmelden. Siehe Apples Anleitung
[Mit einem WebDAV-Server verbinden](https://support.apple.com/de-de/guide/mac-help/mchlp1546/mac).

### Windows-Datei-Explorer

Die Wurzeladresse über **Dieser PC → Netzlaufwerk verbinden** beziehungsweise
**Netzwerkadresse hinzufügen** einrichten. Microsoft dokumentiert die
grundsätzliche Einbindung unter
[File sharing over a network in Windows](https://support.microsoft.com/en-US/Windows/Experience/Connectivity-Networking/file-sharing-over-a-network-in-windows).
Windows-WebClient muss aktiv sein; HTTPS und ein gültiges Zertifikat sind für
zuverlässige Anmeldung erforderlich.

### FreeFileSync

FreeFileSync besitzt keinen eigenen WebDAV-Endpunkt. Zuerst den WebDAV-Bestand
mit Nautilus, Finder oder Explorer einhängen und anschließend diesen
Einhängepunkt in FreeFileSync auswählen. Für bidirektionale Läufe zuerst
**Vergleichen** und die Vorschau prüfen. Keine Regel verwenden, die Konflikte
blind zugunsten einer Seite überschreibt. Grundlagen des Werkzeugs stehen im
offiziellen [FreeFileSync Quick Start](https://freefilesync.org/manual.php?topic=freefilesync).

## Protokollumfang

Maßgeblich ist
[RFC 4918 – Web Distributed Authoring and Versioning](https://www.rfc-editor.org/rfc/rfc4918.html).

| Anforderung | Standard | Implementierte Entscheidung |
|---|---|---|
| Sammlungen müssen hierarchische Mitglieder abbilden; Mitglied-URLs enden bei Sammlungen konsistent. | [RFC 4918 §5](https://www.rfc-editor.org/rfc/rfc4918.html#section-5) | Reale Ordner unter dem Dokumentstamm werden als Sammlungen angeboten; interne Metadaten, Historie, Richtliniendateien und Symlinks bleiben unsichtbar. |
| `PROPFIND` muss Eigenschaften liefern und `Depth` berücksichtigen. | [§9.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.1) | `Depth: 0` und `1` liefern `207 Multi-Status`, starke ETags, Größe, Medientyp und Änderungszeit. `infinity` wird aus Last- und Datenschutzgründen abgewiesen. |
| `PROPPATCH` muss `set` und `remove` in Dokumentreihenfolge und vollständig atomar verarbeiten; beliebige Dead Properties sollten möglich sein. | [§9.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.2) | Schreibende Gerätezugänge können begrenzte, benutzergebundene XML-Metadaten setzen und entfernen. Live Properties bleiben geschützt; Locks, Audit und Sync-Journal greifen. Details: [WEBDAV_EIGENSCHAFTEN_RFC4918.md](WEBDAV_EIGENSCHAFTEN_RFC4918.md). |
| `MKCOL` muss eine Sammlung erzeugen; fehlt die übergeordnete Sammlung, ist `409` vorgesehen. | [§9.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.3) | Genau ein Ordner wird atomar angelegt und erhält die normale SimpleOffice-Ordnerpolitik. Erweiterte MKCOL-Anfragetexte werden mit `415` abgewiesen. |
| `PUT` auf eine neue URL erzeugt eine Ressource; bei Austausch müssen Bedingungen und Sperren beachtet werden. | [§9.7](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.7), [§7.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.2) | Neue Dateien werden temporär geschrieben, synchronisiert und atomar umbenannt. Ein vorhandenes Dokument verlangt `If-Match` oder einen gültigen Lock-Token; blindes Überschreiben erhält `428`. |
| `COPY` lässt die Quelle unverändert und `MOVE` ändert ihre URL-Zuordnung. | [§9.8](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8), [§9.9](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9) | Reguläre Dateien können in existierende Ordner kopiert, verschoben und umbenannt werden. Kopien erhalten eine neue Dokument-ID; Verschiebungen behalten die ID. Fremde Hosts und Benutzerpfade werden abgewiesen. |
| `DELETE` entfernt die URL-Zuordnung und muss Sperren berücksichtigen. Collection-`DELETE` wirkt immer mit Tiefe infinity. | [§9.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.6), [§9.6.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.6.1) | Dateien und nicht leere Ordner verschwinden atomar aus dem sichtbaren Baum, bleiben aber mit Hash, Metadaten und Audit in der privaten Wiederherstellungsablage. Alle Mitglieder, Rechte und Lock-Token werden vorab geprüft; Details stehen in [WEBDAV_ORDNER_LOESCHEN_RFC4918.md](WEBDAV_ORDNER_LOESCHEN_RFC4918.md). |
| `Overwrite: F` muss vorhandene Ziele vor Ersetzung schützen. | [§10.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.6) | `F` ergibt immer `412`. `T` darf nur eine reguläre Datei ersetzen, wenn ein getaggter Ziel-ETag oder Ziel-Lock den aktuellen Zustand beweist. Ziel-ID, Rechte, Versionen und Wiederherstellung bleiben geschützt; Collections und COPY-Ziele werden nicht überschrieben. Details: [WEBDAV_SICHERES_MOVE_ERSETZEN.md](WEBDAV_SICHERES_MOVE_ERSETZEN.md). |
| Exklusive Write-Locks verhindern kollidierende Schreibzugriffe und können auch eine noch nicht belegte URL oder eine Collection sperren. | [§6](https://www.rfc-editor.org/rfc/rfc4918.html#section-6), [§7.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.3), [§7.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.4), [§9.10](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10) | `LOCK`/`UNLOCK` funktionieren für Dateien, LibreOffice-Lock-null-Abläufe und Collections mit Tiefe 0 oder infinity. Rekursive Sperren schützen vorhandene und neue Mitglieder; Details stehen in [WEBDAV_COLLECTION_LOCKS_RFC4918.md](WEBDAV_COLLECTION_LOCKS_RFC4918.md). |
| `If-Match` muss bei abweichendem Validator mit `412` fehlschlagen; `If-None-Match: *` schützt die Neuanlage. | [RFC 9110 §13.1.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.1), [§13.1.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.2) | ETags sind SHA-256-basiert. Vorbedingungen werden nochmals unter derselben Dateisperre wie der Inhalt geprüft. |
| Sammlungen können Änderungen seit einem undurchsichtigen Token effizient melden. | [RFC 6578 §3](https://www.rfc-editor.org/rfc/rfc6578.html#section-3) | `REPORT sync-collection`, `sync-level` 1/infinite, geänderte ETags und Lösch-Tombstones sind benutzergetrennt implementiert; Details stehen in [WEBDAV_SYNC_RFC6578.md](WEBDAV_SYNC_RFC6578.md). |

`OPTIONS` meldet DAV-Klassen `1, 2`, `sync-collection` und die Methoden `PROPFIND`, `PROPPATCH`, `REPORT`, `GET`, `HEAD`,
`PUT`, `DELETE`, `MKCOL`, `COPY`, `MOVE`, `LOCK` und `UNLOCK`.

## Rechte, Sicherheit und Datenschutz

- WebDAV ist standardmäßig deaktiviert und je Benutzer an getrennte,
  widerrufbare Gerätezugänge gebunden. App-Passwörter werden mit `scrypt` und
  zufälligem Salt gespeichert; Lesezugänge können keine Mutation anstoßen.
- Ein Benutzerpfad eines anderen Kontos antwortet mit `404`; Ziele auf anderen
  Hosts oder in anderen Benutzerbäumen werden nicht übertragen.
- Die WebDAV-Schicht übernimmt die bestehenden SimpleOffice-Dokumentrechte und
  erweitert weder Freigaben noch anonyme Zugriffe.
- Aufbewahrungs- und Bearbeitungssperren gelten auch für Schreiben, Kopieren,
  Verschieben und Löschen. Es gibt keine Umgehung durch einen Desktop-Client.
- Pfade mit `..`, internen Steuerverzeichnissen, Richtliniendateien, NUL-Zeichen
  oder Symlinks werden abgewiesen. Spezialdateien werden nicht angeboten.
- Basic Authentication ist ausschließlich über HTTPS sicher. Proxy- und
  Zertifikatkonfiguration sind Voraussetzung für entfernte Nutzung.
- Inhalte, Dateinamen, Lock-Besitzer und Zugangsdaten werden an keinen externen
  Dienst gesendet.

## Speicherung, Audit und Wiederherstellung

Neuanlage, Überschreiben, Kopieren, Umbenennen/Verschieben, Löschung sowie das
Anlegen und Entfernen von Ordnern und tatsächliche Eigenschaftsänderungen erzeugen Ereignis- und Git-basierte
Revisionsdatensätze mit Benutzer und Zeitpunkt. Beim Überschreiben wird der
Vorgänger unter `.simpleoffice-meta/content-versions/` hashgeprüft gesichert.
Eine Kopie bekommt eine neue ID und übernimmt nur Tags, Beschreibung und
nachvollziehbare Herkunft – keine Freigaben oder Aufbewahrungsentscheidungen.

WebDAV-`DELETE` verschiebt die Datei nach
`.simpleoffice-meta/webdav-trash/<Dokument-ID>/`, entfernt sie aus dem aktiven
Index und hält Ursprungspfad, Hash und Metadaten fest. Die Ablage ist nicht per
WebDAV oder Download erreichbar. Eine automatische endgültige Löschung oder
geänderte Aufbewahrungsfrist wird nicht eingeführt. Unter
**Wiederherstellen** kann ausschließlich der löschende Benutzer den Payload
hashgeprüft und ohne Überschreiben zurückholen. Frühere Inhalte nach einem
WebDAV-Speichern lassen sich auf der Dokumentseite als neue Revision
wiederherstellen. Sicherheitsmodell, Konflikte und RFC-Abgrenzung stehen in
[DATEI_WIEDERHERSTELLUNG.md](DATEI_WIEDERHERSTELLUNG.md).

Bei einem nicht leeren Ordner wird der vollständige Baum nach erfolgreicher
Vorprüfung atomar unter
`.simpleoffice-meta/webdav-collection-trash/<Vorgangs-ID>/tree/` verschoben.
Jede enthaltene Datei erscheint anschließend einzeln unter
**Wiederherstellen**. Normative Anforderungen, Wiederanlauf und Grenzen sind in
[WEBDAV_ORDNER_LOESCHEN_RFC4918.md](WEBDAV_ORDNER_LOESCHEN_RFC4918.md)
dokumentiert.

## Fehler- und Ausfallverhalten

- `401`: App-Passwort fehlt oder ist falsch.
- `404`: Ressource oder authentifizierter Benutzerpfad fehlt.
- `409`: Zielordner fehlt, Ordner ist nicht leer oder Operation kollidiert mit
  dem Dateibaum.
- `412`: ETag ist veraltet, `If-None-Match: *` trifft auf eine vorhandene Datei,
  `Overwrite: F` schützt ein Ziel oder COPY/Collection-MOVE würde es ersetzen.
- `415`: nicht unterstützter erweiterter `MKCOL`-Anfragetext.
- `413`: WebDAV-Eigenschafts-XML oder ein Einzelwert überschreitet die feste
  Schutzgrenze.
- `423`: Lock-Token fehlt/falsch oder eine SimpleOffice-Sperre greift.
- `428`: eine vorhandene Datei soll per PUT ohne Quellvalidator oder per MOVE
  ohne getaggten Ziel-ETag beziehungsweise Ziel-Lock überschrieben werden.
- `507`: das optionale WebDAV-Kontingent oder der physisch freie Speicher
  reicht für den angeforderten Zuwachs nicht; die XML-Fehlerbedingung
  unterscheidet `quota-not-exceeded` und `sufficient-disk-space`.
- `422`: bei aktivierter ClamAV-Prüfung wurde Schadcode erkannt; die Datei wird
  isoliert und nicht veröffentlicht.
- `503` mit `Retry-After: 60`: die aktivierte Virenprüfung ist vorübergehend
  nicht sicher verfügbar; eine vorhandene Revision bleibt unverändert.
- `502`: `Destination` verweist auf einen anderen Host oder Benutzerbaum.

Uploads unterliegen `SIMPLEOFFICE_MAX_UPLOAD_MIB`. Temporärdateien werden nach
Fehlern entfernt. Ein Prozessabbruch vor dem atomaren Austausch lässt das
bisherige Dokument unverändert; ein nicht mehr verwendeter Lock läuft nach
höchstens einer Stunde ab.

Mit `SIMPLEOFFICE_WEBDAV_QUOTA_MIB` kann zusätzlich ein standardmäßig
deaktiviertes Kontingent für den sichtbaren Dateibaum gesetzt werden. Clients
lesen Belegung und Rest über die geschützten Live Properties
`DAV:quota-used-bytes` und `DAV:quota-available-bytes`. Berechnung, Lock-Refresh,
Lock-Discovery und 507-Verhalten sind in
[WebDAV-Speichergrenzen und robuste Locks](WEBDAV_QUOTA_UND_LOCKS.md)
dokumentiert.

Downloads liefern starke ETags, `Last-Modified` und `Accept-Ranges: bytes`.
Clients können abgebrochene Übertragungen mit Einzel-, Suffix- oder begrenzten
Mehrfachbereichen fortsetzen. `If-Range` verhindert, dass Teilstücke einer alten
und einer neuen Dateiversion zusammengesetzt werden. Vorbedingungen, 206/304/
412/416-Antworten, Streaming und Schutzgrenzen erläutert
[Fortsetzbare WebDAV-Downloads nach RFC 9110](WEBDAV_DOWNLOADS_RFC9110.md).
Aktuelle Clients und Integrationsskripte können Uploads und vollständige oder
fortgesetzte Downloads zusätzlich nach
[RFC 9530 kryptografisch prüfen](WEBDAV_INTEGRITAET_RFC9530.md), ohne dass
LibreOffice oder Dateimanager diese Erweiterung unterstützen müssen.
Mit `SIMPLEOFFICE_WEBDAV_CLAMAV=1` kann außerdem jeder Datei-`PUT` vor der
atomaren Veröffentlichung in einer privaten Quarantäne mit ClamAV geprüft
werden. Installation, Signaturupdates, Kapazitätsgrenze, Audit und bewusstes
Fail-closed-Verhalten stehen in
[ClamAV-Prüfung vor WebDAV-Uploads](WEBDAV_UPLOADS_CLAMAV.md).

## Migration, Kompatibilität und Grenzen

Es gibt keine Datenbankmigration. Der bisherige direkte LibreOffice-Link und
seine stabilen Dokument-ID-URLs bleiben erhalten. Alte Einzelpasswörter werden
rückwärtskompatibel als bestehender Schreibzugang gelesen. Ohne aktives
App-Passwort ist kein WebDAV-Zugriff möglich.

Bewusst noch nicht implementiert sind WebDAV ACL und serverseitige Suche über
Dead Properties, rekursive COPY-/MOVE-Operationen, `PROPFIND Depth: infinity`,
partielle Range-Uploads, automatisches Zusammenführen binärer Office-Dateien und das
Überschreiben vorhandener COPY-/MOVE-Ziele. Nicht standardkonforme Clients,
die vorhandene Dateien ohne Lock und ohne `If-Match` speichern, erhalten
absichtlich `428` statt eines riskanten Erfolgs.

## Tests

Automatisiert geprüft werden realistische Abläufe für:

- Wurzel- und Ordner-`PROPFIND`, versteckte Steuerpfade und begrenzte Tiefe;
- `PROPPATCH`-Roundtrip, `propname`, fehlende Eigenschaften, atomaren Rollback,
  geschützte Live Properties, Lock- und Rechtefehler sowie XML-Schutzgrenzen;
- `MKCOL` und `PUT`-Neuanlage mit Dokument-ID, Hash und Audit;
- ETag-geschütztes Speichern, veraltete und fehlende Vorbedingungen;
- gesperrte leere Ressource, `PUT`, Token-Übertragung, ausdrücklicher
  Lock-Refresh, `lockdiscovery` und `UNLOCK`;
- rekursive Collection-Locks, geerbte Discovery, neue Mitglieder,
  Überlappung, Ablauf sowie getrennte Quell- und Zielbedingungen;
- Quota-Live-Properties, PROPPATCH-Schutz, erlaubte Verkleinerung sowie
  atomare 507-Ablehnung für PUT und COPY;
- `COPY`, `MOVE`, Umbenennung, neue/stabile IDs und Metadatenherkunft;
- Soft-Delete, Wiederherstellungsdatei und vollständige Ereignishistorie;
- bestätigte, benutzergetrennte Soft-Delete- und Inhaltswiederherstellung,
  belegte Ziele, veraltete Seitenzustände und manipulierte Payloads;
- nicht leere Ordner mit vollständiger Vorprüfung und Recovery, fehlende
  Eltern, fremde Hosts/Benutzer und vorhandene Ziele;
- falsche Zugangsdaten, Symlink-/Pfadgrenzen und Aufbewahrungssperren.
- optionalen ClamAV-Scan vor Neu- und Überschreiben, Fundquarantäne,
  Scanner-/Kapazitätsausfall sowie Rechte-, ETag- und Digest-Ablehnung vor dem
  ersten Scanneraufruf.

Zusätzlich läuft die vollständige Testsuite auf allen in GitHub Actions
konfigurierten Python-Versionen sowie der Dependency Audit.

## Deaktivierung und Rückkehr

Einzelnes **Widerrufen** beendet nur den gewählten Gerätezugang; **Alle Zugänge
widerrufen** deaktiviert WebDAV sofort vollständig. Vorhandene Dateien,
Versionen und Auditdaten bleiben erhalten. Das Entfernen des WebDAV-Blueprints
stellt das frühere Verhalten ohne Migration zurück. Wiederherstellungs- und
Versionsdateien dürfen nur nach Sicherungsprüfung manuell entfernt werden.
