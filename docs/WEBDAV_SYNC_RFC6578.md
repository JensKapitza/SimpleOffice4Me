# Effizienter WebDAV-Abgleich nach RFC 6578

## Zweck und Nutzen

SimpleOffice unterstützt `DAV:sync-collection`, damit kompatible WebDAV-Clients
nicht bei jedem Abgleich den gesamten Dokumentbestand erneut per `PROPFIND`
vergleichen müssen. Nach dem ersten vollständigen Lauf liefert ein
sammlungsspezifisches Sync-Token nur neu angelegte, geänderte, verschobene oder
gelöschte Mitglied-URLs. Der eigentliche Dateiinhalt wird weiterhin mit `GET`
und starkem ETag geladen; Schreiben bleibt durch App-Passwort, Locks und
HTTP-Vorbedingungen geschützt.

Die Funktion arbeitet automatisch an der bestehenden WebDAV-Adresse. Es gibt
keinen externen Dienst, kein zusätzliches Geheimnis und keinen Schalter im
Client. LibreOffice, Nautilus, Explorer, Finder und FreeFileSync funktionieren
wie bisher, auch wenn sie den optionalen REPORT nicht verwenden. Clients mit
RFC-6578-Unterstützung können den effizienteren inkrementellen Weg wählen.

## Auswertung des Primärstandards

