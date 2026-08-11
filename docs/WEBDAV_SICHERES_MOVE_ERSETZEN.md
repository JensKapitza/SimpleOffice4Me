# Vorhandene WebDAV-Dateien sicher per MOVE ersetzen

## Zweck und Nutzen

LibreOffice und Desktop-Dateimanager speichern häufig nicht mit einem direkten
`PUT` auf die geöffnete Datei. Sie laden zuerst eine temporäre Datei hoch und
verschieben diese anschließend mit `MOVE` und `Overwrite: T` auf den bisherigen
Namen. SimpleOffice unterstützt diesen Ablauf für reguläre Dateien, ohne eine
neuere Zielversion still zu überschreiben.

Das Ziel behält seine stabile SimpleOffice-Dokument-ID, Freigaben, Tags,
Aufbewahrungszustand und WebDAV-Eigenschaften. Der alte Zielinhalt wird als
unveränderliche Inhaltsversion archiviert. Die verbrauchte temporäre Quelldatei
verschwindet aus dem sichtbaren WebDAV-Baum, bleibt aber mit eigener ID, Hash,
Herkunft und Inhalt in der privaten Wiederherstellung. So funktionieren
Office-Speichermuster, ohne Rechte durch die Metadaten einer Hilfsdatei zu
ersetzen.

## Ausgewertete Primärstandards

| Anforderung | Norm | Implementierte Entscheidung |
| --- | --- | --- |
| `MOVE` benötigt `Destination`, muss Quelle und Ziel als einen Vorgang behandeln und darf nicht gecacht werden. | [RFC 4918 § 9.9](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9) | Quelle und Ziel liegen im selben benutzer- und gerätegebundenen WebDAV-Baum. Die gemeinsame Mutationssperre hält parallele DAV-Anfragen fern; Erfolg liefert keine cachebare Repräsentation. |
| Bei vorhandenem Ziel und `Overwrite: T` ist das Ziel vor dem MOVE zu ersetzen; bei `F` darf keine Mutation erfolgen. | [RFC 4918 § 9.9.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9.3), [§ 10.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.6) | `F` ergibt immer `412`. `T` ersetzt ausschließlich eine vorhandene reguläre Datei. Collections, Symlinks, Spezialdateien und nicht verwaltete Ziele bleiben abgewiesen. |
| Erfolg bei ersetztem Ziel soll `204 No Content`, bei neuem Ziel `201 Created` liefern. | [RFC 4918 § 9.9.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9.4) | Ersetzen liefert `204`, neues Verschieben `201`; `Location`, `Content-Location`, neuer starker `ETag` und `Repr-Digest` beschreiben das Ziel. |
| Bedingungen müssen vor der Methode ausgewertet werden; eine falsche Bedingung verhindert die Mutation. | [RFC 9110 § 13.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.2.2) | Der normale `If-Match` schützt die Quell-URL. Das vorhandene Ziel muss zusätzlich in einem getaggten DAV-`If`-Block seinen aktuellen starken ETag oder gültigen Lock-Token nachweisen. |
| Ein Lock-Token muss für jede durch die Anfrage veränderte gesperrte Ressource vorliegen. | [RFC 4918 § 7.5](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.5) | Quell- und Ziel-Locks werden getrennt ausgewertet. Ein Ziel-ETag ersetzt bei gesperrtem Ziel nicht das Lock-Token. Der Quell-Lock wird nach erfolgreichem MOVE freigegeben, der Ziel-Lock bleibt bestehen. |
| MOVE-Erfolg darf den Lock der Quelle nicht an das Ziel übertragen. | [RFC 4918 § 7.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.6) | Auch bei einzelnen Dateien wird ein expliziter Quell-Lock entfernt und als `webdav_lock_released_by_move` auditiert. |
| Gleiches Quell- und Ziel-URI ist verboten; fehlende Eltern, Bedingungen und Locks haben festgelegte Fehlerklassen. | [RFC 4918 § 9.9.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.9.4) | Gleiches URI ergibt `403`, fehlender Elternordner `409`, veralteter Validator oder `Overwrite: F` `412`, fehlender Lock `423`. Die zusätzliche Pflicht zu einem Zielvalidator verwendet `428`. |

