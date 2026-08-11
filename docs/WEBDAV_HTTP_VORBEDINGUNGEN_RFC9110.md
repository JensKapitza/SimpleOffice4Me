# Einheitliche HTTP-Vorbedingungen für WebDAV nach RFC 9110

## Zweck und Nutzen

LibreOffice, FreeFileSync, Nautilus/GNOME Files, Windows-Datei-Explorer und
Finder führen dieselbe Dateioperation mit unterschiedlichen Kombinationen aus
ETag, Datum und WebDAV-Lock aus. SimpleOffice4Me prüft die HTTP-Vorbedingungen
für `PUT`, `DELETE`, `PROPPATCH`, `MKCOL`, `COPY` und `MOVE` deshalb an einer
gemeinsamen Stelle und in der von HTTP vorgegebenen Reihenfolge. Ein Client
kann nur den Stand verändern, den er tatsächlich gesehen hat; eine falsche
Bedingung beendet die Anfrage vor Datei-, Metadaten-, Versions-, Papierkorb-
oder Sync-Änderungen.

Der Ausbau ergänzt den ressourcengenauen WebDAV-`If`-Header und Lock-Token. Er
ersetzt sie nicht. Insbesondere verlangt das Überschreiben einer vorhandenen
Datei in der hierarchischen Dateiablage weiterhin einen passenden `If-Match`-
Wert oder Lock-Token. Damit bleibt ein blindes Speichern auch dann verboten,
wenn ein Client lediglich eine großzügige Datumsbedingung sendet.

## Maßgebliche Anforderungen und Umsetzung

| Normative Anforderung | Umsetzung in SimpleOffice4Me |
|---|---|
| Ein Empfänger von `If-Match` **MUST** die Methode nur ausführen, wenn mindestens ein angegebener starker Entity-Tag passt; `*` passt genau bei vorhandener Darstellung. Bei einem Fehlschlag ist `412 Precondition Failed` vorgesehen. | Kommagetrennte Listen werden vollständig geparst. Schwache Tags passen nie für `If-Match`; `*` unterscheidet vorhandene von nicht vorhandenen Dateien und Sammlungen. [RFC 9110 §13.1.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.1) |
| `If-None-Match` verwendet den schwachen Vergleich. Bei unsicheren Methoden **MUST** eine Übereinstimmung die Methode verhindern; `*` schützt eine Neuanlage davor, eine bereits vorhandene Ressource zu ersetzen. | Starke und schwache Listeneinträge werden gegen den aktuellen SHA-256-ETag geprüft. `If-None-Match: *` funktioniert für Dateien und Sammlungen. Ein Treffer ergibt vor jeder Mutation `412`. [RFC 9110 §13.1.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.2) |
| `If-Unmodified-Since` **MUST** ignoriert werden, wenn `If-Match` vorhanden ist. Ungültige Datumswerte **MUST** ignoriert werden. Ist die gewählte Darstellung neuer, **MUST NOT** der Origin-Server die angeforderte Methode ausführen. | Der Server vergleicht auf HTTP-Sekundengenauigkeit mit dem tatsächlich ausgegebenen `Last-Modified`; ein Fehlschlag ergibt `412`. Ein vorhandener `If-Match` hat Vorrang, ungültige Datumswerte verändern das bisherige Verhalten nicht. [RFC 9110 §13.1.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.4) |
| Vorbedingungen **MUST** in der Reihenfolge `If-Match`, ersatzweise `If-Unmodified-Since`, danach `If-None-Match` ausgewertet werden. Sie werden nach den normalen Anforderungsprüfungen und unmittelbar vor der Methode ausgewertet. | Alle schreibenden Dateioperationen verwenden dieselbe Auswertungsfunktion unter dem globalen WebDAV-Mutationslock. Existenz, Pfad, Rechte, Methodenparameter und DAV-`If` werden weiterhin vor der fachlichen Mutation geprüft. [RFC 9110 §13.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.2.2) |
| Wenn eine ausgewertete HTTP- oder WebDAV-Bedingung falsch ist, **MUST** der Server `412` zurückgeben. | Falsche HTTP-ETags, Datumsbedingungen und DAV-`If`-Listen führen einheitlich zu `412`; Dateiinhalt, Dead Properties, Papierkorb, Versionen und Sync-Journal bleiben unverändert. [RFC 4918 §12.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-12.1), [§10.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.4) |
| Wenn der angeforderte Zustandswechsel nachweislich schon erfolgreich war, **MAY** der Origin-Server statt `412` einen erfolgreichen Status senden. | Diese optionale Duplikaterkennung wird bewusst nicht behauptet: Ohne eindeutige, benutzergebundene Operations-ID lässt sich nicht sicher beweisen, dass genau diese Anfrage bereits erfolgreich war. SimpleOffice4Me liefert daher konservativ `412`. [RFC 9110 §13.1.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.1), [§13.1.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.2) |

