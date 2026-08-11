# Ordnergebundene WebDAV-Gerätezugänge nach dem Prinzip kleinster Rechte

## Zweck und Nutzen

Ein App-Passwort muss nicht mehr den gesamten WebDAV-Dateibaum öffnen. Beim
Anlegen kann ein vorhandener Ordner wie `Projekte/Kunde-A` gewählt werden. Der
Zugang sieht genau diese Sammlung und alle Unterordner. Geschwister, Eltern und
die WebDAV-Wurzel bleiben verborgen. Der Ordnerumfang wird mit **Nur lesen**
oder **Lesen und schreiben**, einer Gerätebezeichnung und einem Ablaufdatum
kombiniert.

Das ist insbesondere für folgende Fälle gedacht:

- LibreOffice darf nur Dokumente eines Projekts direkt öffnen und speichern;
- FreeFileSync gleicht nur einen ausdrücklich bestimmten Arbeitsordner ab;
- Nautilus, Windows Explorer oder Finder binden einen Team- oder Geräteordner
  ein, ohne andere Dokumentnamen zu sehen;
- ein lesender Prüf- oder Backup-Client erhält weder Schreibrechte noch
  Zugriff auf fachlich fremde Ordner.

Der Umfang ist eine serverseitig erzwungene Grenze des technischen
Geräte-Principals. Er ersetzt keine fachliche Freigabe und kann bestehende
Dokument-, Bearbeitungs-, Aufbewahrungs- oder Quarantäneregeln nicht lockern.

## Primärstandards und ausgewählte Anforderungen

### WebDAV Access Control – RFC 3744

SimpleOffice implementiert in diesem Ausbau bewusst **nicht** das komplette
WebDAV-ACL-Protokoll und kündigt daher keine ACL-Compliance an. Die normativen
Privilegien und Methodenabhängigkeiten werden als Sicherheitsmodell für den
festen Geräteumfang verwendet.

