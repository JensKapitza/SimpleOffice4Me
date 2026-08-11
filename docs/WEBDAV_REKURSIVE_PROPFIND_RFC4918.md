# Begrenzte rekursive WebDAV-Bestandsaufnahme nach RFC 4918

## Zweck und Nutzen

Der hierarchische Dateibaum unter `/webdav/files/<Benutzer>/` unterstützt
`PROPFIND` jetzt mit `Depth: infinity`. Ein berechtigter Client kann damit den
vollständigen erreichbaren Ordnerbestand in einer konsistenten Anfrage
erfassen. Das hilft insbesondere Inventarisierungs-, Sicherungs- und
Synchronisationswerkzeugen; normale Desktop-Clients dürfen weiterhin mit
`Depth: 0` oder `1` arbeiten. FreeFileSync greift wie bisher über einen vom
Betriebssystem eingebundenen WebDAV-Ordner zu.

Die Funktion ist absichtlich begrenzt: höchstens 2.000 sichtbare Mitglieder,
64 Ordnerebenen und 8 MiB erzeugtes XML. Damit wird die von RFC 4918 empfohlene
Interoperabilität nicht mit einer unbeschränkten Serverlast erkauft.

## Primärstandard und normative Anforderungen

Maßgeblich ist
[RFC 4918 – Web Distributed Authoring and Versioning](https://www.rfc-editor.org/rfc/rfc4918.html).

| Anforderung | Abschnitt | Umsetzung und Entscheidung |
|---|---|---|
| Ein Client **MAY** `Depth: 0`, `1` oder `infinity` senden. Ein DAV-konformer Server **MUST** `0` und `1` unterstützen und **SHOULD** `infinity` unterstützen. Fehlt der Header, **SHOULD** der Server `infinity` annehmen. | [§9.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.1), [§10.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.2) | Der hierarchische Endpunkt akzeptiert alle drei Werte und verwendet bei fehlendem Header `infinity`. Für eine einzelne Datei wirkt jede gültige Tiefe wie `0`. |
| Eine erfolgreiche Antwort **MUST** für jedes Mitglied bis zur angeforderten Tiefe ein `response`-Element enthalten. Die Ergebnisliste ist flach; ihre Reihenfolge ist nicht semantisch. | [§9.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.1) | Die Antwort enthält Wurzel, Ordner und verwaltete Dateien als flache `207 Multi-Status`-Liste. Die Traversierung wird zur Reproduzierbarkeit nach Namen sortiert, Clients dürfen daraus aber keine Ordnungsbedeutung ableiten. |
| `allprop`, `propname`, ausgewählte `prop`-Werte sowie nicht vorhandene Eigenschaften müssen korrekt unterschieden werden. | [§9.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.1) | Für jedes Mitglied werden dieselben Live- und Dead-Property-Regeln wie bei `Depth: 0/1` angewendet; fehlende Eigenschaften stehen in einem getrennten `404`-`propstat`. |
| Autorisierung muss vor der Offenlegung oder einer nachgelagerten Fehlerprüfung erfolgen. | [§8.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-8.1) | Benutzertrennung und optionaler Ordnerpräfix werden vor Bestandsaufnahme und XML-Auswertung erzwungen. Nicht erreichbare Bäume antworten mit `404`; Lesezugänge dürfen auf ihrem freigegebenen Teilbaum auflisten, aber nicht schreiben. |
| Server dürfen unendliche Tiefenabfragen wegen Leistungs- oder Sicherheitsrisiken ablehnen; der Standard beschreibt dafür `propfind-finite-depth`. | [§9.1.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.1.1) | SimpleOffice unterstützt sie begrenzt, statt sie grundsätzlich abzulehnen. Beim Überschreiten einer Grenze gibt es eine vollständige `507`-Fehlerantwort und niemals eine unvollständige Erfolgsliste. |
| Rekursive WebDAV-Operationen können CPU-, Speicher- und I/O-Ressourcen angreifen; Server **SHOULD** die Auswirkungen begrenzen. | [§20.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-20.2) | Mitgliederzahl, Tiefe und fertige XML-Größe sind fest begrenzt. Quota-, Dead-Property- und Lock-Daten werden innerhalb einer Anfrage wiederverwendet. |

## Implementiertes Verhalten

- `Depth: 0` liefert nur die angefragte Ressource.
- `Depth: 1` liefert die Ressource und ihre direkten Kinder.
- `Depth: infinity` liefert alle erreichbaren Nachkommen bis zu den festen
  Schutzgrenzen.
- Ein fehlender `Depth`-Header bedeutet am hierarchischen Endpunkt gemäß der
  Empfehlung des RFC `infinity`.
- Interne SimpleOffice-Verzeichnisse, Richtliniendateien, Symlinks und
  Spezialdateien werden weder traversiert noch ausgegeben.
- Die Bestandsaufnahme verwendet dieselbe benutzerbezogene Mutationssperre wie
  `PUT`, `PROPPATCH`, `COPY`, `MOVE` und `DELETE`. Der sichtbare Baum und seine
  Eigenschaften können dadurch nicht mitten in der XML-Erzeugung verändert
  werden.
- Die Antwort wird vollständig im Speicher geprüft und erst danach als `207`
  gesendet. `Cache-Control: private, no-store` verhindert eine gemeinsame
  Zwischenspeicherung; `Vary` berücksichtigt mindestens Autorisierung und
  Tiefe.
- Erfolgreiche Leseabfragen erzeugen bewusst keinen Auditdatensatz, damit ein
  lesender Client die Audit-Historie nicht unbegrenzt vergrößern kann.
  Abgewiesene Grenzwertüberschreitungen werden dagegen mit Benutzer, Pfad,
  Grund, beobachtetem Wert, Grenze und Zeitpunkt revisionssicher protokolliert.
  Inhalte, Eigenschaftswerte und App-Passwörter stehen nicht im Audit-Snapshot.

## Bedienung und Desktop-Integration

LibreOffice, Nautilus/GNOME Files, Windows-Datei-Explorer und macOS Finder
können unverändert mit ihrer normalen `Depth: 0/1`-Folge arbeiten. FreeFileSync
wird weiterhin auf den vom Betriebssystem eingebundenen WebDAV-Ordner
angesetzt. Ein Werkzeug, das direkt eine vollständige Bestandsaufnahme
benötigt, kann beispielsweise senden:

```sh
curl --user 'BENUTZER:APP-PASSWORT' \
  --request PROPFIND \
  --header 'Depth: infinity' \
  --header 'Content-Type: application/xml' \
  --data '<d:propfind xmlns:d="DAV:"><d:prop><d:displayname/><d:getetag/></d:prop></d:propfind>' \
  'https://office.example/webdav/files/BENUTZER/'
```

Das App-Passwort gehört nicht in die URL, ein Skript oder die Shell-Historie.
Produktiver Fernzugriff setzt HTTPS, ein vertrauenswürdiges Zertifikat und
einen getrennten, möglichst nur lesenden und auf einen Ordner begrenzten
Gerätezugang voraus. Die Einrichtung der Zielprogramme steht in
[Dateiverwaltung über WebDAV](WEBDAV_DATEIVERWALTUNG.md) und
[Getrennte WebDAV-Gerätezugänge](WEBDAV_ZUGAENGE.md).

Nach der ersten Bestandsaufnahme sollten kompatible Synchronisationsclients
den effizienteren
[`sync-collection`-REPORT nach RFC 6578](WEBDAV_SYNC_RFC6578.md) verwenden.

## Fehler- und Ausfallverhalten

- `400`: `Depth` ist nicht `0`, `1` oder `infinity`, oder der XML-Text ist
  ungültig.
- `401`, `403`, `404`: Anmeldung, Schreibumfang oder erreichbarer Benutzerbaum
  genügen nicht. Rechte werden vor der Tiefenverarbeitung geprüft.
- `413`: die PROPFIND-Anfrage selbst überschreitet die XML-Grenze.
- `507` mit `X-SimpleOffice-Propfind-Limit`: die Bestandsaufnahme überschreitet
  `member-count`, `nesting-depth` oder `response-bytes`; `tree-changed` schützt
  außerdem vor einem nicht sicher lesbaren Baum. Die XML-Antwort enthält
  `propfind-resource-limit`, aber keine teilweise `multistatus`-Liste.

Eine abgewiesene Anfrage verändert keine Datei, Eigenschaft, Freigabe,
Aufbewahrungsregel oder Sync-Marke. Der Client kann einen kleineren Teilbaum
mit `Depth: 1` abfragen oder nach einem initialen Stand den RFC-6578-Abgleich
verwenden.

## Migration, Rückwärtskompatibilität und Grenzen

Es gibt keine Daten- oder Datenbankmigration. Explizite `Depth: 0/1`-Anfragen
behalten ihr Verhalten. Nur eine bisher headerlose PROPFIND-Anfrage am
hierarchischen Endpunkt erhält nun normgerecht den vollständigen, begrenzten
Teilbaum; Clients, die ausschließlich die Zielressource benötigen, müssen
`Depth: 0` senden.

Die ältere, dokument-ID-stabile URL unter `/webdav/documents/` bleibt bewusst
auf `Depth: 0/1` begrenzt und lehnt `infinity` weiterhin ab. Sie ist keine
Sammlung und dient LibreOffice-Direktlinks, nicht der Bestandsaufnahme.

Die Grenzen sind derzeit feste Sicherheitswerte und nicht per Gerätezugang
erweiterbar. Es gibt keine serverseitige Paginierung für PROPFIND; RFC 4918
definiert dafür kein interoperables Fortsetzungsformat. Suchabfragen über
Eigenschaftswerte und WebDAV ACL sind weiterhin nicht implementiert.

## Tests

Automatisiert geprüft werden:

- explizite und implizite rekursive Abfragen mit verschachtelten Ordnern,
  Unicode-Namen und flachen, eindeutigen `href`-Antworten;
- Ausschluss interner Pfade und Symlink-Ziele;
- nur lesender, ordnerbegrenzter Gerätezugang ohne Offenlegung benachbarter
  Bäume und ohne Schreibmöglichkeit;
- Mitglieder-, Tiefen- und XML-Größenlimit ohne Teilerfolg sowie zugehörige
  Audit-Snapshots;
- gemeinsame Mutationssperre und unverändertes Verhalten der bestehenden
  PROPFIND-, Property-, Lock-, Sync- und Desktop-Client-Tests.

## Deaktivierung und Rückkehr

WebDAV lässt sich unverändert durch Widerruf aller Gerätezugänge deaktivieren.
Für eine rein clientseitige Rückkehr genügt `Depth: 0/1`; dabei werden keine
Dateien oder Metadaten umgeschrieben. Ein Code-Rollback dieser Erweiterung
entfernt nur `Depth: infinity` vom hierarchischen Endpunkt. Bestehende Dateien,
Revisionen, Auditdaten und Aufbewahrungsregeln bleiben erhalten.
