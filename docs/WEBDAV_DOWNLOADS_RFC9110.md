# Fortsetzbare WebDAV-Downloads nach RFC 9110

## Zweck und Nutzen

SimpleOffice liefert Dokumente über beide geschützten WebDAV-Dateipfade mit
HTTP-Validatoren und Byte-Bereichen aus. Dateimanager, LibreOffice und
Synchronisationswerkzeuge können dadurch unveränderte Dateien ohne erneute
Übertragung erkennen und einen abgebrochenen großen Download an der richtigen
Stelle fortsetzen. Die Datei wird aus genau einem geöffneten Snapshot gestreamt;
ein zeitgleich atomar ersetztes Dokument vermischt daher niemals alte und neue
Bytes in derselben Antwort.

Die Funktion verändert nur lesende `GET`- und `HEAD`-Antworten. Schreibrechte,
Freigaben, Aufbewahrung und Dateiinhalte bleiben unverändert.

## Maßgeblicher Standard

Primärquelle ist [RFC 9110 – HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html).

| Normative Anforderung | Abschnitt | Umsetzung |
|---|---|---|
| Ein Origin-Server **MUST** Vorbedingungen nach den normalen Zugriffsprüfungen und vor der Methodenausführung auswerten. | [§13.2.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.2.1) | Authentifizierung, Benutzerpfad und Existenz werden zuerst geprüft; danach folgen ETag- und Datumsbedingungen, erst anschließend `Range`. |
| `If-Match` verwendet starke Vergleiche; ein Fehlschlag verhindert die Methode. | [§13.1.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.1), [§13.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.2.2) | Nur der aktuelle starke SHA-256-ETag oder `*` wird akzeptiert; schwache und veraltete Tags erhalten `412`. |
| Ein zutreffendes `If-None-Match` **MUST** bei `GET`/`HEAD` zu `304` führen. | [§13.1.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.2) | Starke und schwache Listenvergleiche sowie `*` werden verarbeitet; der Body bleibt leer. |
| `If-Modified-Since` und `If-Unmodified-Since` sind nur in ihrer vorgeschriebenen Rangfolge auszuwerten. | [§13.1.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.3), [§13.1.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.4), [§13.2.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.2.2) | ETags haben Vorrang; ungültige Datumswerte werden ignoriert. Antworten enthalten den tatsächlich verwendeten `Last-Modified`-Wert. |
| Bei falschem `If-Range` **MUST** der `Range`-Header ignoriert werden; die vollständige aktuelle Repräsentation wird übertragen. | [§13.1.5](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1.5) | Nur ein exakt passender starker ETag oder der exakt ausgegebene HTTP-Datumswert erlaubt `206`; schwache/veraltete Validatoren ergeben `200`. |
| Ein Server darf Byte-Bereiche unterstützen und dies mit `Accept-Ranges: bytes` ankündigen. | [§14.1.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-14.1.2), [§14.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-14.3) | Einzelbereiche, offene Enden, Suffixbereiche und bis zu acht getrennte Bereiche werden unterstützt. `HEAD` ignoriert `Range` und meldet die volle Länge. |
| Eine `206`-Antwort **MUST** die für `200` verwendeten Validatorfelder tragen; ein Einzelbereich benötigt `Content-Range`. | [§15.3.7](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.3.7), [§15.3.7.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.3.7.1) | `206` enthält ETag, Last-Modified, Cache-Control, Content-Length und bei Einzelbereichen `Content-Range`. |
| Mehrere Bereiche werden als `multipart/byteranges` mit `Content-Range` je Teil übertragen. | [§15.3.7.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.3.7.2), [§14.6](https://www.rfc-editor.org/rfc/rfc9110.html#section-14.6) | Jeder Teil erhält Medientyp und Byteposition; eine begrenzte, deterministische Grenze trennt die Teile. |
| Eine `416`-Antwort **SHOULD** die aktuelle Gesamtlänge als `Content-Range: bytes */Länge` liefern. | [§14.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-14.4), [§15.5.17](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.17) | Nicht erfüllbare, ungültige, überlappende oder übermäßig viele Bereiche erhalten `416` einschließlich Gesamtlänge und Validatoren. |

## Designentscheidungen

- Der starke ETag wird aus SHA-256 des geöffneten Dateideskriptors berechnet.
  Damit beziehen sich ETag, Länge und übertragene Bytes auf denselben Snapshot.
- Inhalte werden in 64-KiB-Blöcken gestreamt. Ein vollständiger Download wird
  nicht als große Bytefolge in den Arbeitsspeicher geladen.
- Höchstens acht nicht überlappende Bereiche sind zulässig. Diese Grenze schützt
  vor speicher- und CPU-intensiven Multipart-Anfragen; eine Überschreitung wird
  nachvollziehbar mit `416` beantwortet.
- `Cache-Control: private, no-cache` erlaubt private Wiederverwendung nur nach
  erfolgreicher Validierung. Authentifizierte Inhalte werden nicht öffentlich
  cachebar gemacht.
- `If-Range` mit Datum wird nur bei exakter Übereinstimmung mit dem vom Server
  erzeugten `Last-Modified` akzeptiert. Bei Unsicherheit wird sicher die ganze
  aktuelle Datei gesendet.
- Range gilt ausschließlich für `GET`. `HEAD` liefert Metadaten der vollständigen
  Darstellung und keinen Inhalt.

## Bedienung und Konfiguration

Es ist keine zusätzliche Konfiguration erforderlich. Nach Aktualisierung der
Installation verwenden kompatible Clients die vom Server gelieferten Header
automatisch:

- LibreOffice und Dateimanager können unveränderte Dateien über ETag oder
  Last-Modified erkennen.
- Ein Client setzt zum Fortsetzen beispielsweise `Range: bytes=1048576-` und
  `If-Range: "<aktueller SHA-256-ETag>"`.
- FreeFileSync arbeitet weiterhin über das vom Betriebssystem eingehängte
  WebDAV-Laufwerk. Der Mount kann abgebrochene Downloads fortsetzen, sofern er
  HTTP-Range nutzt.
- Nautilus/GVfs, Windows Explorer/WebClient und Finder entscheiden selbst, ob
  sie Range und Validatoren nutzen. SimpleOffice benötigt keine clientseitigen
  Erweiterungen.

Voraussetzungen bleiben ein gültiges WebDAV-App-Passwort und in Produktion
HTTPS. Lesezugänge dürfen dieselben Downloadfunktionen verwenden; Schreibrechte
sind dafür nicht notwendig.

## Sicherheit, Datenschutz, Rechte und Freigaben

Range- und Bedingungsheader werden erst nach erfolgreicher WebDAV-Anmeldung,
Benutzertrennung und Pfadprüfung ausgewertet. Eine `304`, `412` oder `416`
bestätigt deshalb keine Existenz fremder Dateien. Die Antwort enthält keine
Zugangsdaten, Lock-Token oder internen Speicherpfade.

Mehrfachbereiche sind auf acht begrenzt und überlappende Bereiche werden
abgewiesen. Der Stream hält nur das bereits autorisiert geöffnete reguläre
Dateiobjekt; Symlinks, interne Metadaten, Historien und Spezialdateien bleiben
ausgeschlossen. Lesen erzeugt bewusst keine Inhaltskopie und keinen Audit-
Eintrag, weil kein gespeicherter Zustand verändert wird. Vorhandene Zugriffs-
und Downloadprotokolle des Reverse Proxys bleiben davon unberührt.

## Fehler- und Ausfallverhalten

- `304 Not Modified`: der Client besitzt bereits den aktuellen Inhalt;
- `412 Precondition Failed`: `If-Match` oder `If-Unmodified-Since` ist veraltet;
- `416 Range Not Satisfiable`: Bereich ungültig, nicht erfüllbar, überlappend
  oder über der festen Anzahlgrenze;
- `401`/`404`: Anmeldung, Benutzerpfad oder Ressource ist nicht gültig.

Ein falsches `If-Range` ist kein Fehler: Der Server antwortet mit `200` und der
vollständigen aktuellen Datei. Bei einem Verbindungsabbruch werden weder Datei
noch Metadaten verändert. Der Dateideskriptor wird beim Ende oder Abbruch des
Streams geschlossen.

## Formate und Interoperabilität

Die Funktion ist dateiformatunabhängig und arbeitet mit ODT, ODS, PDF, Bildern,
Archiven und Binärdateien identisch. Einzelbereiche verwenden `206` plus
`Content-Range`; mehrere Bereiche verwenden `multipart/byteranges`. Die stabile
Dokument-ID-URL und der hierarchische Dateibaum besitzen dasselbe Verhalten.

Proprietäre Delta- oder Block-Synchronisationsprotokolle werden nicht
angekündigt. Byte-Range spart Downloadvolumen, ersetzt aber keinen binären
Delta-Upload. Teilweises `PUT` nach RFC 9110 §14.5 wird bewusst nicht akzeptiert,
weil Office-Dateien ohne transaktionales Dateiformatwissen beschädigt werden
könnten.

## Migration und Rückwärtskompatibilität

Es gibt keine Migration. Clients ohne Range- oder Validator-Unterstützung
erhalten weiterhin `200` und die vollständige Datei. URLs, App-Passwörter,
Dateiinhalte, ETags, Locks, Versionen, Freigaben und Aufbewahrungsregeln ändern
sich nicht. Neu hinzugekommen sind `Last-Modified`, bedingte Antworten und die
tatsächliche Umsetzung des bereits angekündigten `Accept-Ranges: bytes`.

## Tests

Automatisiert geprüft werden:

- Einzel-, Suffix- und offene Byte-Bereiche auf beiden WebDAV-URLs;
- Multipart-Antworten, Längen und Teil-Header;
- ungültige, nicht erfüllbare, überlappende und mehr als acht Bereiche;
- starke und schwache ETag-Vergleiche mit korrekter Vorbedingungsreihenfolge;
- Datumsbedingungen, ungültige Datumswerte und `If-Range` mit ETag/Datum;
- identische GET-/HEAD-Validatoren und vollständige HEAD-Länge;
- stabiler geöffneter Snapshot bei atomarem Austausch der sichtbaren Datei;
- bestehende Rechte-, Pfad-, Lock-, Quota-, Sync- und Retentionstests.

## Bekannte Grenzen

- Range-Uploads beziehungsweise partielles `PUT` sind nicht implementiert.
- Mehr als acht oder überlappende Bereiche werden auch dann abgewiesen, wenn ein
  anderer Server sie verarbeiten könnte.
- Der SHA-256-Validator erfordert beim Öffnen einen vollständigen sequenziellen
  Lesevorgang. Dies begrenzt Speicherbedarf, aber nicht den I/O-Aufwand bei sehr
  großen Dateien. Ein künftig vertrauenswürdig persistierter Hash könnte diesen
  Aufwand reduzieren, muss aber externe Dateiveränderungen sicher erkennen.
- Netzwerk-Proxys können Range selbst verändern oder puffern; SimpleOffice kann
  deren Verhalten nicht steuern.

## Deaktivierung und Rückkehr

Eine gesonderte Aktivierung existiert nicht. Für eine vorübergehende Rückkehr
kann der Reverse Proxy `Range` entfernen; SimpleOffice liefert dann vollständige
`200`-Antworten, während Validatoren weiter funktionieren. Ein vollständiger
Code-Rollback betrifft keine gespeicherten Daten. Das Widerrufen eines WebDAV-
App-Passworts beendet wie bisher den Zugang des betreffenden Geräts.