Maßgeblich ist
[RFC 6578 – Collection Synchronization for WebDAV](https://www.rfc-editor.org/rfc/rfc6578.html).

| Normative Aussage | Abschnitt | Umsetzung in SimpleOffice |
|---|---|---|
| Unterstützende Sammlungen **MUST** `sync-collection` in `DAV:supported-report-set` nennen. | [§3.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.2) | Jede sichtbare Dateisammlung kündigt den Report an; `OPTIONS` nennt zusätzlich `sync-collection` und `REPORT`. |
| Der Request **MUST** eine Sammlung adressieren und `sync-token`, `sync-level` und `prop` enthalten; `limit` ist optional. | [§3.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.2), [§6.1](https://www.rfc-editor.org/rfc/rfc6578.html#section-6.1) | Form, XML-Größe und Zieltyp werden geprüft. Fehlende Elemente, Nicht-Sammlungen und ungültiges XML ergeben `400`. |
| Der REPORT ist nur für `Depth: 0` definiert. | [§3.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.2) | Fehlendes `Depth` gilt als `0`; jeder andere Wert wird mit `400` abgewiesen. |
| `sync-level` **MUST** `1` oder `infinite` sein. Level 1 erfasst direkte, `infinite` alle Nachfahren. | [§3.3](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.3), [§6.3](https://www.rfc-editor.org/rfc/rfc6578.html#section-6.3) | Beide Werte sind implementiert. Ein Token bleibt unabhängig vom gewählten Level gültig. |
| Ein leeres Token **MUST** alle aktuellen Mitglieder, aber keine früher entfernten URLs liefern. | [§3.4](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.4) | Der Initiallauf erzeugt einen Snapshot des sichtbaren Baums; Steuerdateien, Symlinks und Spezialdateien fehlen. |
| Danach **MUST** der Server neue oder ETag-geänderte Mitglieder sowie entfernte Mitglied-URLs melden. Jede URL darf nur einmal erscheinen. | [§3.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.2), [§3.5](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.5) | Ein persistentes Journal fasst mehrere Änderungen je Pfad auf den letzten Zustand zusammen. Entfernte URLs erhalten einen `404`-Status ohne `propstat`. Entfernen und erneutes Belegen derselben URL erscheint als Änderung. |
| Bei einer unter `infinite` entfernten Sammlung **MUST NOT** jedes Kind zusätzlich als entfernt erscheinen. | [§3.5.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.5.2) | Der Report meldet die entfernte Sammlung einmal und unterdrückt ihre gelöschten Nachfahren. |
| Das Ergebnis **MUST** `207 Multi-Status`, passende `DAV:response`-Elemente und ein neues Token enthalten. | [§3.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.2), [§6.4](https://www.rfc-editor.org/rfc/rfc6578.html#section-6.4) | Erfolgreiche Reports liefern diese Struktur und für vorhandene Ressourcen ETag, Typ, Größe und Änderungszeit. |
| Sync-Tokens **MUST** gültige URIs sein und vom Client als undurchsichtig behandelt werden. Ein ungültiges Token verletzt `DAV:valid-sync-token`. | [§3.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.2), [§4](https://www.rfc-editor.org/rfc/rfc6578.html#section-4) | Tokens sind zufällige `urn:uuid:`-URIs, werden exakt validiert und sind an Benutzer und Zielsammlung gebunden. Fremde, erfundene oder aus der Historie gefallene Tokens ergeben `403` mit `DAV:valid-sync-token`. |
| `DAV:sync-token` **MUST** auf unterstützenden Sammlungen definiert und als WebDAV-`If`-State-Token verwendbar sein; bei `allprop` **SHOULD NOT** es ungefragt erscheinen. | [§4–5](https://www.rfc-editor.org/rfc/rfc6578.html#section-4) | Explizites `PROPFIND` liefert das geschützte Token. Getaggte `If`-Bedingungen werden vor Mutationen sammlungs- und benutzergebunden geprüft; veraltete Zustände ergeben `412`. Ein gewöhnliches `allprop` löst keinen teuren Vollabgleich aus. |
| Server **MAY** Historie begrenzen, sollen Tokens aber nur bei Notwendigkeit ungültig machen. | [§3.1](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.1), [§3.2](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.2) | Pro Sammlung bleiben höchstens 4.096 Änderungen und 512 verwendbare Tokenstände erhalten. Danach muss der Client mit leerem Token sicher voll abgleichen. |
| Ein vom Client verlangtes `limit` muss korrekt paginiert oder mit `DAV:number-of-matches-within-limits` abgewiesen werden. | [§3.6–3.7](https://www.rfc-editor.org/rfc/rfc6578.html#section-3.6) | Teil-Snapshots werden nicht vorgetäuscht: `DAV:limit` wird derzeit mit `507` und der vorgeschriebenen Fehlerbedingung abgewiesen. |

Zusätzlich bleiben die ETag- und Lock-Regeln aus
[RFC 4918](https://www.rfc-editor.org/rfc/rfc4918.html) sowie die bedingten
HTTP-Anfragen aus
[RFC 9110 §13](https://www.rfc-editor.org/rfc/rfc9110.html#section-13)
maßgeblich. Ein Sync-Report ersetzt niemals `If-Match` oder einen Lock-Token.

## Design, Speicherung und Konfliktverhalten

Der erste Report erfasst nur reguläre sichtbare Dateien und Ordner. Für Dateien
wird eine leichtgewichtige Größen-/Nanosekunden-Signatur im Journal gehalten;
der ausgegebene starke ETag bleibt der SHA-256-Inhaltshash. WebDAV-Mutationen
aktualisieren das Journal unmittelbar. Vor einem REPORT wird zusätzlich mit
dem realen Dateibaum abgeglichen, damit auch bestätigte Wiederherstellungen und
andere verwaltete Änderungen sichtbar werden.

Der Zustand liegt atomar geschrieben unter
`.simpleoffice-meta/webdav-sync.json`. Er ist weder über WebDAV noch über die
Weboberfläche erreichbar. Ein Dateilock serialisiert parallele
Journal-Aktualisierungen. Ein Absturz vor dem atomaren Austausch behält den
vorherigen gültigen Zustand; der nächste Report rekonstruiert Abweichungen aus
dem Dateibaum. Die Dokument- und Git-Audit-Historie bleibt die maßgebliche
Historie der Mutation – das Sync-Journal ist nur ein begrenzter Transportindex
und ändert keine Aufbewahrungsregel.

## Rechte, Sicherheit und Datenschutz

- `REPORT` verlangt dasselbe benutzergebundene App-Passwort wie `PROPFIND` und
  ist auch mit einem reinen Lesezugang zulässig.
- Ein Benutzer kann sein Token nicht im Pfad eines anderen Kontos verwenden.
  Fremde Benutzerpfade bleiben `404`; ein fremdes Token ist dort ungültig.
- Zufällige Tokens verraten weder Revisionsnummern noch Pfade. Antworten
  enthalten ausschließlich Mitglieder des authentifizierten Dateibaums.
- Interne Metadaten, Historie, Ordnerpolitiken, Quarantäne, Papierkorb,
  Symlinks und Spezialdateien werden nicht aufgenommen.
- Basic Authentication bleibt ausschließlich über HTTPS vertretbar. Es gibt
  keine Übertragung an Dritte und keine automatische Freigabe.
- Der XML-Anfragetext ist auf 64 KiB begrenzt. Unbekannte Reports, fehlerhaftes
  XML und nicht unterstützte Ebenen werden ohne Mutation abgewiesen.

## Bedienung und Interoperabilität

Für LibreOffice, FreeFileSync, Nautilus, Windows Explorer und Finder gelten
weiterhin die Schritte aus [WEBDAV_DATEIVERWALTUNG.md](WEBDAV_DATEIVERWALTUNG.md).
Es ist keine Clientkonfiguration für Sync-Tokens nötig. Ein unterstützender
Client entdeckt die Erweiterung über `OPTIONS` oder
`DAV:supported-report-set`, startet mit leerem Token und speichert das Token
aus dem `207`-Ergebnis für den nächsten Lauf.

FreeFileSync arbeitet typischerweise über den vom Betriebssystem eingehängten
WebDAV-Ordner und kann deshalb selbst vollständige Vergleiche ausführen.
SimpleOffice behauptet keine direkte RFC-6578-Unterstützung durch FreeFileSync.
Der Vorteil greift nur, wenn der verwendete WebDAV-Client oder Mount-Provider
den REPORT nutzt.

## Fehler- und Ausfallverhalten

- `400`: falsches Ziel, falsches `Depth`, ungültiges XML oder unvollständiger
  `sync-collection`-Request.
- `401`: App-Passwort fehlt oder ist ungültig.
- `403 DAV:valid-sync-token`: Token ist fremd, veraltet oder unbekannt; der
  Client wiederholt den Lauf mit leerem Token.
- `507 DAV:number-of-matches-within-limits`: der Client verlangt eine derzeit
  nicht unterstützte Ergebnisbegrenzung.
- `413`: XML-Anfragetext überschreitet 64 KiB.
- `207`: erfolgreicher Initial- oder Folgelauf; ein leerer Änderungssatz ist
  normal und enthält trotzdem das aktuelle Token.

## Migration, Rückwärtskompatibilität und Deaktivierung

Es gibt keine Datenbank- oder Dokumentmigration. Der Journalzustand entsteht
erst beim ersten `PROPFIND`/REPORT und kann bei deaktiviertem WebDAV ungenutzt
bleiben. Alte Clients ignorieren die zusätzliche Capability und arbeiten wie
zuvor. Zum Rückkehrverhalten kann der RFC-6578-Handler entfernt und die
Journaldatei nach einer Sicherung gelöscht werden; Dokumente, Versionen,
Freigaben, Auditdaten und Aufbewahrungsregeln bleiben unverändert. Clients
müssen danach einmal vollständig per `PROPFIND` synchronisieren.

## Tests und bekannte Grenzen

Automatisiert geprüft werden Initialabgleich, `sync-level` 1 und `infinite`,
Datei- und Ordneranlage, Inhaltsänderung, Lösch-Tombstone, Entfernen mit
anschließender Neubelegung, wiederholter leerer Abgleich, Tokenwechsel,
Benutzertrennung, ungültige Tokens, falsches `Depth`, unvollständiges XML,
`limit` und REPORT auf eine Datei. Die vollständige Suite prüft zusätzlich
WebDAV-Rechte, ETags, Locks, atomare Speicherung, Wiederherstellung und Audit.

Bewusst nicht implementiert sind paginierte `DAV:limit`-Ergebnisse sowie
verteilte Journale über mehrere unabhängig schreibende Serverknoten. Für Clusterbetrieb
ist deshalb ein gemeinsamer Dokument- und Metadatenspeicher mit genau einem
koordinierten Schreibpfad erforderlich.
