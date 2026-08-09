# WebDAV-Ordner kopieren und verschieben nach RFC 4918

## Zweck und Nutzen

SimpleOffice kann vollständige Ordnerbäume über WebDAV serverseitig kopieren
und verschieben. Nautilus, Windows-Datei-Explorer, macOS Finder und
FreeFileSync müssen deshalb nicht mehr jede Datei einzeln herunterladen und
wieder hochladen. Ein Umbenennen oder Verschieben innerhalb der Ablage bleibt
auf demselben Dateisystem atomar; beim Kopieren entstehen eigenständige
Dokumente mit neuer ID und ohne übernommene Freigaben.

Die Funktion gilt ausschließlich im benutzergebundenen Pfad
`/webdav/files/<benutzer>/`. App-Passwort, HTTPS-Empfehlung, Schreibrecht und
ein eventuell gesetzter Ordnerbereich gelten unverändert für Quelle und Ziel.

## Ausgewerteter Primärstandard

Maßgeblich ist [RFC 4918](https://www.rfc-editor.org/rfc/rfc4918.html):

| Anforderung | Norm | Umsetzung |
| --- | --- | --- |
| `Destination` ist für COPY und MOVE erforderlich. | [§ 9.8](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8), [§ 9.9](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9) | Fehlt der Header, antwortet der Server mit `400`. Fremde Hosts, Benutzer und nicht vom Gerätezugang abgedeckte Ziele werden abgewiesen. |
| Collection-COPY ohne `Depth` verhält sich wie `Depth: infinity`; `0` und `infinity` müssen unterstützt werden. | [§ 9.8.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.3) | Beide Varianten sind implementiert. `Depth: 0` kopiert nur den Ordner und seine Dead Properties; Standard ist rekursiv. Andere Werte ergeben `400`. |
| Rekursives COPY muss die Hierarchie am Ziel konsistent abbilden und darf sich nicht in sich selbst kopieren. | [§ 9.8.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.3) | Pfade werden vorab vollständig geprüft. Ziele innerhalb der Quelle ergeben `403`; erst danach beginnt die Mutation. |
| Dead Properties sollten beim COPY dupliziert werden; Creation-Live-Properties dürfen neue Werte erhalten. | [§ 9.8.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.2) | Benutzerdefinierte WebDAV-Eigenschaften werden für jeden kopierten Ordner und jede Datei übernommen. Neue Ordner- und Dokument-IDs, Zeitwerte und Freigaben werden bewusst neu erzeugt. |
| Collection-MOVE ist immer rekursiv; Clients dürfen nur `Depth: infinity` senden. | [§ 9.9.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9.2) | Ohne Header oder mit `infinity` wird der gesamte Baum verschoben. Andere Werte ergeben `400`. |
| Dead Properties müssen beim MOVE erhalten bleiben. | [§ 9.9.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9.1) | Stabile Ordner- und Dokument-IDs ziehen mit dem atomar umbenannten Baum um; die Property-Zuordnung bleibt dadurch unverändert. |
| Ein erfolgreicher MOVE darf Quell-Locks nicht an das Ziel verschieben; vorhandene Zielbereich-Locks gelten anschließend. | [§ 7.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.6) | Alle expliziten Locks innerhalb der Quelle werden nach erfolgreichem MOVE auditierbar entfernt. Ein rekursiver Lock am Ziel-Elternordner wird dynamisch wirksam. COPY dupliziert keine Locks. |
| Für jede veränderte gesperrte Ressource muss das passende Token im `If`-Header stehen. | [§ 7.5](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.5) | MOVE prüft Quellwurzel, getrennt gesperrte Nachfahren und Zielbereich. COPY benötigt keinen Quelltoken, wohl aber Tokens des veränderten Zielbereichs. |
| `Overwrite: F` muss ein bestehendes Ziel schützen. | [§ 9.8.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.4), [§ 9.9.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9.3) | Bestehende Ziele ergeben immer `412`, auch bei `Overwrite: T`; siehe Sicherheitsentscheidung unten. |
| COPY und MOVE dürfen nicht gecacht werden. | [§ 9.8](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8), [§ 9.9](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9) | Mutationsantworten enthalten keine cachebare Repräsentation. |

### Bewusste Sicherheitsentscheidung gegenüber dem Standardumfang

RFC 4918 erlaubt bei `Overwrite: T` das Löschen oder Ersetzen eines bestehenden
Ziels. SimpleOffice führt diese Variante absichtlich nicht aus. Ein Desktop-
Client erhält `412 Precondition Failed` und muss einen eindeutigen Namen wählen
oder das Ziel in einem getrennten, bestätigten Schritt löschen. Dadurch kann
eine veraltete FreeFileSync- oder Dateimanager-Ansicht keine neuere Datei oder
Ordnerstruktur unbemerkt ersetzen.

RFC 4918 beschreibt bei einzelnen Fehlern in rekursiven Operationen eine
`207 Multi-Status`-Antwort und möglichst weit fortgesetzte Teiloperationen
([COPY § 9.8.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.3),
[MOVE § 9.9.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9.2)).
SimpleOffice prüft stattdessen den gesamten Baum vorab und behandelt ihn als
Transaktion. Ein nicht sicher verarbeitbares Mitglied verhindert die gesamte
Operation; ein unerwarteter COPY-Ausfall entfernt das sichtbare Teilziel und
legt bereits erzeugte Dateien in die bestehende Wiederherstellung. Diese
All-or-nothing-Regel vermeidet schwer erkennbare Teilkopien. Damit ist die
partielle `207`-Fortsetzung bewusst nicht implementiert.

## Bedienung

Nach Einrichtung des WebDAV-Zugangs können Ordner wie gewohnt im Desktop-
Client verschoben, umbenannt oder kopiert werden:

- Nautilus/GNOME Files: `dav(s)://server/webdav/files/<benutzer>/` verbinden,
  Ordner per Ausschneiden/Einfügen oder Drag-and-drop bewegen.
- Windows-Datei-Explorer: HTTPS-Webadresse als Netzwerkspeicher verbinden;
  normale Kopier-, Umbenenn- und Verschiebeaktionen verwenden.
- macOS Finder: „Mit Server verbinden“ und
  `https://server/webdav/files/<benutzer>/` angeben.
- FreeFileSync: den eingehängten WebDAV-Pfad als Ziel wählen. Serverseitige
  MOVE-/COPY-Anfragen werden genutzt, wenn die eingesetzte DAV-Schicht sie
  erzeugt; andernfalls funktionieren weiterhin einzelne PUT/MOVE-Abläufe.
- LibreOffice: Dokumente bleiben über denselben WebDAV-Baum direkt editierbar.
  Das Verschieben des Elternordners ändert die hierarchische URL; die interne
  Dokument-ID und die auditierte Historie bleiben erhalten.

Der bestehende Einrichtungsdialog „In LibreOffice bearbeiten“ erzeugt getrennte
App-Passwörter und zeigt die passende Stamm-URL. Für einen Synchronisations-
Client empfiehlt sich ein eigenes, auf den benötigten Ordner begrenztes
Schreibkennwort.

## Rechte, Sicherheit und Datenschutz

- Quelle und Ziel müssen demselben authentifizierten Benutzer gehören und im
  Bereich des Gerätezugangs liegen. Nicht sichtbare Pfade antworten ohne
  Informationsleck.
- Read-only-Zugänge dürfen COPY und MOVE nicht ausführen.
- Ein MOVE der Wurzel eines ordnergebundenen Gerätezugangs wird abgewiesen,
  weil dafür Rechte auf den nicht sichtbaren Elternordner nötig wären.
- Pfad-Traversal, reservierte SimpleOffice-Namen, Symlinks und Spezialdateien
  werden vor der ersten Änderung abgewiesen.
- Die Quelle darf höchstens 2.000 sichtbare Mitglieder und 64 Ebenen besitzen.
  Damit bleiben Laufzeit, Dateideskriptoren und Auditvolumen begrenzt.
- COPY prüft logisches Kontingent und freien physischen Speicher anhand der
  gesamten Dateigröße, bevor Daten dupliziert werden.
- Während einer Anfrage verhindert die gemeinsame WebDAV-Mutationssperre
  konkurrierende Änderungen durch andere WebDAV-Clients.
- Kopien erhalten neue Dokument- und Ordner-IDs. Tags, Beschreibung,
  Anhangsursprung, Malware-Scanstatus und Dead Properties werden übernommen;
  Benutzerfreigaben und Lock-Tokens ausdrücklich nicht.
- MOVE erhält stabile IDs, Tags, Dateihistorie und Dead Properties. Jede Datei
  erhält einen feldgenauen Ortsverlauf mit altem und neuem Pfad.
- Inhalte werden nicht an externe Dienste übertragen; es entstehen keine
  zusätzlichen Zugangsdaten oder Hintergrundverbindungen.

## Audit, Konflikte und Fehlerverhalten

Eine erfolgreiche Operation erzeugt pro Datei die bestehenden Ereignisse
`document_copied` beziehungsweise `document_moved` und zusätzlich eine
Zusammenfassung `webdav_collection_copied` oder `webdav_collection_moved` mit
Quelle, Ziel, Anzahl, Bytes, Benutzer und Zeit. Entfernte Quell-Locks werden als
`webdav_lock_released_by_move` protokolliert.

Wichtige Antworten:

- `201 Created`: vollständiger Erfolg;
- `400 Bad Request`: fehlendes/ungültiges Ziel, `Depth` oder `Overwrite`;
- `403 Forbidden`: Wurzeloperation, Credential-Grenze oder Ziel in der Quelle;
- `409 Conflict`: fehlender Ziel-Elternordner oder unsicherer Quellbaum;
- `412 Precondition Failed`: Ziel existiert oder HTTP-Bedingung ist veraltet;
- `423 Locked`: notwendiges Quell- oder Zieltoken fehlt;
- `507 Insufficient Storage`: Kontingent, Plattenplatz, Baumgröße oder
  unerwarteter Schreibfehler.

Bei COPY werden bereits sichtbar erzeugte Dokumente nach einem unerwarteten
Fehler soft-gelöscht und bleiben über die Wiederherstellung nachvollziehbar.
MOVE verwendet für die Ordnerabbildung ein atomares Rename auf demselben
Dokumenten-Dateisystem. Schlägt eine nachfolgende Metadatenaktualisierung fehl,
werden Ordner und Metadatensnapshots auf die Quelle zurückgesetzt und der
Rollback revisionssicher vermerkt.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenmigration. Vorhandene Dateien, IDs, Freigaben, Locks,
Aufbewahrungsregeln und Gerätekennwörter bleiben unverändert. Ältere Clients,
die Ordner weiterhin Datei für Datei übertragen, funktionieren wie bisher.

## Automatisierte Tests

Die Tests decken insbesondere ab:

- rekursives COPY mit mehreren Ebenen und unabhängigen Dokument-IDs;
- `Depth: 0`, Standard-`infinity` und ungültige Depth-Werte;
- Erhalt von Tags und Dead Properties ohne Übernahme von Freigaben;
- rekursives MOVE mit stabilen IDs und aktualisierter Orts-/Audit-Historie;
- RFC-konformes Freigeben von Quell-Locks nach MOVE;
- Quell-/Ziel- und Nachfahr-Locks sowie ordnergebundene Gerätezugänge;
- Kreisziele, bestehende Ziele, fehlende Eltern, Symlinks und Spezialdateien;
- Kontingent-, Tiefen- und Mitgliedergrenzen;
- sichtbaren COPY-Rollback bei simuliertem Dateisystemfehler;
- RFC-6578-Sync-Journal für alte und neue Pfade.

## Bekannte Grenzen, Deaktivierung und Rückkehr

- Partielle `207 Multi-Status`-Ergebnisse sind zugunsten eines konsistenten,
  vollständig zurückgerollten Ziels nicht implementiert.
- Ziele werden nie automatisch überschrieben oder zusammengeführt.
- Die atomare MOVE-Garantie gilt innerhalb des konfigurierten Dokumentenroots;
  Quelle und Ziel liegen konstruktionsbedingt auf demselben Dateisystem.
- Externe Prozesse, die Dateien direkt im Dokumentenroot verändern, verwenden
  die WebDAV-Mutationssperre nicht. Der nächste Scan repariert den Index, kann
  aber keine Desktop-Konfliktentscheidung nachholen.

Eine separate Laufzeitoption ist nicht erforderlich. Zum Deaktivieren kann ein
Gerätezugang auf `read` gesetzt oder widerrufen werden. Ein Rückgang auf eine
ältere Version benötigt keine Konvertierung; die erzeugten Dateien und
Metadaten bleiben normale SimpleOffice-Ressourcen.
