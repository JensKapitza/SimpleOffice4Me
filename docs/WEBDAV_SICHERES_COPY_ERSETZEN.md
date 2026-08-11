# Vorhandene WebDAV-Dateien sicher per COPY ersetzen

## Zweck und Nutzen

FreeFileSync, Nautilus/GNOME Files, Windows Explorer, Finder und andere
WebDAV-Clients verwenden `COPY`, um eine serverseitige Kopie anzulegen. Wenn
das Ziel bereits existiert, verlangt RFC 4918 standardmäßig einen Austausch.
SimpleOffice unterstützt diesen Ablauf nun für reguläre Dateien, verhindert
aber, dass eine veraltete Clientansicht eine neuere Zielversion unbemerkt
überschreibt.

Der kopierte Inhalt ersetzt ausschließlich den Inhalt des Zieldokuments. Das
Ziel behält seine stabile Dokument-ID, Freigaben, Tags, Aufbewahrung,
fachlichen Sperren und WebDAV-Eigenschaften. Der vorherige Zielinhalt wird in
der unveränderlichen Inhaltsversionierung archiviert. Die Quelle samt Inhalt,
ID, Metadaten und Lock bleibt unverändert. Damit lässt sich mit kopierten
Dateien sofort weiterarbeiten, ohne Berechtigungen oder Referenzen durch die
Metadaten einer Vorlage zu ersetzen.

## Ausgewertete Primärstandards

