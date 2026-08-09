# WebDAV-Eigenschaften und Metadaten nach RFC 4918

## Zweck und Nutzen

SimpleOffice speichert benutzerdefinierte WebDAV-Eigenschaften („Dead
Properties“) für Dateien und Ordner. Desktop-Programme können damit
Schlagwörter, Bewertungen, Bearbeitungszustände oder anwendungsspezifische
Metadaten über `PROPPATCH` ablegen und später über `PROPFIND` wieder lesen.
Dateiinhalt und SimpleOffice-Berechtigungen werden dadurch nicht verändert.

Die Funktion ist vor allem für LibreOffice-Erweiterungen, Dateimanager und
Synchronisationsprogramme nützlich, die eigene XML-Namensräume verwenden.
Eigenschaften bleiben beim Umbenennen und Verschieben einer Datei erhalten;
beim Kopieren werden sie gemäß WebDAV-Empfehlung auf die neue Datei kopiert.

## Auswertung der Primärstandards

Maßgeblich sind
[RFC 4918](https://www.rfc-editor.org/rfc/rfc4918.html) und für den
inkrementellen Abgleich
[RFC 6578](https://www.rfc-editor.org/rfc/rfc6578.html).

| Normative Anforderung | Quelle | Umsetzung in SimpleOffice |
|---|---|---|
| Eigenschaften sind qualifizierte XML-Namen; Dead Properties werden vom Server gespeichert, ihre Semantik bleibt beim Client. Alle Instanzen einer Live Property müssen deren Definition erfüllen. | [RFC 4918 §4.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-4.1), [§4.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-4.4) | Fremde, nicht leere XML-Namensräume werden als Dead Properties gespeichert. Berechnete `DAV:`-Eigenschaften bleiben serververwaltet und geschützt. |
| Eigenschaftswerte sind wohlgeformte XML-Fragmente; Dead Properties sollen semantisch unverändert wiedergegeben werden. Ein `xml:lang` am umschließenden `prop` **MUST** mit dem Wert gespeichert werden. | [§4.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-4.3), [§14.26](https://www.rfc-editor.org/rfc/rfc4918.html#section-14.26) | Elementname, Attribute, geerbtes `xml:lang`, Text, Kindelemente und Namensräume werden als XML gespeichert und wieder ausgegeben. Präfixe dürfen bei der Serialisierung wechseln, die XML-Namensräume und Inhalte bleiben gleich. |
| DAV-konforme Ressourcen **MUST** `PROPPATCH`, `propertyupdate`, `set` und `remove` unterstützen und **SHOULD** beliebige Dead Properties erlauben. | [§9.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.2) | Dateien und sichtbare Sammlungen akzeptieren geordnete `set`-/`remove`-Anweisungen. Schreibende App-Passwörter sind erforderlich. |
| PROPPATCH-Anweisungen **MUST** in Dokumentreihenfolge verarbeitet werden und **MUST** vollständig atomar sein: alle oder keine. | [§9.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.2) | Der vollständige Antrag wird zuerst begrenzt, geparst und geprüft. Ein geschütztes Feld führt für dieses Feld zu `403` und für abhängige Änderungen zu `424`; es wird nichts gespeichert. Erst der vollständig gültige Endzustand wird atomar ersetzt. |
| Ein versuchter PROPPATCH **MUST** mit `207 Multi-Status` antworten und darf nicht gecacht werden. Geschützte Properties **SHOULD** `cannot-modify-protected-property` melden. | [§9.2.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.2.1) | Erfolgs- und Fehlergruppen werden als `propstat` ausgegeben. Antworten tragen `Cache-Control: no-store`; Änderungen an ETag, Größe, Typ, Zeit, Ressourcenart, Locks, Reports und Sync-Token erhalten die genannte Vorbedingung. |
| `DAV:displayname` und `DAV:getcontentlanguage` **SHOULD NOT** geschützt sein; `displayname` ändert keine URL. | [§15.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-15.2), [§15.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-15.3) | Beide Live Properties sind schreibbar und syntaktisch geprüft. `displayname` ist reine Präsentationsmetadaten, benennt die Datei nicht um. `getcontentlanguage` wird zusätzlich als `Content-Language` bei `GET`/`HEAD` ausgegeben. |
| `PROPFIND prop` **MUST** fehlende Eigenschaften mit `404` im `propstat` kenntlich machen; `propname` nennt vorhandene Namen; `allprop` umfasst Dead Properties und die in RFC 4918 definierten Live Properties. | [§9.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.1), [§9.1.4–9.1.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.1.4) | Die drei Abfrageformen werden ausgewertet. Erweiterungen wie `sync-token` und `supported-report-set` erscheinen bei `allprop` nur über `include`, während benutzerdefinierte Eigenschaften enthalten sind. |
| Write-Locks gelten auch für Eigenschaften. | [§7.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.1) | Ein gesperrtes Dokument akzeptiert `PROPPATCH` nur mit dem zugehörigen benutzergebundenen Lock-Token. SimpleOffice-Aufbewahrungs- und Bearbeitungssperren gelten zusätzlich. |
| COPY **SHOULD** Dead Properties duplizieren; MOVE soll die Eigenschaften der verschobenen Ressource erhalten. | [§9.8.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.8.2), [§9.9.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9.1) | Eine Dateikopie erhält einen eigenen Eigenschaftssatz mit identischen Ausgangswerten. Verschieben und Umbenennen behalten die stabile Dokument-ID und damit die Eigenschaften. |
| Externe XML-Entitäten sind nicht vertrauenswürdig; bei Ablehnung **SHOULD** `no-external-entities` gemeldet werden. Auch verschachtelte interne Entitäten können Ressourcen erschöpfen. | [§20.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-20.6) | `DOCTYPE` und `ENTITY` werden vor dem XML-Parser abgewiesen und als `no-external-entities` gemeldet. Körpergröße, Elementzahl, Anweisungszahl, Einzelwert und gespeicherte Feldzahl sind fest begrenzt. |
| Ein Sync-Bericht meldet geänderte Mitglied-URLs; angeforderte Eigenschaften gehören in deren `propstat`. | [RFC 6578 §3.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.2), [§3.8](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.8) | Eine erfolgreiche Eigenschaftsänderung erneuert den benutzergebundenen Sync-Token. Der folgende `sync-collection`-Bericht enthält die Datei genau einmal und liefert angeforderte Dead Properties mit. |

## Bedienung und Kompatibilität

Die Funktion benötigt keine zusätzliche Serverkonfiguration. Ein vorhandener
schreibender WebDAV-Gerätezugang genügt. LibreOffice und Dateimanager können
weiterhin normal öffnen und speichern; Programme mit Eigenschaftsunterstützung
senden zusätzlich beispielsweise:

```xml
<d:propertyupdate xmlns:d="DAV:" xmlns:m="urn:example:metadata">
  <d:set>
    <d:prop>
      <m:tags><m:tag>rechnung</m:tag><m:tag>kunde-a</m:tag></m:tags>
    </d:prop>
  </d:set>
</d:propertyupdate>
```

Die Werte sind anschließend mit einer benannten `PROPFIND`-Abfrage, mit
`propname` oder über `allprop` verfügbar. Ein `remove` entfernt nur die
genannte Eigenschaft. Wiederholtes Setzen desselben Wertes und Entfernen eines
nicht vorhandenen Wertes sind idempotent.

SimpleOffice-Tags im Webformular und beliebige WebDAV-Dead-Properties bleiben
bewusst getrennt. Ein fremder Client darf dadurch nicht unbemerkt fachliche
Tags, Freigaben, Fristen oder Aufbewahrung ändern. Eine spätere Zuordnung kann
nur als ausdrücklich konfigurierte und dokumentierte Funktion ergänzt werden.

## Speicherung, Audit und Datenschutz

- Die Werte liegen atomar in
  `.simpleoffice-meta/webdav-properties.json`; der Pfad ist weder in WebDAV
  noch als Download sichtbar.
- Eigenschaftssätze sind an Benutzer und stabile Dokument- beziehungsweise
  Ordner-ID gebunden. Ein anderer Benutzerbaum kann sie nicht lesen.
- Jede tatsächliche Änderung erzeugt einen Git-basierten Auditdatensatz mit
  Benutzer, Zeitpunkt, Ressource und qualifizierten Eigenschaftsnamen. Die
  Werte werden nicht in das Auditereignis kopiert, damit vertrauliche
  Metadaten dort nicht unnötig vervielfacht werden.
- App-Passwörter, Inhalte und Eigenschaften werden an keinen externen Dienst
  gesendet. Basic Authentication setzt HTTPS voraus.
- Dead Properties verleihen keine Leserechte, Schreibrechte oder Freigaben.
  Nur ein schreibender, nicht abgelaufener Gerätezugang darf sie ändern.

## Schutzgrenzen und Fehlerverhalten

- höchstens 64 KiB XML pro `PROPFIND`/`PROPPATCH`;
- höchstens 256 XML-Elemente und 64 Änderungsanweisungen pro Anfrage;
- höchstens 16 KiB XML pro Einzelwert und 128 gespeicherte Eigenschaften je
  Ressource;
- `400` für fehlerhafte Form oder verbotene Entitäten, `403`/`424` innerhalb
  von `207` für atomar abgewiesene geschützte Änderungen, `413` für
  Größenüberschreitungen, `423` für fehlende Lock-Token oder interne
  Bearbeitungssperren und `507` innerhalb von `207` für die Feldobergrenze;
- ein Fehler vor dem atomaren Austausch lässt den bisherigen Eigenschaftssatz
  vollständig unverändert.

## Migration und Rückwärtskompatibilität

Es gibt keine Datenmigration. Die Steuerdatei wird erst bei der ersten
erfolgreichen Eigenschaftsänderung angelegt. Vorhandene Dateien, ETags,
Versionen und WebDAV-Adressen ändern sich nicht. Clients ohne `PROPPATCH`
arbeiten unverändert weiter. Die ältere direkte LibreOffice-Dokumentadresse
unter `/webdav/documents/...` unterstützt dieselben Datei-Eigenschaften wie
der hierarchische Dateibaum.

## Tests

Automatisierte Positiv-, Negativ-, Rechte-, Konflikt- und
Interoperabilitätstests prüfen:

- verschachtelte XML-Werte, `prop`, `propname`, `allprop`, `set` und `remove`;
- atomaren Rollback mit `403` und `424` bei einem geschützten `DAV:`-Feld;
- Nur-Lese-App-Passwörter, fremde Benutzerpfade, Locks und
  Aufbewahrungssperren;
- Ablehnung von Entitäten, fehlerhaftem und übergroßem XML;
- Erhaltung bei MOVE, Kopie bei COPY und getrennte stabile IDs;
- Audit-Snapshot und inkrementellen RFC-6578-Bericht samt Eigenschaftswert.

## Bekannte Grenzen und Deaktivierung

Die Implementierung bietet kein WebDAV ACL, keine serverseitige Suche über
beliebige XML-Werte und keine automatische fachliche Interpretation fremder
Namensräume. Property-Patches auf nicht vorhandene Lock-null-Ressourcen und
rekursive Ordner-COPY/MOVE-Operationen sind nicht implementiert.

Das Widerrufen des betreffenden Gerätezugangs beendet Lesen und Schreiben
sofort. Werden alle WebDAV-Zugänge widerrufen, ist die Funktion vollständig
deaktiviert. Ein Rückbau des Codes erfordert keine Datenänderung; die private
Eigenschaftsdatei kann zur späteren Wiederaktivierung liegen bleiben. Eine
manuelle Löschung entfernt nur WebDAV-Zusatzmetadaten, nicht die Dokumente,
sollte aber erst nach Sicherung und ausdrücklicher Entscheidung erfolgen.
