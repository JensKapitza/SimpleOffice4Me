# WebDAV-Bedingungen und ressourcengenauer Konfliktschutz nach RFC 4918

## Zweck und Nutzen

LibreOffice, Finder, Nautilus, Windows Explorer und Synchronisationsprogramme
reichen beim Speichern häufig einen WebDAV-`If`-Header mit Lock-Token und
optional einem ETag ein. Diese Bedingungen dürfen nicht wie eine globale
Zeichenkette behandelt werden: Ein Token gilt ausschließlich für die im Header
bezeichnete Ressource. Andernfalls könnte eine Sperre für Datei A irrtümlich
einen Schreibvorgang auf Datei B autorisieren.

SimpleOffice wertet den Header deshalb vor jeder WebDAV-Mutation vollständig
und begrenzt aus. Getaggte absolute oder pfadbezogene URLs, ungetaggte Listen,
`Not`, State Tokens, ETags, mehrere alternative Listen und mehrere Bedingungen
innerhalb einer Liste werden berücksichtigt. Erst eine erfolgreiche Auswertung
stellt ein Lock-Token genau für seinen Lock-Schlüssel bereit. Die eigentliche
Rechte-, Lock-, Versions- und Audit-Prüfung bleibt zusätzlich bestehen.

## Ausgewertete Anforderungen der Primärstandards