## Designentscheidungen

- Datei-ETags sind starke, in Anführungszeichen gesetzte SHA-256-Werte. Der
  parser akzeptiert normgerechte Listen und auch Kommata innerhalb eines
  Entity-Tags, statt den Header naiv am Komma zu teilen.
- Sammlungen besitzen derzeit keinen stabilen Inhalts-ETag. Für sie sind
  deshalb die Existenzbedingungen `If-Match: *` und `If-None-Match: *` sowie
  `If-Unmodified-Since` nutzbar. Eine konkrete ETag-Liste kann ohne vorhandenen
  Sammlungs-ETag nicht passen.
- Der HTTP-Vorbedingungscheck läuft nach Authentifizierung, Benutzer- und
  Pfadgrenze sowie DAV-`If`, aber vor der jeweiligen Zustandsänderung. Die
  Dokumentablage prüft den Inhalts-Hash bei atomaren Schreib- und
  Ersetzoperationen zusätzlich unmittelbar im Store. Das schließt auch ein
  Rennen zwischen Vorprüfung und Speicherung.
- Eine fehlgeschlagene Bedingung erzeugt den Audit-Eintrag
  `webdav_http_precondition_rejected` mit Benutzer, Methode, internem Pfad,
  Bedingungsname, Status und Zeit. Der vom Client gesendete ETag oder Datumswert
  wird bewusst nicht gespeichert.
- Die bestehende Pflicht zu `If-Match` oder Lock-Token beim Überschreiben einer
  vorhandenen Datei bleibt eine zusätzliche Sicherheitsrichtlinie. Ein Client
  kann sie nicht nur mit `If-Unmodified-Since` umgehen.

## Bedienung in Desktop-Clients

Übliche Clients senden die Validatoren automatisch:

- **LibreOffice** liest ETag und `Last-Modified` beim Öffnen und sendet beim
  Speichern je nach Verbindung `If-Match`, einen WebDAV-Lock oder beides.
- **FreeFileSync** sollte im Modus „Dateizeit und -größe“ beziehungsweise mit
  Versionskontrolle erst vergleichen und anschließend schreiben. Der Server
  schützt jeden tatsächlichen WebDAV-Schreibvorgang; für robuste bidirektionale
  Läufe empfiehlt sich dennoch die ETag-basierte Erkennung des Clients.
- **Nautilus/GNOME Files, Explorer und Finder** verwenden die vorhandene
  WebDAV-URL unverändert. Konflikte erscheinen als HTTP `412`; die Datei muss
  neu geladen beziehungsweise als Konfliktkopie gespeichert werden.

Für manuelle oder diagnostische Anfragen:

```http
PUT /webdav/files/jens/Bericht.odt HTTP/1.1
If-Match: "alter-sha256", "aktueller-sha256"
```

`If-Match` ist eine Alternative-Liste: Ein aktueller starker Eintrag genügt.
`If-None-Match` wird danach zusätzlich geprüft, wenn beide Header vorhanden
sind. `If-Unmodified-Since` ist nur der standardisierte Fallback, wenn kein
`If-Match` gesendet wurde.

## Voraussetzungen und Konfiguration

Es gibt keinen neuen Schalter und keine Datenmigration. Erforderlich sind wie
bisher HTTPS, ein persönlicher WebDAV-App-Zugang, eine vorhandene berechtigte
Dateiablage und bei gesperrten Ressourcen das passende Lock-Token. Die
Grenzwerte sind fest im Serverprofil gewählt, damit Clients keine unbeschränkte
Parserarbeit auslösen können.

## Sicherheit, Datenschutz, Rechte und Freigaben

- Vorbedingungen werden erst nach erfolgreicher Anmeldung und innerhalb der
  Pfadgrenze des App-Zugangs ausgewertet. Sie verraten keine Existenz außerhalb
  dieser Grenze und erweitern keine Datei- oder Freigaberechte.
- `412`, `400` und `413` verändern weder sichtbare Dateien noch Dead
  Properties, Locks, Papierkorb, Versionshistorie oder Sync-Token.
- Antworten zu einer vorhandenen Ressource enthalten ETag, `Last-Modified` und
  `Cache-Control: private, no-cache`, damit der Client den Konflikt gezielt
  auflösen kann. Es werden keine Inhalte oder Zugangsdaten zurückgegeben.
- Audit-Daten enthalten nicht den vom Client gelieferten Validator. Das
  begrenzt die Speicherung potenziell personenbezogener oder absichtlich
  übergroßer Headerwerte.
- Es entstehen keine automatische Freigabe, externe Übertragung oder
  Lockerung bestehender Aufbewahrungsregeln.

