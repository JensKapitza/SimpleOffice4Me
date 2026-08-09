# Sichere Datei- und Inhaltswiederherstellung

## Zweck und Nutzen

SimpleOffice kann zwei typische Fehler ohne stilles Überschreiben rückgängig
machen:

1. Eine über WebDAV gelöschte Datei lässt sich über **Wiederherstellen** in
   einen vorhandenen Ordner zurückholen.
2. Nach einem Speichern aus LibreOffice, Nautilus, Finder, Explorer oder einem
   Sync-Lauf lässt sich auf der Dokumentseite ein früherer Dateiinhalt als neue
   Revision wiederherstellen.

Die bisherige Datei, die Metadaten und das Audit werden nicht zurückgesetzt.
Eine Inhaltswiederherstellung archiviert den aktuellen Inhalt zuerst und legt
danach eine neue, nachvollziehbare Revision an. Damit bleibt auch eine
versehentliche Wiederherstellung reversibel.

## Bedienung

### WebDAV-Löschung zurückholen

1. In der Hauptnavigation **Wiederherstellen** öffnen.
2. Ursprungspfad, Löschzeit, Größe, Dokument-ID und vollständigen SHA-256-Wert
   prüfen.
3. Den ursprünglichen oder einen neuen relativen Zielpfad eintragen. Der
   Zielordner muss bereits existieren.
4. Die Bestätigung markieren und **Wiederherstellen** wählen.

Ist das Ziel bereits belegt, bleibt dessen Inhalt unverändert. Ein anderer
Name oder Ordner kann anschließend explizit gewählt werden. Angezeigt werden
nur Löschungen des angemeldeten Benutzers; fremde Dokument-IDs liefern keine
Informationen.

### Früheren Dateiinhalt zurückholen

Auf einer Dokumentseite erscheint **Frühere Dateiinhalte**, sobald WebDAV oder
die SimpleOffice-Wiederherstellung mindestens einen Vorgänger archiviert hat.
Zeitpunkt, Bearbeiter, Größe und Hash helfen bei der Auswahl. Nach Bestätigung
wird der ausgewählte Inhalt als nächste Inhaltsrevision gespeichert. Ein seit
dem Öffnen der Seite erneut geänderter Inhalt wird wegen der eingebetteten
SHA-256-Vorbedingung nicht überschrieben.

## Standards und abgeleitete Entscheidungen

Die Wiederherstellung ist eine geschützte SimpleOffice-Funktion oberhalb des
WebDAV-Namensraums. Sie behauptet bewusst keine vollständige DAV-Versionierung.