| MUST, SHOULD oder MAY | Primärnorm | Umsetzung in SimpleOffice |
| --- | --- | --- |
| Eine WebDAV-Ressource **MUST** `COPY` unterstützen; `Destination` **MUST** vorhanden sein. Die Methode ist idempotent, nicht safe und Antworten dürfen nicht gecacht werden. | [RFC 4918 § 9.8](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8) | Das Ziel muss im selben authentifizierten Benutzer- und Gerätebereich liegen. Wiederholtes Kopieren mit identischem Quellstand erzeugt denselben sichtbaren Zielinhalt; Antworten enthalten keine cachebare Repräsentation. |
| Bei einer Nicht-Collection soll der Zielzustand der Quelle so weit wie möglich entsprechen. Dead Properties **SHOULD** mitkopiert werden, sofern sie am Ziel gesetzt werden können. | [RFC 4918 § 9.8.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.1), [§ 9.8.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.2) | Bei einem neuen Ziel werden Quell-Tags und Dead Properties weiter kopiert. Bei einem vorhandenen Ziel bleibt bewusst dessen Sicherheits- und Metadatenprofil erhalten; nur der Dateiinhalt wird ersetzt. |
| Existiert das Ziel und ist `Overwrite: F`, **MUST** die Anfrage fehlschlagen. Bei Overwrite **MAY** ein In-place-Austausch zum Erhalt von Live Properties erfolgen. | [RFC 4918 § 9.8.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.4), [§ 10.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.6) | `F` ergibt vor jeder Mutation `412`. `T` beziehungsweise ein fehlender Header erlaubt nur bei regulären verwalteten Dateien einen In-place-Austausch. Collections, Symlinks, Spezialdateien und fremde Dateien bleiben ausgeschlossen. |
| Ein neu erzeugtes Ziel soll `201 Created`, ein ersetztes Ziel `204 No Content` liefern. | [RFC 4918 § 9.8.5](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.5) | Neue Kopien liefern `201`, ersetzte Ziele `204`. `Location`, `Content-Location`, starker Ziel-`ETag` und `Repr-Digest` beschreiben den gespeicherten Zielstand. |
| Bedingungen sind vor der Zustandsänderung in definierter Reihenfolge auszuwerten; falsche Lost-Update-Bedingungen ergeben `412`. | [RFC 9110 § 13.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.2.2) | `If-Match` schützt die Quell-URL. Das vorhandene Ziel muss zusätzlich in einem getaggten DAV-`If`-Block den aktuellen starken ETag oder seinen Lock-Token nennen. Beide Inhalts-Hashes werden unmittelbar vor dem Store-Aufruf erneut geprüft. |
| Ein Token ist für jede durch die Methode veränderte gesperrte Ressource vorzulegen. Eine gesperrte Quelle braucht bei `COPY` kein Token, weil `COPY` sie nicht verändert. | [RFC 4918 § 7.5](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.5), insbesondere [§ 7.5.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.5.1) | Ein gesperrtes Ziel verlangt sein getaggtes Token. Ein Quell-Lock wird weder verlangt, übertragen noch entfernt. Ziel-Locks bleiben nach erfolgreichem Austausch bestehen. |
| Locks am Ziel hängen vom Zielzustand ab, nicht von Locks der Quelle. | [RFC 4918 § 7.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.6), [§ 15.8](https://www.rfc-editor.org/rfc/rfc4918.html#section-15.8) | Die Lock-Discovery des Ziels bleibt unverändert. Quell-Token werden nie zum Ziel kopiert. |
| WebDAV-ETags sollen für verteiltes Schreiben geeignet sein und nach erfolgreicher Änderung nicht still wiederverwendet werden. | [RFC 4918 § 8.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-8.6) | Der Ziel-ETag ist die starke SHA-256 des gespeicherten Inhalts. Eine echte Inhaltsänderung erzeugt einen neuen ETag und einen RFC-6578-Sync-Journaleintrag ausschließlich für den Zielpfad. |

## Bewusste Sicherheitsverschärfung und Abweichung

Ohne `Overwrite` gilt nach RFC 4918 der Wert `T`. SimpleOffice akzeptiert
diese Semantik, führt den Austausch aber nur aus, wenn der Client zusätzlich
den gerade gesehenen Zielzustand nachweist:

```http
COPY /webdav/files/jens/vorlage.odt HTTP/1.1
Destination: https://office.example/webdav/files/jens/angebot.odt
Overwrite: T
If: <https://office.example/webdav/files/jens/angebot.odt> (["<sha256>"])
```

Alternativ kann ein Client das gültige Ziel-Lock-Token getaggt mitsenden. Fehlt
beides, antwortet SimpleOffice mit `428 Precondition Required`, dem aktuellen
Ziel-ETag und `Cache-Control: private, no-cache`. Ein falscher oder veralteter
Wert ergibt `412`. Diese zusätzliche Anforderung schützt vor Lost Updates, die
der alleinige `Overwrite`-Header nicht verhindern kann.

RFC 4918 erlaubt beim Überschreiben sowohl Delete-plus-Copy als auch einen
In-place-Austausch. SimpleOffice wählt bewusst In-place: Ziel-ID, Zielrechte,
Aufbewahrung, Tags und Ziel-Dead-Properties bleiben erhalten. Quellrechte und
Quellmetadaten ersetzen sie nicht. Dies ist ein absichtliches Sicherheitsprofil
für eine Dokumentenverwaltung und verhindert, dass `COPY` vorhandene Freigaben
lockert oder Aufbewahrungsregeln umgeht. Wer die Metadaten der Quelle benötigt,
kopiert stattdessen auf einen neuen Namen und löscht das bisherige Ziel nach
separater Rechteprüfung.

## Transaktion, Versionierung und Konfliktschutz

1. Authentifizierung, schreibendes Gerätekennwort, Bereichsgrenze, sichere
   Pfade und vorhandener Elternordner werden geprüft.
2. `Overwrite`, Quellbedingung und der getaggte Ziel-ETag beziehungsweise das
   Ziel-Lock-Token werden ausgewertet.
3. Quelle und Ziel müssen reguläre, verfügbare und bearbeitbare verwaltete
   Dateien sein. Fachliche Sperren und laufende Bereinigungen blockieren den
   Vorgang mit `423`.
4. Quelle und Ziel werden erneut gehasht. Eine Abweichung von den zuvor
   ausgewerteten starken ETags ergibt `412`, bevor das Ziel verändert wird.
5. Der alte Zielinhalt wird hashverifiziert unter
   `.simpleoffice-meta/content-versions/<ziel-id>/` archiviert.
6. Der Quellinhalt wird über den bestehenden fsync-, Temporärdatei- und
   atomaren Rename-Pfad auf das Ziel geschrieben.
7. `document_content_replaced` und
   `webdav_document_replaced_via_copy` protokollieren IDs, Pfade, vorherigen
   Zielhash, neuen Hash, Akteur und Zeitpunkt. Anschließend wird nur der
   Zielpfad in das RFC-6578-Sync-Journal aufgenommen.

Der WebDAV-Mutationslock serialisiert DAV-Schreibvorgänge. Direkte Änderungen
im Server-Dateisystem außerhalb von WebDAV werden durch die zusätzliche
Hashprüfung erkannt; externe Prozesse sollten das verwaltete Root trotzdem
nicht schreibend umgehen.

## Bedienung und Desktop-Integration

- **FreeFileSync:** WebDAV-Quelle und -Ziel normal vergleichen. Bei `412` oder
  `428` erneut vergleichen und den Konflikt anzeigen lassen. Ein vorhandenes
  Ziel wird nur ersetzt, wenn die WebDAV-Schicht dessen starken ETag oder Lock
  getaggt weiterreicht. Die sichtbare Quota wächst beim Austausch nicht.
- **Nautilus/GNOME Files:** Server über `davs://host/webdav/files/benutzer/`
  verbinden. Kopieren und „Datei ersetzen“ funktioniert mit DAV-Versionen, die
  Zielbedingungen weiterreichen; andernfalls bleibt das Ziel sicher bestehen.
- **Windows Explorer:** Nur HTTPS und ein getrenntes SimpleOffice-
  Gerätekennwort verwenden. Nach einem Konflikt die Ansicht aktualisieren,
  nicht wiederholt blind überschreiben.
- **macOS Finder:** Mit `https://host/webdav/files/benutzer/` verbinden. Finder
  darf Quell-Locks behalten; das Ziel benötigt beim Ersetzen weiterhin seinen
  eigenen Validator.
- **LibreOffice:** `COPY` ist vor allem für „Speichern unter“ und Vorlagen
  relevant. Das normale sichere Ersetzen einer temporären Speicherversion per
  `MOVE` ist separat in `WEBDAV_SICHERES_MOVE_ERSETZEN.md` dokumentiert.

Es ist keine Client-Erweiterung und keine Server-Geheimnisdatei erforderlich.
Die Einrichtung verwendet die bestehenden ablaufenden, widerrufbaren und auf
Ordner einschränkbaren WebDAV-Gerätekennwörter. Produktiv ist HTTPS Pflicht.

## Rechte, Sicherheit und Datenschutz

- Quelle und Ziel liegen im selben authentifizierten Benutzerbaum und müssen
  vom Pfadbereich desselben schreibenden Gerätekennworts erfasst sein.
- Zielrechte, fachliche Attribute, Tags, Aufbewahrung und WebDAV-Eigenschaften
  bleiben erhalten. `COPY` erzeugt keine automatische Freigabe.
- Die Quelle wird weder gelöscht noch verändert; ein Quell-Lock bleibt aktiv.
- Ziel-Collections, Symlinks, Spezialdateien, nicht verwaltete Dateien,
  Traversal und fremde Hosts oder Benutzer werden abgewiesen.
- Der alte Zielinhalt bleibt in der privaten, vorhandenen Versionsablage. Es
  gibt keine externe Übertragung und keine Änderung der Aufbewahrungsfrist.
- Eine zweite ClamAV-Prüfung ist nicht nötig, weil kein neuer Payload in das
  System gelangt: Die Quelle ist bereits eine veröffentlichte verwaltete Datei
  und wurde beim ursprünglichen optionalen fail-closed `PUT` geprüft.

## Fehler- und Ausfallverhalten

| Status | Bedeutung | Empfohlene Clientreaktion |
| --- | --- | --- |
| `204` | vorhandenes Ziel erfolgreich ersetzt | neuen ETag speichern |
| `201` | neues Ziel erfolgreich erstellt | neue Ziel-URL übernehmen |
| `400` | ungültiger Header, Ziel oder Pfad | Anfrage korrigieren |
| `409` | Elternordner fehlt oder Ziel ist nicht verwaltet | Baum neu einlesen |
| `412` | `Overwrite: F`, veralteter Validator oder gleiche Quelle/Ziel | nicht wiederholen; Konflikt lösen |
| `413` | Quelle überschreitet die konfigurierte Größenbegrenzung | kleinere Datei verwenden |
| `423` | Ziel-Lock, Aufbewahrung oder fachliche Sperre | Token senden oder Sperre klären |
| `428` | kein ausdrücklicher Zielvalidator | Ziel lesen und getaggten ETag/Token senden |
| `507` | physischer Speicher- oder interner I/O-Fehler | später erneut versuchen; Ziel prüfen |

Fehler vor dem atomaren Rename verändern das sichtbare Ziel nicht. Der alte
Inhalt wird vor einem Write verifiziert archiviert. Ein identischer
Quell-/Zielinhalt ist idempotent: Es entsteht keine künstliche Inhaltsversion,
der sicher geprüfte Vorgang wird dennoch auditiert.

## Migration, Rückwärtskompatibilität und Deaktivierung

Es gibt keine Datenmigration und keine neue Pflichtkonfiguration. Bestehende
Dokumente, Versionen, Gerätekennwörter, Locks, Tags, Freigaben und
Aufbewahrungsregeln bleiben kompatibel. Neue `COPY`-Ziele funktionieren wie
bisher mit `201`; nur das bisher konservativ abgewiesene Ersetzen einer
regulären Datei erhält den geschützten `204`-Pfad.

Zum Deaktivieren genügt ein read-only Gerätezugang, eine Ordnerbeschränkung
oder der Widerruf des Gerätekennworts. Ein Rückgang auf eine ältere Version
braucht keine Konvertierung: Inhaltsarchive, Audit-Snapshots und
Dokumentmetadaten verwenden ausschließlich bestehende Formate. Eine ältere
Version wird vorhandene COPY-Austausche wieder mit `412` abweisen.

## Tests und bekannte Grenzen

Automatisierte Positiv-, Negativ- und Interoperabilitätstests prüfen:

- `204`, `Location`, `Content-Location`, neuen starken ETag und `Repr-Digest`;
- Erhalt von Ziel-ID, Freigaben, Tags und Ziel-Dead-Properties;
- unveränderte Quelle und getrennten Erhalt von Quell- und Ziel-Lock;
- Inhaltsversion und vollständige Audit-Historie;
- `Overwrite: F`, fehlenden, falschen und veralteten Zielvalidator;
- spät geänderte Quelle und spät geändertes Ziel ohne Teilmutation;
- fachlich gesperrtes Ziel sowie Austausch bei vollständig belegter sichtbarer
  Quota, weil keine zusätzliche sichtbare Datei entsteht;
- unverändertes Verhalten für neue Ziele und Collection-COPY.

Bewusste Grenzen:

- Vorhandene Collections werden nicht überschrieben oder zusammengeführt.
- Ziel-Metadaten bleiben absichtlich erhalten; dies ist kein vollständiger
  Klon aller Quell-Metadaten auf einen vorhandenen Namen.
- Inhaltsversionen benötigen physischen Speicher außerhalb der sichtbaren
  Benutzerquota. Physische I/O-Fehler werden weiterhin fail-closed behandelt.
- WebDAV-Clients ohne getaggte Zielbedingungen erhalten `428`; sie können neue
  Namen kopieren, aber keine vorhandene Datei blind ersetzen.
- Direkte Schreibprozesse im Dokumentenroot nehmen nicht am DAV-Locking teil.