### Bewusste Sicherheitsverschärfung

RFC 4918 setzt für einen fehlenden `Overwrite`-Header den Wert `T` voraus.
SimpleOffice wertet `T` zwar aus, führt den Austausch aber erst aus, wenn der
Client den aktuellen Zielzustand ausdrücklich belegt. Ein Beispiel mit starkem
Ziel-ETag lautet:

```http
MOVE /webdav/files/jens/bericht.odt.tmp HTTP/1.1
Destination: https://office.example/webdav/files/jens/bericht.odt
Overwrite: T
If: <https://office.example/webdav/files/jens/bericht.odt> (["<sha256>"])
```

Ein Client, der das Ziel zuvor gesperrt hat, kann statt des ETags dessen Token
getaggt mitsenden. Fehlt beides, antwortet der Server mit `428 Precondition
Required` und dem aktuellen Ziel-ETag. Diese Erweiterung verhindert, dass eine
veraltete FreeFileSync- oder Dateimanager-Ansicht eine zwischenzeitlich
gespeicherte Version verliert.

RFC 4918 modelliert Overwrite als Löschen des Ziels und anschließendes
Verschieben der Quelle. SimpleOffice erhält bewusst die stabile Ziel-ID und
deren Rechte sowie fachliche Metadaten. Nur der Inhalt wird ersetzt; die Quelle
wird separat wiederherstellbar soft-gelöscht. Diese Abweichung schützt
Freigaben, Aufbewahrung und Referenzen davor, durch Metadaten einer temporären
Datei umgangen zu werden. Das sichtbare URL-Ergebnis und die HTTP-Statuscodes
entsprechen dem MOVE-Ablauf, die interne Ressourcenidentität jedoch nicht einer
reinen Delete-plus-Move-Implementierung.

## Ablauf, Versionen und Rollback

1. Authentifizierung, Schreibbereich, sichere Pfade, Quellbedingung, Ziel-
   bedingung, Locks und beide SimpleOffice-Bearbeitungssperren werden geprüft.
2. Der Quellinhalt wird gegen Größenlimit und Quell-ETag geprüft.
3. Der bisherige Zielinhalt wird hashverifiziert unter
   `.simpleoffice-meta/content-versions/<ziel-id>/` archiviert.
4. Der neue Inhalt ersetzt das Ziel über den bestehenden atomaren
   Temporärdatei-und-Rename-Pfad. Ziel-ID, Rechte, Tags und Eigenschaften
   bleiben erhalten.
5. Die Quelle wird mit erwarteter SHA-256-Prüfung in die private
   WebDAV-Wiederherstellung verschoben.
6. Schlägt Schritt 5 fehl oder wurde die Quelle parallel verändert, wird der
   archivierte Zielinhalt wiederhergestellt. Der Rollback wird gesondert
   revisionssicher protokolliert; Quelle und bisheriges Ziel bleiben sichtbar.
7. Erst nach vollständigem Erfolg werden Quell- und Zielpfad in das
   RFC-6578-Sync-Journal geschrieben und der explizite Quell-Lock entfernt.

Der gemeinsame WebDAV-Mutationslock verhindert, dass ein zweiter DAV-Client
Zwischenzustände beobachtet. Direkte externe Änderungen im Dokumentenroot
werden durch die erneute Hashprüfung erkannt; sie verwenden den DAV-Lock jedoch
nicht und sollten im produktiven Betrieb vermieden werden.

## Bedienung und Client-Kompatibilität

- **LibreOffice:** Dokument über die WebDAV-URL öffnen und normal speichern.
  Lock-basierte temporäre Speichervorgänge können das vorhandene Ziel ersetzen,
  ohne dessen Freigaben oder Historie zu verlieren.
- **Nautilus/GNOME Files, Windows Explorer und Finder:** Das Ersetzen einer
  Datei ist möglich, wenn die DAV-Schicht einen Ziel-Lock oder getaggten
  Ziel-ETag sendet. Ein Client ohne Zielvalidator erhält verständlich `428`
  statt einer stillen Überschreibung.