| Normative Anforderung | Abgeleitete Umsetzung |
| --- | --- |
| Der `If`-Header **MUST** State Tokens und ETags in der Grammatik aus getaggten oder ungetaggten Listen transportieren; beide Formen dürfen nicht gemischt werden. | Ein eigener Parser akzeptiert genau eine der beiden Formen. Lose Token, leere Listen, fehlende Klammern, gemischte Produktionen sowie Steuerzeichen werden vor der Mutation abgewiesen. [RFC 4918 §10.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.4), [§10.4.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.4.2) |
| Bedingungen innerhalb einer Liste werden gemeinsam geprüft; mehrere Listen einer Ressource stellen Alternativen dar. `Not` negiert die unmittelbar folgende Bedingung. | Innerhalb einer Liste gilt logisches UND, zwischen Listen logisches ODER. Nur positive, tatsächlich passende Lock-Token einer erfolgreichen Liste werden weitergereicht. [RFC 4918 §10.4.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.4.3) |
| State Tokens und ETags sind gegen den Zustand der identifizierten Ressource zu prüfen. Ein Token einer anderen Ressource darf nicht als übermitteltes Lock-Token für das Ziel gelten. | Jede Resource Tag URL wird auf den authentifizierten Dateibaum oder die stabile Dokument-URL aufgelöst. Lock-Schlüssel, ETag und Collection-Sync-Token werden ausschließlich dort verglichen. [RFC 4918 §10.4.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.4.4), [§6.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-6.4) |
| Ist keine Bedingungsliste erfolgreich, **MUST** die Anfrage mit `412 Precondition Failed` scheitern. | Eine syntaktisch gültige, aber falsche Lock-, ETag- oder Sync-Bedingung liefert 412, bevor Datei, Index, Lock oder Sync-Journal geändert werden. [RFC 4918 §10.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.4) |
| Eine ungetaggte Liste ist eine Kurzform für die Request-URI; getaggte Listen können bei COPY/MOVE unterschiedliche Ressourcen bezeichnen. | Ungetaggte Bedingungen werden nur der aktuellen URL zugeordnet. Getaggte Bedingungen funktionieren mit absoluten URLs desselben Hosts und lokalen Pfaden. Die Quell-Datei bei COPY/MOVE muss ihren eigenen Lock-Token tragen. [RFC 4918 §10.4.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.4.1), [§10.4.9](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.4.9) |
| ETags im `If`-Header ermöglichen gemeinsam mit Locks den Schutz gegen überschreibende parallele Änderungen; ein Server **MUST** bei einer erfolgreichen PUT-Antwort einen unveränderten Validator liefern, wenn die Repräsentation unverändert gespeichert wurde. | Starke SHA-256-ETags werden exakt verglichen. Lock und ETag können in derselben UND-Liste stehen; danach schützt die bestehende atomare SHA-256-Prüfung nochmals unmittelbar vor dem Ersetzen. [RFC 4918 §8.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-8.6), [RFC 9110 §13](https://www.rfc-editor.org/rfc/rfc9110.html#section-13) |
| UNLOCK **MUST** das Lock-Token aus dem `Lock-Token`-Header auf die Request-URI anwenden. | UNLOCK akzeptiert genau einen vollständig geklammerten `opaquelocktoken:`-Wert. Ein zufällig im `If`-Header vorkommender oder mehrfach angegebener Wert genügt nicht. [RFC 4918 §9.11](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.11), [§10.5](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.5) |
| Server **MAY** die Größe einer Anfrage aus Ressourcenschutzgründen begrenzen. | Der `If`-Header ist auf 16 KiB, 64 Listen und 256 Bedingungen begrenzt. Damit bleiben Laufzeit und Speicherbedarf auch bei bösartigen Headern vorhersagbar. [RFC 4918 §18](https://www.rfc-editor.org/rfc/rfc4918.html#section-18) |

## Bedienung und Desktop-Kompatibilität

Es ist keine zusätzliche Serverkonfiguration notwendig. Bestehende
WebDAV-App-Passwörter und die in **Dokumente auf dem Desktop bearbeiten**
angezeigten URLs bleiben unverändert.

LibreOffice kann seine übliche ungetaggte Form senden:

```http
If: (<opaquelocktoken:01234567-89ab-cdef-0123-456789abcdef>)
```

Clients mit mehreren beteiligten Ressourcen können die URL ausdrücklich
zuordnen und einen ETag ergänzen:

```http
If: <https://office.example/webdav/files/alex/Plan.odt>
    (<opaquelocktoken:01234567-89ab-cdef-0123-456789abcdef> ["sha256..."])
```

Mehrere geklammerte Listen sind Alternativen. Mehrere Bedingungen in einer
Klammer müssen dagegen alle zutreffen. FreeFileSync und Dateimanager, die keine
Locks verwenden, können weiterhin `If-Match` beziehungsweise
`If-None-Match` nutzen. Bestehende Dateien dürfen auch weiterhin nicht ohne
Lock-Token oder `If-Match` blind überschrieben werden.

## Rechte, Sicherheit und Datenschutz

- Resource Tags werden nur innerhalb des authentifizierten Benutzerbaums und
  des Ordnerbereichs des verwendeten Gerätezugangs aufgelöst. Fremde Hosts,
  Benutzer und nicht freigegebene Ordner erfüllen die Vorbedingung nicht.
- Ein valides Token wird zusätzlich mit Besitzer und aktivem Lock-Schlüssel
  verglichen. Der Header erweitert weder Lese- noch Schreibrechte.
- ETags, Lock-Token und URLs werden nicht neu in Audit-Einträge aufgenommen.
  Erfolgreiche Dateioperationen verwenden unverändert ihre bestehende
  vollständige Versions- und Aktionshistorie; abgewiesene Bedingungen ändern
  diese Historie nicht.
- Es erfolgt keine externe Anfrage zur Auflösung einer absoluten URL. Host und
  Pfad werden ausschließlich lokal geprüft; damit entsteht kein SSRF-Pfad.
- Vergleiche sicherheitsrelevanter Token und Validatoren erfolgen
  timing-resistent. Schwache ETags werden im schreibenden `If`-Pfad bewusst
  nicht als starke Gleichheit akzeptiert.

## Fehler- und Ausfallverhalten

- `400 Bad Request`: fehlerhafte Grammatik, gemischte Formen, ungültige lokale
  Resource Tag URL oder nicht erlaubte Query-/Fragment-Anteile.
- `412 Precondition Failed`: syntaktisch gültige, aber nicht erfüllte
  Bedingung sowie getaggte Ressource außerhalb von Host, Benutzer oder
  Geräteordner.
- `413 Content Too Large`: mehr als 16 KiB, 64 Listen oder 256 Bedingungen.
- `423 Locked`: der Header ist zwar gültig, übermittelt aber für die konkret
  zu ändernde gesperrte Ressource kein passendes positives Token.
- Ein abgewiesener Vorgang schreibt weder Nutzdatei noch Metadaten,
  Sync-Journal oder Lock-Laufzeit. Lock-Refresh verlängert erst nach
  erfolgreicher ressourcengenauer Auswertung.

## Migration und Rückwärtskompatibilität

Es gibt keine Migration und keine Änderung an Dateiformaten, Berechtigungen,
Aufbewahrung oder Gerätepasswörtern. Standardkonforme Header von LibreOffice
und Desktop-Clients bleiben kompatibel. Bewusst nicht mehr akzeptiert werden
mehrdeutige Header, bei denen ein Token nur irgendwo im Text vorkommt oder für
eine andere URL getaggt ist. Das ist eine Sicherheitskorrektur; solche
Anfragen müssen vom Client mit der tatsächlichen Request-URI wiederholt werden.

## Automatisierte Tests

Die Tests decken zusätzlich ab:

- fremd und hostfremd getaggte Token ohne Änderung der Datei;
- absolute und lokale Tags für die exakte Request-URI;
- UND aus Lock-Token, starkem ETag und negierter unbekannter Bedingung;
- ODER zwischen mehreren Listen und Ablehnung eines falschen ETags;
- getaggte Quell-Locks bei COPY sowie fehlende Zieldatei nach Ablehnung;
- Lock-Refresh nur für die exakte Ressource;
- UNLOCK ausschließlich mit genau einem `Lock-Token`-Header;
- fehlerhafte, gemischte und übergroße Header;
- bestehende RFC-6578-Sync-Token sowie ordnergebundene Gerätezugänge.

## Bekannte Grenzen und Rückkehr zum vorherigen Verhalten

Die Implementierung unterstützt die in SimpleOffice verwendeten
`opaquelocktoken:`- und `urn:uuid:`-State-Tokens. Andere proprietäre
State-Token-Schemata werden als nicht passend behandelt; mit `Not` können sie
RFC-konform als nicht vorhandener Zustand geprüft werden. Rekursive
Collection-Locks und überschreibendes COPY/MOVE bleiben unabhängig davon noch
nicht implementiert.

Die sichere Auswertung ist nicht separat abschaltbar, weil die frühere lose
Token-Suche eine Umgehung der Ressourcenzuordnung erlaubte. Ein Rollback erfolgt
nur durch Rückkehr auf die vorherige Anwendungsversion; vorhandene Dateien,
Locks und Zugangsdaten benötigen dabei keine Konvertierung. Bereits aktive
Locks laufen weiterhin spätestens nach der serverseitig begrenzten Stunde ab.
