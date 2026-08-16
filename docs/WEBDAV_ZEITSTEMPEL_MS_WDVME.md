# WebDAV-Zeitstempel und ausgewählte Windows-Dateieigenschaften

## Zweck und Nutzen

SimpleOffice liefert für Dateien und Ordner persistente Erstellungszeiten,
HTTP-konforme Änderungszeiten und die von Windows-WebDAV-Clients häufig
abgefragten Dateiindikatoren. Dadurch können Windows Explorer, LibreOffice,
Nautilus, Finder und ein eingehängtes FreeFileSync-Ziel Ressourcen
einheitlicher anzeigen und Metadaten beim serverseitigen Kopieren erhalten.

Die Zeitwerte sind Anzeige- und Herkunftsmetadaten. Für Synchronisation und
Konfliktschutz bleiben der starke `getetag`, `If-Match`, Lock-Token und bei
geeigneten Clients der Sync-Token maßgeblich. Insbesondere darf
`creationdate` nicht als Ersatz für einen Inhaltsvalidator verwendet werden.

## Maßgebliche Standards

### RFC 4918

| Stufe | Originalanforderung | Umsetzung und Designentscheidung |
|---|---|---|
| SHOULD | Eine persistente Ressource sollte `DAV:creationdate` besitzen; der Wert folgt dem RFC-3339-Profil. Die Eigenschaft darf geschützt sein. | Neue Dateien erhalten die bereits revisionssicher gespeicherte erste Erfassungszeit, neue Ordner eine persistente Erstellungszeit in ihrer Ordnerpolitik. Ausgabe erfolgt kanonisch in UTC mit `Z`. Die Eigenschaft ist schreibgeschützt. Siehe [RFC 4918 §15.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-15.1). |
| SHOULD | `MOVE` sollte `creationdate` bewahren; eine durch `COPY` erzeugte Ressource erhält normalerweise einen neuen Wert. Clients sollten nicht allein danach synchronisieren. | Datei- und Ordner-MOVE erhalten die vorhandene Metadatenidentität. COPY erzeugt eine neue Datei-ID beziehungsweise Ordnerpolitik und damit eine neue Erstellungszeit. ETags und Sync-Token bleiben die Konfliktgrundlage. Siehe [RFC 4918 §15.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-15.1). |
| SHOULD | `DAV:getlastmodified` sollte geschützt sein, das RFC-1123-Datumsformat verwenden und bei per GET erreichbaren Ressourcen zu `Last-Modified` passen. | Dateien und Ordner liefern eine HTTP-Datumsangabe aus der aktuellen Dateisystemänderungszeit. Die Eigenschaft kann nicht per PROPPATCH gesetzt werden. Inhaltsänderungen ändern den Wert; reine Dead-Property-Änderungen an Dateien nicht. Siehe [RFC 4918 §15.7](https://www.rfc-editor.org/rfc/rfc4918.html#section-15.7). |
| MUST | PROPPATCH verarbeitet Anweisungen in Dokumentreihenfolge und muss vollständig atomar sein. | Unzulässige Windows-Werte ergeben für die Ursache `409` und für abhängige Änderungen `424`; kein Teil der Anfrage wird gespeichert. Rechte, HTTP-Bedingungen und Locks werden vor dem Commit geprüft. Siehe [RFC 4918 §9.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.2). |
| MUST/SHOULD | COPY und MOVE müssen die jeweiligen Property-Semantiken beachten; Dead Properties sollen am Ziel erhalten werden, soweit das Property-Verhalten nichts anderes verlangt. | Unterstützte Windows-Dead-Properties werden bei Datei- und Ordner-COPY kopiert und bei MOVE bewahrt. Live Properties werden stets aus der Zielressource neu berechnet. Siehe [RFC 4918 §9.8.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.2) und [§9.9.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9.1). |
| SHOULD | Server sollen unnötige personenbezogene Angaben in Properties vermeiden. | Ordner speichern intern den anlegenden Benutzer für Audit und Herkunft, geben ihn aber nicht als öffentliche Live Property aus. Windows-Property-Werte werden im Audit nur mit ihrem Namen, nicht mit ihrem Inhalt protokolliert. Siehe [RFC 4918 §20.5](https://www.rfc-editor.org/rfc/rfc4918.html#section-20.5). |

### Ausgewählte Microsoft-WebDAV-Erweiterungen

Microsoft beschreibt zusätzliche Eigenschaften für Windows-WebDAV-Clients in
[MS-WDVME: Server Processing of Client Properties](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-wdvme/b24bb05a-b847-483b-b0b2-ea34435a0c9b)
und die umgekehrte Richtung in
[Client Processing of Server Properties](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-wdvme/23aa2795-9b78-44dc-8c57-dd252d591424).
SimpleOffice implementiert bewusst nur den für eine normale Dateiverwaltung
nützlichen, gefahrlosen Ausschnitt:

| Namespace und Property | Verhalten |
|---|---|
| `urn:schemas-microsoft-com:Win32FileAttributes` | Begrenzte Zeichenkette als Dead Property; wird nicht in Betriebssystemrechte oder SimpleOffice-ACLs übersetzt. Definition: [MS-WDVME §2.2.3.3](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-wdvme/f83d826b-7fad-4f80-838c-5c7cc98cb59f). |
| `Win32CreationTime`, `Win32LastAccessTime`, `Win32LastModifiedTime` im selben Namespace | Begrenzte Zeichenketten werden unverändert gespeichert und beim COPY übernommen. Sie ersetzen weder die geschützten DAV-Zeiten noch Dateisystemzeiten. Beispiele: [Win32CreationTime](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-wdvme/eabdcf12-d625-4b4e-84ed-7704dbc8e5cf), [Win32LastModifiedTime](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-wdvme/a4d8996b-319d-4dfe-9597-8642973dd275). |
| `urn:schemas-microsoft-com:office:office:specialFolderType` | Eine kanonische vorzeichenbehaftete 32-Bit-Ganzzahl wird als Dead Property gespeichert; andere Werte werden atomar abgewiesen. Definition: [MS-WDVME §2.2.3.7](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-wdvme/17047eed-9b5f-499a-abaf-5917edf10df9). |
| `DAV:iscollection`, `DAV:isFolder`, `DAV:ishidden` | Geschützte, serverberechnete Hinweise (`1/0` beziehungsweise `t/f`). Ein führender Punkt kennzeichnet eine normale sichtbare Ressource als verborgen; interne SimpleOffice-Pfade werden weiterhin überhaupt nicht angeboten. |

Die Namespace-Zuordnung folgt Microsofts
[MS-WDVME-Namespace-Tabelle](https://learn.microsoft.com/en-us/openspecs/sharepoint_protocols/ms-wdvme/f75e7612-e913-403f-89d2-f46ddb6e30c8).
Dies ist ausdrücklich **keine vollständige MS-WDVME- oder SharePoint-
Implementierung**. Replikationseigenschaften, `Office:modifiedby`,
`ResourceTag`, NTLM, FrontPage-RPC und SharePoint-Listenverhalten sind nicht
implementiert oder beworben.

## Datenmodell und Lebenszyklus

- Eine neue Datei verwendet `first_seen_at` aus ihrer Dokumentmetadatei als
  persistente Erstellungszeit. Ein Inhaltsupdate ändert diesen Wert nicht.
- Eine neue Sammlung erhält `created_at` und `created_by` in der bestehenden,
  privaten Ordnerpolitik. Die Politik wird zusammen mit dem Ordner verschoben.
- Eine Kopie erhält eine neue Dokument-ID beziehungsweise neue Ordnerpolitik.
  Dadurch ist ihre Erstellungszeit von der Quelle unabhängig.
- Vorhandene ältere Ordnerpolitiken werden nicht automatisch umgeschrieben.
  Fehlt eine nachweisbare persistente Erstellungszeit, meldet PROPFIND die
  angeforderte `creationdate` mit `404`, statt einen unzuverlässigen Wert zu
  erfinden. Neue Ordner sind vollständig versorgt.
- `getlastmodified` wird aus dem Ressourcenstatus erzeugt und im von HTTP
  geforderten Datumsformat ausgegeben. Eine Sekundengenauigkeit ist
  protokollbedingt; der starke ETag erkennt Inhaltsänderungen genauer.

## Bedienung und Client-Kompatibilität

Es ist keine zusätzliche Client-Konfiguration erforderlich. Die vorhandene
HTTPS-WebDAV-Adresse und ein separater Gerätezugang werden wie in
[WEBDAV_DATEIVERWALTUNG.md](WEBDAV_DATEIVERWALTUNG.md) eingerichtet.

- **Windows Explorer und Microsoft Office:** lesen die DAV-Zeiten und können
  die ausgewählten Microsoft-Dead-Properties setzen. Andere SharePoint-
  Erweiterungen werden wie bisher ignoriert oder als unbekannte Dead
  Properties innerhalb der allgemeinen Schutzgrenzen behandelt.
- **LibreOffice:** nutzt weiterhin ETags und Locks für das Speichern. Die
  zusätzlichen Properties ändern weder Dokumentformat noch Sperrablauf.
- **Nautilus und Finder:** erhalten standardisierte Erstellungs- und
  Änderungszeiten; unbekannte Microsoft-Namespaces dürfen sie ignorieren.
- **FreeFileSync:** verwendet den vom Betriebssystem eingehängten WebDAV-
  Ordner. Für bidirektionale Synchronisation bleiben Dateiinhalte, ETags und
  eine geprüfte Vorschau maßgeblich; Windows-Zeitzeichenketten dürfen keine
  automatische Konfliktentscheidung erzwingen.

## Rechte, Sicherheit und Datenschutz

- Lesen folgt dem Benutzer- und optionalen Ordnerumfang des Gerätezugangs.
  PROPPATCH benötigt ausdrücklich Schreibumfang und beachtet Retention,
  Ressourcen-Locks, `If-Match` und getaggte `If`-Bedingungen.
- Microsoft-Dateiattribute sind reine Interoperabilitätsmetadaten. Sie können
  keine Berechtigungen lockern, keine Datei außerhalb des Dokumentstamms
  verbergen und keine Aufbewahrungsregel verändern.
- Eigenschaftswerte dürfen weder Kindelemente noch Attribute oder NUL-Zeichen
  enthalten und sind auf 256 UTF-8-Bytes begrenzt. `specialFolderType` muss
  kanonisch im Bereich `-2147483648` bis `2147483647` liegen.
- Namen geänderter Properties, Benutzer, Ressource und Zeitpunkt werden
  vollständig auditiert. Werte werden aus Datenschutzgründen nicht in das
  Ereignisjournal dupliziert.
- Es findet keine externe Übertragung statt. Basic Authentication bleibt nur
  über HTTPS zulässig; App-Passwörter gehören nicht in Dateien oder Logs.

## Fehler- und Ausfallverhalten

- `207 Multi-Status` trennt bei PROPFIND vorhandene (`200`) und nicht
  persistierbare ältere Properties (`404`).
- `403` kennzeichnet geschützte DAV-/Microsoft-Live-Properties oder fehlende
  Schreibrechte, `409` einen semantisch ungültigen Wert und `424` die deshalb
  nicht ausgeführte restliche atomare Änderung.
- `412` schützt einen neueren Ressourcenstand, `423` eine aktive Sperre und
  `413` die feste XML-/Wertgrößengrenze.
- Ein Fehler vor dem atomaren Metadaten-Commit lässt alle bisherigen
  Properties unverändert. Eigenschaftsänderungen verändern keinen
  Dateiinhalt, keine Erstellungszeit und keine Freigabe.

## Migration, Rückwärtskompatibilität und Rückkehr

Es gibt keine Datenbank- oder Inhaltsmigration. Neue Ordnerpolitiken verwenden
Schema-Version 2 mit zusätzlichen Herkunftsfeldern; Leser akzeptieren
unverändert die vorherige Version. Dateien, ältere Ordner, App-Passwörter,
ACLs, Versionen und Aufbewahrungsregeln bleiben unverändert. Clients ohne
Kenntnis der Microsoft-Namespaces arbeiten ausschließlich mit DAV weiter.

Ein Widerruf des Gerätezugangs deaktiviert die Desktop-Nutzung sofort. Das
Entfernen des WebDAV-Blueprints kehrt zum früheren Verhalten zurück, ohne
Dateien oder Metadaten umzuschreiben. Bereits gespeicherte Dead Properties
können mit einem gültigen schreibenden PROPPATCH gezielt entfernt werden.

## Automatisierte Tests

Die WebDAV-Suite prüft zusätzlich:

- RFC-3339-`creationdate` und HTTP-`getlastmodified` für Datei und Sammlung;
- stabile Erstellungszeit nach Inhaltsänderung und MOVE sowie neue Zeit nach
  COPY;
- PROPPATCH-Schutz für beide DAV-Zeitwerte;
- Roundtrip und COPY der vier Win32-Eigenschaften und von
  `specialFolderType`;
- berechnete Ordner-/Verbergen-Hinweise;
- atomaren `409`/`424`-Rollback, Wertebegrenzung, Lock-/Rechteprüfung und
  datensparsames Audit.

Bekannte Grenze: Reale Windows-, macOS- und Linux-Zeitstempel lassen sich
nicht verlustfrei ineinander abbilden. SimpleOffice bewahrt deshalb fremde
Win32-Zeichenketten separat und stellt keine Änderung der lokalen
Dateisystemzeiten oder ACLs in Aussicht.