| Anforderung | Stufe | Abgeleitete und implementierte Entscheidung |
|---|---|---|
| `DAV:read` kontrolliert das Lesen des Inhalts und gewöhnlicher Eigenschaften einer Ressource. | MUST – [RFC 3744 §3.1](https://www.rfc-editor.org/rfc/rfc3744.html#section-3.1) | Ein Ordnerzugang darf `PROPFIND`, `GET`, `HEAD` und `REPORT` nur am Grenzordner und darunter ausführen. |
| `DAV:write` fasst Schreibprivilegien zusammen; `DAV:write-properties` und `DAV:write-content` unterscheiden Metadaten- und Inhaltsänderungen. | MUST – [§3.2–3.4](https://www.rfc-editor.org/rfc/rfc3744.html#section-3.2) | Der vorhandene Umfang `read`/`write` bleibt eine konservative Positivliste. Ein Lesezugang wird vor `PUT`, `PROPPATCH`, `MKCOL`, `COPY`, `MOVE`, `DELETE`, `LOCK` und `UNLOCK` mit `403` abgewiesen. |
| Ein Server muss vor Ausführung einer Methode die erforderlichen Privilegien prüfen. | MUST – [§7.1](https://www.rfc-editor.org/rfc/rfc3744.html#section-7.1), [Appendix B](https://www.rfc-editor.org/rfc/rfc3744.html#appendix-B) | Authentifizierung, Benutzerpfad, Ordnergrenze und Lese-/Schreibumfang werden vor Datei-, Lock-, Sync- oder Auditmutation geprüft. |
| `MOVE` benötigt Rechte am Quell- und Zielkontext; `COPY` benötigt das Binden am Ziel. | MUST – [§7.3](https://www.rfc-editor.org/rfc/rfc3744.html#section-7.3), [§7.4](https://www.rfc-editor.org/rfc/rfc3744.html#section-7.4) | Quellressource und `Destination` müssen innerhalb derselben Geräteordnergrenze liegen. Ein Verschieben oder Kopieren hinaus wird vor jeder Mutation abgewiesen. |
| Ein Lock beeinflusst Inhaltsänderungen und das Entsperren ist ein eigenes Privileg. | MUST – [§3.5](https://www.rfc-editor.org/rfc/rfc3744.html#section-3.5), [§7.5](https://www.rfc-editor.org/rfc/rfc3744.html#section-7.5) | `LOCK` und `UNLOCK` sind nur mit Schreibzugang und nur innerhalb des Ordners möglich. Ein bekannter Token hebt die Ordnergrenze nicht auf. |
| ACL-Auswertung umfasst Principal- und Gruppenbeziehungen. | MUST für ACL-Server – [§6](https://www.rfc-editor.org/rfc/rfc3744.html#section-6) | Nicht implementiert: Die Gerätekennung ist kein durch `DAV:principal-URL` auffindbarer ACL-Principal. Es gibt keine vom Desktop-Client les- oder änderbare `DAV:acl`. |
| ACL-Informationen können selbst sensible Namen und Strukturen offenlegen. | Sicherheitsfolge – [§12](https://www.rfc-editor.org/rfc/rfc3744.html#section-12) | Die Oberfläche zeigt den Ordner nur dem angemeldeten Browserbenutzer. WebDAV-Antworten außerhalb der Grenze enthalten weder Eigenschaften noch Verzeichnislisten. |

### WebDAV, HTTP und Authentifizierung

| Anforderung | Stufe | Umsetzung |
|---|---|---|
| Autorisierung muss vor einer WebDAV-Methode erfolgen; der Server darf einen authentifizierten Principal trotzdem wegen fehlender Berechtigung abweisen. | MUST / Semantik – [RFC 4918 §8.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-8.3), [§20.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-20.1) | Ungültige Zugangsdaten erhalten `401`; ein lesender Gerätezugang erhält für Schreibmethoden `403`. |
| `404 Not Found` darf verwendet werden, wenn der Server die Existenz einer Zielressource nicht offenlegen will. | MAY – [RFC 9110 §15.5.5](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.5) | Eltern, Geschwister, stabile Dokument-URLs und `OPTIONS`-Ziele außerhalb des Geräteordners antworten einheitlich mit `404`. |
| Basic Authentication schützt das Kennwort nicht ohne eine vertrauliche Transportverbindung. | Sicherheitsanforderung – [RFC 7617 §4](https://www.rfc-editor.org/rfc/rfc7617.html#section-4) | App-Passwörter dürfen außerhalb des lokalen Rechners ausschließlich über HTTPS verwendet werden. Sie werden nicht in URLs, Audit oder HTML-Quelltext wiederholt. |
| Ein Sync-Token ist an die Sammlung gebunden, für die er ausgestellt wurde. | MUST – [RFC 6578 §3.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.2), [§5](https://www.rfc-editor.org/rfc/rfc6578.html#section-5) | `REPORT` startet am sichtbaren Grenzordner. Auch ein getaggter `If`-Header darf keinen Token einer Sammlung außerhalb der Geräteordnergrenze verwenden. |

## Datenmodell und Rechteprüfung

Jeder neue Eintrag in `webdav-credentials.json` enthält zusätzlich
`path_prefix`. Gespeichert wird ein normalisierter, relativer Pfad ohne
führenden oder abschließenden Schrägstrich. Ein leerer Wert bedeutet aus
Kompatibilitätsgründen den gesamten verwalteten Dateibaum.

Beim Erzeugen gelten folgende Prüfungen:

1. Der Wert ist höchstens 500 druckbare Zeichen lang.
2. Absolute Pfade, `..`, Steuerverzeichnisse, Policy-Dateien und Nullbytes sind
   verboten.
3. Der Zielordner muss bereits als reguläres Verzeichnis existieren.
4. Symlinks und jeder Pfad durch einen Symlink werden abgewiesen.

Nach erfolgreicher Basic-Authentifizierung trägt die interne Identität
Benutzername, Gerätekennung, `read`/`write` und Ordnergrenze. Jede reale
Ressource wird normalisiert und muss entweder der Grenzordner selbst oder ein
Nachkomme sein. Die Prüfung gilt für:

- `OPTIONS`, `PROPFIND`, `GET`, `HEAD`, `REPORT`;
- `PUT`, `PROPPATCH`, `MKCOL`, `DELETE`;
- Quelle und Ziel von `COPY` und `MOVE`;
- `LOCK`, Lock-Refresh und `UNLOCK`;
- getaggte Sync-Token-Bedingungen;
- die stabile Dokument-ID-URL unter `/webdav/documents/...`.

Die virtuelle stabile Dokumentliste enthält ausschließlich Dokumente
innerhalb des Ordners. Ein gültiges App-Passwort kann daher weder Dateinamen
noch IDs aus einem Geschwisterordner ermitteln.

Der Grenzordner selbst darf nicht per `DELETE` entfernt werden: Dafür wäre
nach dem Privilegienmodell das Entfernen der Bindung in seinem nicht sichtbaren
Elternordner nötig. Leere Unterordner innerhalb der Grenze bleiben mit den
bisherigen Sicherheitsregeln löschbar.

## Bedienung und Desktop-Einrichtung

1. In SimpleOffice ein Dokument öffnen und **In LibreOffice bearbeiten**
   wählen.
2. Gerätebezeichnung, vorhandenen relativen Ordner, Rechte und Gültigkeit
   festlegen. Für ein einzelnes Projekt den Projektordner statt eines leeren
   Werts verwenden.
3. Das App-Passwort einmalig kopieren.
4. In der Tabelle **Gerätezugänge** die zu genau diesem Zugang gehörende
   WebDAV-Adresse kopieren. Sie endet beim gewählten Ordner.
5. SimpleOffice-Benutzername und App-Passwort im Passwortspeicher des Clients
   hinterlegen.

### LibreOffice

Unter **Datei → Remote öffnen → Dienste verwalten → WebDAV** die angezeigte
HTTPS-Adresse verwenden. Alternativ lässt sich die stabile URL des sichtbaren
Dokuments unter **Datei → Öffnen** einfügen. Für Speichern mit `Strg+S` ist
**Lesen und schreiben** nötig. LibreOffice-Locks und ETag-Prüfungen bleiben
innerhalb des Ordners wirksam.

### Nautilus / GNOME Files

Unter **Andere Orte → Mit Server verbinden** die gerätespezifische
`davs://`-Adresse verwenden. Der sichtbare Einstieg ist der Grenzordner; ein
Wechsel zu dessen Eltern ist serverseitig nicht möglich.

### Windows-Datei-Explorer und macOS Finder

Die HTTPS-Adresse als Netzwerkspeicher beziehungsweise über **Gehe zu → Mit
Server verbinden** eintragen. Das App-Passwort getrennt vom Browserkennwort
speichern. Windows WebClient verlangt in der Praxis ein vertrauenswürdiges
TLS-Zertifikat; Basic Auth über Klartext-HTTP ist nicht vorgesehen.

### FreeFileSync

Die gerätespezifische Adresse als WebDAV-Ziel wählen. Für einen reinen
Server-Export genügt **Nur lesen**; ein bidirektionaler Abgleich benötigt
**Lesen und schreiben**. Vor dem ersten produktiven Lauf Vorschau und
Konfliktstrategie prüfen. `COPY`, `MOVE`, ETags, Locks und Sync-Token können
die Ordnergrenze nicht verlassen.

## Sicherheit, Datenschutz, Freigaben und Audit

- Der Zugriff ist standardmäßig deaktiviert und entsteht nur nach bewusster
  Browseraktion. Es werden keine Dateien automatisch freigegeben.
- App-Passwort und Hash erscheinen weder im Audit noch in der Gerätetabelle.
- Erzeugen und Widerrufen protokollieren zusätzlich die normalisierte
  Ordnergrenze. Damit ist später nachvollziehbar, welche Reichweite ein Gerät
  besaß.
- Schreibvorgänge behalten die bestehende vollständige Datei-, Versions- und
  Feldhistorie. Der Geräteordner schwächt keine Bearbeitungs- oder
  Aufbewahrungssperre.
- Ein Ordnername ist für den Browserbenutzer und in dessen eigener
  Revisionshistorie sichtbar. Er wird nicht an externe Dienste übertragen.
- `404` außerhalb der Grenze verhindert absichtliche Namensauskunft, ersetzt
  aber keine Rate-Begrenzung am Reverse Proxy.

## Fehler- und Ausfallverhalten

- `400`/Hinweis in der Weboberfläche: Ordnerwert syntaktisch ungültig.
- `401`: App-Passwort fehlt, ist falsch, abgelaufen oder widerrufen.
- `403`: gültiger Lesezugang versucht eine Schreibmethode.
- `404`: Ziel liegt außerhalb der Ordnergrenze oder existiert nicht. Beide
  Fälle sind absichtlich nicht unterscheidbar.
- `412`: ETag, `If-Match`, `If-None-Match` oder Sammlungstoken ist veraltet
  beziehungsweise gehört nicht in die sichtbare Grenze.
- `423`: sichtbare Ressource ist gesperrt oder der Lock-Token passt nicht.
- `502`: `COPY`/`MOVE` versucht ein Ziel außerhalb des erlaubten Benutzer- und
  Gerätebaums.

Fehlgeschlagene Grenzprüfungen erfolgen vor Datei-, Lock-, Sync- und
Auditmutation. Ein Serverabbruch ändert deshalb keine Ressource teilweise; die
bestehende atomare Speicherung bleibt unverändert.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenbank- oder Dateimigration. Bestehende Mehrgeräte-Einträge
ohne `path_prefix` sowie der historische Einzelzugang werden als leerer Prefix
gelesen und behalten exakt ihren bisherigen Gesamtbaumzugriff. Diese bewusste
Kompatibilität verhindert, dass bereits eingerichtete LibreOffice- oder
FreeFileSync-Verbindungen unerwartet ausfallen.

Neue Zugänge können sofort enger erstellt werden. Für die Umstellung eines
Altgeräts wird zuerst ein ordnergebundener Ersatz erzeugt und im Client
getestet; danach wird ausschließlich der alte Zugang widerrufen. Dateien,
Versionen, Locks, Sync-Journale und Aufbewahrungsfristen werden dabei nicht
verändert.

## Automatisierte Tests

Die Tests decken positiv und negativ ab:

- Normalisierung eines Ordners sowie Ablehnung von Traversal,
  Steuerverzeichnissen und nicht vorhandenen Sammlungen;
- Anzeige der gerätespezifischen Adresse ohne Hash oder Geheimnis;
- Audit-Snapshot mit Ordnergrenze;
- `PROPFIND`, `GET` und stabile Dokument-ID-URLs innerhalb der Grenze;
- `404` für Wurzel, Geschwister, fremde stabile URLs und `OPTIONS` außerhalb;
- `PUT`, `MKCOL`, `COPY` innerhalb der Grenze;
- `403` beim Löschen des Grenzordners, aber unveränderte Sammlung;
- abgewiesenes `MOVE`, `LOCK` und getaggtes Sammlungstoken außerhalb;
- Kombination aus ordnergebundenem Zugriff und reinem Leserecht;
- unveränderte Funktion aller alten unbeschränkten Zugangsdaten.

## Bewusst nicht implementiert und bekannte Grenzen

- Keine WebDAV-ACL-Bearbeitung mit `ACL`, `DAV:acl`, Principal-Discovery oder
  Gruppen. Die feste Geräteordnergrenze ist kleiner, verständlicher und kann
  nicht von einem Desktop-Client erweitert werden.
- Ein Zugang umfasst genau einen Ordner mit allen Nachkommen. Mehrere
  voneinander getrennte Ordner benötigen getrennte Gerätezugänge.
- Das nachträgliche Ändern der Grenze ist absichtlich nicht möglich. Erzeugen
  und Widerrufen ergibt eine eindeutige Auditkette und verhindert stille
  Rechteausweitung.
- Wird der Grenzordner später umbenannt oder gelöscht, folgt das Zugangsmuster
  nicht automatisch. Der Client erhält `404`; es muss ein neuer Zugang für
  den neuen Pfad erzeugt werden.
- Betriebssystemeigene WebDAV-Caches können eine alte Verzeichnisliste kurz
  anzeigen. Der Server liefert dafür keine Inhalte mehr und akzeptiert keine
  Mutation außerhalb der aktuellen Grenze.

## Deaktivierung und Rückkehr

**Widerrufen** deaktiviert nur den gewählten Gerätezugang. **Alle Zugänge
widerrufen** schaltet WebDAV für den Benutzer vollständig ab. Wer bewusst zum
früheren Gesamtbaumverhalten zurückkehren muss, erzeugt einen neuen Zugang mit
leerem Ordnerfeld; dies ist eine explizite neue Berechtigung und keine
automatische Lockerung. Keine dieser Aktionen löscht Dokumente oder verändert
Aufbewahrungsregeln.