## Formate und Protokollkompatibilität

Unterstützt werden starke und schwache RFC-Entity-Tags, `*`, mehrere
Listeneinträge sowie IMF-fixdate-kompatible Datumswerte. Die Prüfung gilt für
beide WebDAV-URL-Formen: die hierarchische Dateiablage unter
`/webdav/files/<benutzer>/` und die stabile LibreOffice-Dokument-URL.
WebDAV-`If`-Listen, Lock-Token und getaggte Zielbedingungen bei sicherem
COPY/MOVE bleiben separat nach RFC 4918 aktiv.

## Fehler- und Ausfallverhalten

- `400 Bad Request`: leerer oder syntaktisch ungültiger ETag-Header;
- `412 Precondition Failed`: gültige, aber falsche ETag-, Existenz- oder
  Datumsbedingung;
- `413 Content Too Large`: mehr als 8 KiB oder 64 Entity-Tags;
- `423 Locked`: korrekte HTTP-Bedingung, aber fehlendes oder falsches
  WebDAV-Lock-Token;
- `428 Precondition Required`: vorhandene Datei soll ohne `If-Match` und ohne
  Lock-Token überschrieben werden.

Eine ungültige HTTP-Datumsangabe wird normgemäß ignoriert. Nach `412` muss der
Client die Ressource neu lesen und darf die neue Version nicht unbemerkt
überschreiben. Interne Audit-I/O-Fehler folgen dem bestehenden fail-closed
Verhalten der Dokumenthistorie; es gibt keinen stillen Fallback ohne Prüfung.

## Migration und Rückwärtskompatibilität

Bestehende URLs, App-Passwörter, Locks, ETags, Dateien und Metadaten bleiben
unverändert. Clients ohne optionale Bedingungen funktionieren wie zuvor; für
vorhandene Dateiüberschreibungen gilt weiterhin die bereits bestehende
`If-Match`-/Lock-Pflicht. Neu ist, dass Listen korrekt ausgewertet werden und
auch `DELETE`, `PROPPATCH`, `MKCOL`, `COPY` und `MOVE` die Standardbedingungen
einheitlich beachten. Ein bisher versehentlich akzeptierter schwacher oder
syntaktisch ungültiger `If-Match`-Wert wird sicher abgewiesen.

## Tests

Die automatisierten Tests decken ab:

- starke Mehrfach-ETags, schwache `If-Match`-Ablehnung und schwachen
  `If-None-Match`-Vergleich;
- die Priorität von `If-Match` vor einem veralteten `If-Unmodified-Since`;
- stale Datumsbedingungen für `DELETE`, `PROPPATCH`, `COPY` und `MOVE` ohne
  Teiländerung;
- `*` bei vorhandenen und fehlenden Dateien sowie Sammlungen;
- ungültige, überlange und zu viele Tags mit fail-closed Fehlern;
- Audit-Historie ohne Speicherung des gelieferten Validators;
- ungültige Datumswerte und unveränderte COPY-Interoperabilität;
- bestehende Rechte-, Lock-, Retention-, Race-, Versions-, Sync- und
  Desktop-Client-Abläufe als Regressionstests.

## Bewusste Grenzen und offene Entscheidungen

- Sammlungen erhalten noch keinen eigenen starken Inhalts-ETag; ihr Inhalt
  wird über RFC-6578-Sync-Token und WebDAV-`If` geschützt.
- HTTP-Datumswerte haben nur Sekundengenauigkeit und sind schwächer als ETags.
  Sie ersetzen daher nicht die lokale Pflicht zu ETag oder Lock beim
  Dateiüberschreiben.
- Bedingte Bereichsanfragen und Cache-Validierung für `GET`/`HEAD` sind separat
  in [WebDAV-Downloads nach RFC 9110](WEBDAV_DOWNLOADS_RFC9110.md)
  dokumentiert.
- Das vorhandene Ziel einer überschreibenden COPY-/MOVE-Anfrage wird weiterhin
  über einen getaggten DAV-`If`-Block geschützt, weil normale HTTP-Bedingungen
  sich auf die Request-URI, also die Quelle, beziehen.

## Deaktivierung und Rückkehr zum vorherigen Verhalten

Die Prüfung ist Bestandteil des Sicherheitsprofils und besitzt bewusst keinen
unsicheren Laufzeitschalter. Eine Rückkehr erfordert das Zurücknehmen dieses
Commits; Daten oder Metadaten müssen dabei nicht migriert werden. Bereits
erzeugte Audit-Einträge bleiben gemäß der bestehenden Historien- und
Aufbewahrungsregeln erhalten. Ein Proxy darf die Bedingungsheader nicht
entfernen oder umschreiben.