| Normative Aussage | Quelle | Umsetzung |
|---|---|---|
| `DELETE` **MUST** die URI-Zuordnung der Ressource entfernen; bei Erfolg darf die URL nicht weiter auf die gelöschte Ressource zeigen. | [RFC 4918 §9.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.6) | Die Datei verschwindet unmittelbar aus `PROPFIND`, `GET` und dem aktiven Index. Die private, nicht über WebDAV erreichbare Wiederherstellungskopie ist keine URI-Zuordnung im sichtbaren DAV-Baum. |
| WebDAV-Server **MUST** ETags nach erfolgreicher inhaltsändernder Operation korrekt behandeln; Clients können sie für Cache- und Schreibkonflikte verwenden. | [RFC 4918 §8.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-8.6) | Jede aktive Inhaltsversion hat einen starken SHA-256-ETag. Nach Wiederherstellung entsteht ein passender neuer aktueller Validator. |
| Write-Locks dienen insbesondere der Vermeidung verlorener Änderungen; Clients **SHOULD** einen passenden Lock verwenden, wenn dieser verfügbar ist. | [RFC 4918 §7.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.2) | WebDAV-Schreiben bleiben Lock- und ETag-geschützt. Die Weboberfläche prüft die erwartete aktuelle Prüfsumme unter derselben exklusiven Dateisperre wie den Austausch. |
| Ein Empfänger von `If-Match` **MUST** die Operation nur bei Übereinstimmung ausführen; andernfalls ist `412 Precondition Failed` vorgesehen. | [RFC 9110 §13.1.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.1) | WebDAV nutzt `If-Match`; die HTML-Wiederherstellung überträgt denselben Sicherheitsgedanken als serverseitig validierte SHA-256-Vorbedingung. Stale Formulare ändern nichts. |
| DeltaV definiert Version-Control, Version-Ressourcen, Checkout und Checkin als eigenes Protokollmodell. | [RFC 3253 §3](https://www.rfc-editor.org/rfc/rfc3253.html#section-3), [§3.2](https://www.rfc-editor.org/rfc/rfc3253.html#section-3.2) | SimpleOffice meldet keine DeltaV-Konformität und bietet weder `VERSION-CONTROL`, `CHECKOUT`, `CHECKIN` noch `DAV:version-history`. Die interne, hashbasierte Historie wird nur über die authentifizierte Anwendung bedient. |
| Ein DeltaV-Server **MAY** neue versionierbare Ressourcen automatisch unter Versionskontrolle stellen; dann **MUST** das Ergebnis einem expliziten `VERSION-CONTROL` entsprechen. | [RFC 3253 §3.2](https://www.rfc-editor.org/rfc/rfc3253.html#section-3.2) | Diese Option wird bewusst nicht gewählt: Ohne das vollständige Ressourcen- und Methodenmodell wäre eine automatische DeltaV-Ankündigung irreführend und inkompatibel. |

### Implementierte Konformität

- sichtbare Löschung der WebDAV-URL bei unveränderter privater
  Wiederherstellungskopie;
- atomare Rückholung innerhalb des Dokumentenspeichers;
- starke SHA-256-Integritätsprüfung vor jeder Rückholung;
- Vorbedingung gegen zwischenzeitliche Änderungen;
- niemals automatisches Ersetzen eines vorhandenen Zielpfads;
- stabile Dokument-ID, Suchindex, Dateifingerprint und Metadatenhistorie;
- Ereignis- und Git-Audit mit Akteur, Zeitpunkt, Quelle, Ziel und Hash;
- Bearbeitungs- und Aufbewahrungssperren gelten auch für alte Dateiinhalte.

### Bewusst nicht implementiert

- vollständige WebDAV-Versionierung nach RFC 3253;
- Wiederherstellung durch `COPY` oder `MOVE` aus einem sichtbaren Papierkorb;
- endgültiges Löschen aus der Oberfläche;
- automatische Bereinigung oder neue Aufbewahrungsregeln;
- binäres Zusammenführen zweier Office-Dateien;
- Wiederherstellung in einen noch nicht vorhandenen Ordner.

Diese Grenzen verhindern, dass Desktop- oder Sync-Clients interne Historien
sehen, Aufbewahrung umgehen oder einen neueren Inhalt unbemerkt ersetzen.

## Voraussetzungen und Konfiguration

Es ist keine neue Umgebungsvariable und keine Datenbankmigration erforderlich.
Die Funktion verwendet den vorhandenen Dokumentenstamm und die bestehenden
Grenzen aus `SIMPLEOFFICE_MAX_UPLOAD_MIB`. Wiederherstellbare Inhalte liegen
unter `.simpleoffice-meta/webdav-trash/` und
`.simpleoffice-meta/content-versions/`; beide Verzeichnisse müssen im Backup
enthalten sein und dürfen nicht durch einen Webserver veröffentlicht werden.

Für LibreOffice, FreeFileSync, Nautilus/GNOME Files, Windows Explorer und
Finder bleibt die Einrichtung aus
[WEBDAV_DATEIVERWALTUNG.md](WEBDAV_DATEIVERWALTUNG.md) unverändert. Die Clients
sehen nur den aktiven Baum. Rückholung erfolgt absichtlich in der
SimpleOffice-Weboberfläche, damit Identität, Bestätigung und Audit eindeutig
sind.

## Sicherheit, Datenschutz und Rechte

- Die Seite verlangt eine normale angemeldete SimpleOffice-Sitzung.
- Eine Löschung gehört dem WebDAV-Benutzer, der sie ausgelöst hat. Nur dieses
  Konto kann sie auflisten oder wiederherstellen; unberechtigte Direktaufrufe
  werden wie eine unbekannte Ressource behandelt.
- Bestätigung, erwarteter Hash, tatsächlicher Payload-Hash, regulärer Dateityp,
  Symlink-Grenzen und Zielpfad werden serverseitig geprüft.
- Interne Pfade, Steuerdateien, absolute Pfade, `..`, NUL-Zeichen und
  Symlink-Ziele sind ausgeschlossen.
- Es gibt keine externe Übertragung, automatische Freigabe oder neue
  Berechtigung. Bestehende Freigabelinks werden nicht neu erzeugt.
- Dateiinhalte erscheinen nicht im Audit; protokolliert werden nur notwendige
  Identifikatoren und Prüfsummen.

## Speicherung, Audit und Konflikte

Beim Soft-Delete speichert SimpleOffice Löschbenutzer, Ursprung, Zeitpunkt und
den genauen privaten Wiederherstellungspfad. Ältere Tombstones ohne die neuen
Felder bleiben kompatibel: Besitzer und Datei werden eingeschränkt aus dem
bestehenden Lösch-Audit und dem dokumentbezogenen Papierkorb ermittelt.

Eine erfolgreiche Dateirückholung erzeugt `document_restored`. Eine
Inhaltsrückholung erzeugt neben der normalen neuen Inhaltsrevision zusätzlich
`document_content_restored` und `content_recovery_history`. Beide Aktionen
werden auch in der Git-basierten Historie festgehalten.

Konflikte führen zu einem verständlichen Abbruch ohne Teiländerung:

- Zielname existiert bereits;
- Seite basiert auf einem veralteten Hash;
- Archiv fehlt, ist ein Symlink, wurde verändert oder überschreitet das Limit;
- Zielordner fehlt oder Pfad verlässt den Dokumentenspeicher;
- Dokument wurde für manuelle Löschung oder durch eine Arbeitsfrist gesperrt;
- Wiederherstellung gehört zu einem anderen Benutzer.

## Fehler- und Ausfallverhalten

Der aktive Dateibaum wird unter einer exklusiven Inhaltssperre geprüft und
verändert. Das Verschieben einer Soft-Delete-Datei in den aktiven Baum ist eine
atomare Dateisystemoperation. Metadaten, Suchindex und Fingerprint werden erst
danach aktualisiert. Schlägt die Prüfung vorher fehl, bleibt die
Wiederherstellungsdatei unverändert. Ein vorhandenes Ziel wird unter keinen
Umständen entfernt oder ersetzt.

Bei einer Inhaltswiederherstellung wird der aktuelle Inhalt zuerst
hashverifiziert archiviert; erst dann ersetzt eine temporär geschriebene Datei
atomar den aktiven Inhalt. Fehlerhafte Archive werden nicht geöffnet oder
kopiert. Ein Prozessabbruch nach der atomaren Dateibewegung, aber vor Abschluss
aller Metadaten kann wie andere Dateisystemänderungen durch den nächsten Scan
erkannt werden; das Audit sollte anschließend betrieblich geprüft werden.

## Migration und Rückwärtskompatibilität

Es gibt keine destruktive Migration. Bereits aktive Dateien, alte
LibreOffice-URLs, Dokument-IDs, App-Passwörter und WebDAV-Pfade bleiben
unverändert. Bereits vorhandene Soft-Delete-Einträge aus dem aktuellen
WebDAV-Ausbau werden anhand ihrer bestehenden `location_history` erkannt.
Fehlt deren Payload oder stimmt der Hash nicht, werden sie sichtbar als nicht
wiederherstellbar markiert und niemals geraten.

## Tests

Automatisierte Positiv-, Negativ-, Rechte-, Konflikt- und
Interoperabilitätstests prüfen:

- Soft-Delete über eine echte WebDAV-`DELETE`-Anfrage und anschließende
  bestätigte Rückholung;
- stabile Dokument-ID, Inhalt, Zielpfad, Suchzustand, Recovery- und Git-Audit;
- Benutzertrennung bei Liste und direktem POST;
- fehlende Bestätigung, belegtes Ziel und manipulierten Payload;
- kompatible Wiederherstellung eines alten Tombstones;
- Inhaltsrückholung als neue Revision mit archiviertem aktuellem Inhalt;
- veraltete Seitenvorbedingung und bestehende Aufbewahrungssperre;
- Darstellung der Wiederherstellungsseiten und Hashwerte.

Die vollständige Suite läuft zusätzlich unter allen in GitHub Actions
konfigurierten Python-Versionen sowie mit Dependency Audit.

## Bekannte Grenzen

Die private Ablage hat noch keine konfigurierbare Quota und keine automatische
Lebenszyklusbereinigung. Das ist absichtlich sicherer als eine unbemerkte
Löschung, kann aber Speicher belegen. Ein Betreiber muss sie zusammen mit den
aktiven Dokumenten sichern und Kapazität überwachen. DeltaV-Clients sehen keine
Versionen, und Desktop-Clients können keine alte Version direkt auswählen.

## Deaktivierung und Rückkehr

Das Widerrufen aller WebDAV-Gerätezugänge verhindert neue Desktop-Änderungen
und Löschungen sofort. Die Wiederherstellungsnavigation und die beiden
POST-Routen können bei Bedarf entfernt werden, ohne Datenformat oder aktiven
Dateibaum zu ändern. Bereits archivierte Inhalte und Auditdaten bleiben dabei
unberührt. Sie dürfen nur nach bestehendem Backup-, Fristen- und
Aussonderungsverfahren manuell entfernt werden.