- **FreeFileSync:** Die Variante **Zwei Wege** oder **Aktualisieren** nur mit
  Konflikterkennung einsetzen. Wird ein Ziel ersetzt, muss die verwendete
  WebDAV-Schicht dessen Validator weiterreichen. Bei `412` oder `428` neu
  vergleichen und keine automatische „Ziel gewinnt“-Regel erzwingen.

Neue Ziele werden unverändert mit `MOVE` und `201` verschoben. `COPY` und das
Ersetzen vorhandener Collections bleiben absichtlich konservativ und ergeben
bei vorhandenem Ziel `412`.

## Rechte, Datenschutz und Ausfallverhalten

- Quelle und Ziel müssen im selben Benutzerbaum und im Bereich desselben
  schreibenden App-Passworts liegen. Fremde Hosts und Benutzer ergeben `502`.
- Beide Dokumente müssen bearbeitbar sein. Fachliche Sperren,
  Aufbewahrungsregeln und laufende Bereinigungszustände werden nicht umgangen.
- Zielrechte und Zielmetadaten bleiben erhalten; es entsteht keine neue
  Freigabe. Der wiederherstellbare Quellinhalt ist nur seinem Benutzer
  zugänglich.
- Es erfolgt keine externe Übertragung und keine Änderung an ClamAV-
  Konfiguration. Die Quelle wurde bereits beim WebDAV-`PUT` nach der optionalen
  fail-closed Upload-Regel geprüft; MOVE führt keine zweite externe Prüfung aus.
- `412` bedeutet veraltete Quelle oder Ziel beziehungsweise `Overwrite: F`;
  `413` eine zu große Quelle; `423` einen fehlenden Lock; `428` einen fehlenden
  Zielvalidator; `507` einen Speicher-/Rollbackfehler. Bei einem vollständigen
  Rollback bleiben beide sichtbaren Dateien unverändert.

Audit-Ereignisse umfassen `document_content_replaced`,
`document_soft_deleted`, `webdav_document_replaced_via_move`,
`webdav_lock_released_by_move` und im Fehlerfall
`webdav_document_replace_rolled_back`. Hashes, IDs, Pfade, Benutzer,
Zeitpunkte und Wiederherstellungspfad machen den Vorgang nachvollziehbar.

## Migration, Tests, Grenzen und Rückkehr

Es gibt keine Datenmigration und keine neue Pflichtkonfiguration. Vorhandene
Dokumente, Versionen, Rechte, Gerätekennwörter und Aufbewahrungsregeln bleiben
kompatibel. Alte Clients können weiterhin direkt mit `PUT` plus `If-Match`
speichern oder neue Namen verwenden.

Automatisierte Tests prüfen erfolgreichen ETag- und Lock-Ablauf, `204` und neue
Integritätsheader, Erhalt von Ziel-ID, Rechten, Tags und Dead Properties,
Inhaltsversion und Quellwiederherstellung, `Overwrite: F`, fehlende und
veraltete Validatoren, getrennte Quell-/Ziel-Locks, Lockfreigabe sowie einen
simulierten Fehler mit Ziel-Rollback.

Bewusste Grenzen:

- Collections und COPY-Ziele werden nicht überschrieben.
- Die Ziel-ID und Zielmetadaten bleiben aus Sicherheitsgründen erhalten; dies
  ist keine bitgenaue Abbildung der internen Delete-plus-Move-Identität.
- Der konsumierte temporäre Quellinhalt belegt bis zur bestehenden manuellen
  Bereinigung Platz in der Wiederherstellung; Aufbewahrungsregeln wurden nicht
  verändert.
- Ein Prozessabbruch außerhalb der kontrollierten Python-Fehlerpfade kann wie
  bei jeder mehrstufigen Metadatenoperation einen Initialscan erfordern; die
  Inhaltsarchive bleiben hashverifiziert reparierbar.

Zum Deaktivieren genügt ein read-only Gerätezugang oder dessen Widerruf. Ein
Downgrade benötigt keine Konvertierung: archivierte Zielversionen und
soft-gelöschte Quellen verwenden bereits vorhandene SimpleOffice-Formate.
