# Portable Dateinamen für WebDAV-Desktop-Clients

## Zweck und Nutzen

Ein WebDAV-Bestand wird häufig nacheinander von Linux, Windows und macOS
bearbeitet. Diese Systeme unterscheiden sich bei Groß-/Kleinschreibung,
Unicode-Normalisierung und reservierten Namen. Eine unter Linux zulässige Datei
wie `CON.txt` kann deshalb im Windows Explorer nicht zuverlässig angelegt oder
synchronisiert werden. Zwei Namen, die sich nur durch Großschreibung oder die
Unicode-Darstellung eines Akzents unterscheiden, können auf einem anderen
Dateisystem sogar dieselbe Datei bezeichnen.

SimpleOffice prüft daher jedes **neue Pfadsegment**, bevor es durch `PUT`,
`MKCOL`, Lock-null-`LOCK`, `COPY` oder `MOVE` sichtbar wird. Sichere Namen wie
`Käse 📄.odt` bleiben unverändert erhalten. Problematische Namen werden weder
still umgeschrieben noch teilweise angelegt, sondern mit einer verständlichen
`409 Conflict`-Antwort abgewiesen. Das verhindert unbemerkte Umbenennungen,
Dubletten und Überschreibungen beim Wechsel zwischen LibreOffice,
FreeFileSync, Nautilus, Explorer und Finder.

## Primärstandards und abgeleitete Anforderungen

Die folgenden Originaltexte wurden für die Implementierung ausgewertet. RFC
5198 beschreibt ein konservatives Unicode-Interchange-Profil; er schreibt
nicht unmittelbar das Namensmodell eines WebDAV-Servers vor. SimpleOffice
übernimmt daraus bewusst die für plattformübergreifende Dateiablagen sicheren
Regeln.

| Schlüsselwort | Primärquelle | Anforderung und Umsetzung |
|---|---|---|
| MUST | [RFC 4918 §5.2](https://www.rfc-editor.org/rfc/rfc4918.html#section-5.2) | Eine WebDAV-Collection muss eine konsistente URL-Zuordnung ihrer Mitglieder anbieten. SimpleOffice lässt deshalb keine zwei Geschwisternamen zu, die nach NFC-Normalisierung und Unicode-Casefolding dieselbe portable Identität besitzen. |
| MUST | [RFC 4918 §8.3](https://www.rfc-editor.org/rfc/rfc4918.html#section-8.3) | Server müssen Nicht-ASCII-Zeichen in URLs korrekt behandeln und dürfen Prozentkodierung nicht doppelt dekodieren. Die vorhandene WebDAV-Routenauflösung bleibt unverändert; geprüft wird erst das bereits einmal dekodierte einzelne Mitgliedssegment. |
| MUST NOT | [RFC 5198 §2](https://www.rfc-editor.org/rfc/rfc5198.html#section-2) | C0-/C1-Steuerzeichen sind für den Netzwerkaustausch ungeeignet. SimpleOffice lehnt Unicode-Kategorie `Cc` vollständig ab; zusätzlich werden Surrogate, Private-Use- und nicht zugewiesene Codepunkte abgewiesen. |
| SHOULD | [RFC 5198 §2](https://www.rfc-editor.org/rfc/rfc5198.html#section-2) und [§3](https://www.rfc-editor.org/rfc/rfc5198.html#section-3) | Text sollte in NFC übertragen werden; kanonisch äquivalente Folgen müssen als äquivalent betrachtet werden. Neue Namen müssen bereits NFC sein und die Kollisionsprüfung verwendet NFC plus Casefolding. Es gibt keine stille Normalisierung. |
| SHOULD | [Unicode Standard Annex #15](https://www.unicode.org/reports/tr15/) | NFC bewahrt die übliche visuelle Darstellung und erzeugt eine stabile kanonische Form. SimpleOffice verwendet NFC, nicht die stärker verändernde Kompatibilitätsnormalisierung NFKC. |
| Plattformprofil | [Microsoft: Naming Files, Paths, and Namespaces](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file) | Die Zeichen `< > : " / \\ | ? *`, abschließende Leerzeichen/Punkte und Gerätenamen wie `CON`, `NUL`, `COM1` oder `LPT1` sind unter Windows nicht portable. Diese Regeln gelten auch für Namen mit Erweiterung und die dokumentierten hochgestellten Ziffernvarianten. |
| MAY | [RFC 5198 §6](https://www.rfc-editor.org/rfc/rfc5198.html#section-6) | Anwendungen dürfen zusätzliche Sicherheitsgrenzen setzen. SimpleOffice blockiert bidirektionale Steuerzeichen sowie führende ASCII-Leerzeichen und begrenzt ein neues UTF-8-Segment auf 200 Bytes. |

## Implementierte Namensrichtlinie

Für neue Dateien und Ordner gelten zusammenhängend folgende Regeln:

- Der Name ist Unicode-NFC und höchstens 200 UTF-8-Bytes lang. Das lässt auf
  üblichen Dateisystemen mit 255-Byte-Segmentgrenze ausreichend Platz für den
  zufälligen Namen der atomaren temporären Upload-Datei.
- Steuerzeichen, Surrogate, Private-Use-, nicht zugewiesene und bidirektionale
  Steuerzeichen sind nicht zulässig.
- Windows-reservierte Zeichen, Gerätenamen und Namen mit führendem ASCII-
  Leerzeichen oder abschließendem Leerzeichen/Punkt sind nicht zulässig.
- Geschwister dürfen nach `NFC(casefold(NFC(name)))` nicht kollidieren. Damit
  wird ein Linux-Server absichtlich konservativer behandelt als sein lokales
  Dateisystem, damit ein späterer Windows- oder macOS-Abgleich eindeutig bleibt.
- Die Prüfung läuft innerhalb derselben globalen Mutationssperre wie das
  Anlegen, Kopieren oder Verschieben. Zwei gleichzeitige Anfragen können die
  Kollisionsprüfung daher nicht beide erfolgreich passieren.

Die Antwort enthält `409 Conflict`, eine XML-Fehlerbedingung
`<s:portable-file-name reason="…"/>` und den Diagnose-Header
`X-SimpleOffice-Name-Reason`. Clients können unter anderem
`unicode-nfc-required`, `windows-reserved-device-name`,
`windows-reserved-character`, `bidirectional-control-character`,
`case-or-normalization-collision` oder `name-too-long` anzeigen.

## Verhalten je WebDAV-Methode

| Methode | Prüfung |
|---|---|
| `PUT` | Eine neue Ressource muss die Richtlinie erfüllen. Das Überschreiben exakt derselben vorhandenen URL bleibt möglich und benötigt weiterhin ETag oder Lock-Token. |
| `MKCOL` | Der neue Ordnername und alle Kollisionen mit Dateien oder Ordnern desselben Elternordners werden geprüft. |
| `LOCK` | Eine Lock-null-Ressource darf nur unter einem portablen neuen Namen reserviert werden. Ein Lock auf einer exakt vorhandenen Altdatei bleibt möglich. |
| `COPY` | Der Zielname wird geprüft. Eine Kopie unter einem lediglich anders geschriebenen Alias wird abgewiesen. |
| `MOVE` | Der Zielname wird geprüft; die Quelle wird nur für diese Kollisionsprüfung ausgenommen. Dadurch sind eine reine Groß-/Kleinschreibungsänderung und die bewusste NFC-Bereinigung eines Altbestands möglich. |
| `GET`, `HEAD`, `PROPFIND`, `DELETE` | Vorhandene Altbestände werden nicht versteckt oder automatisch verändert. Normale Rechte-, Lock- und Aufbewahrungsregeln gelten weiter. |

## Bedienung mit Desktop-Clients

In LibreOffice, Nautilus/GNOME Files, Windows Explorer und Finder kann wie
bisher direkt gespeichert oder umbenannt werden. Bei `409` sollte der Name im
Client geändert und der Vorgang wiederholt werden. Empfohlen sind kurze,
sichtbare NFC-Namen ohne nachgestellten Punkt oder Leerzeichen. Akzente,
Umlaute, viele Schriftsysteme und Emoji sind erlaubt, sofern sie die Regeln
erfüllen.

FreeFileSync sollte zuerst im Vorschaumodus laufen. Erscheint für eine neue
Datei ein Namenskonflikt, ist der Name auf der Quellseite bewusst zu bereinigen;
ein automatisches Überschreiben des Zieles darf nicht erzwungen werden. Ein
bestehender nicht portabler Name kann über den eingehängten WebDAV-Bestand per
Umbenennen/`MOVE` in eine sichere Form überführt werden.

## Rechte, Audit, Sicherheit und Datenschutz

Die Namensprüfung erweitert keine Rechte. Lesezugänge werden vor der Richtlinie
mit `403` abgewiesen und erzeugen keinen Namensrichtlinien-Eintrag; fremde
Benutzerbäume bleiben als `404` verborgen. ETag-, Lock-, Aufbewahrungs- und
Ordnerrechte werden nicht umgangen.

Jede Richtlinienablehnung wird als `webdav_portable_name_rejected` mit Benutzer,
Methode, Elternpfad, Grund, UTF-8-Länge und SHA-256 des Namens revisionssicher
protokolliert. Der abgewiesene Klartextname wird aus Datenschutzgründen nicht in
Audit-Snapshot oder Logbuch übernommen. Dateiinhalt wird bei einer
Namensablehnung weder geschrieben noch an ClamAV oder einen externen Dienst
übergeben.

Die zusätzliche Sperre gegen bidirektionale Steuerzeichen reduziert
Täuschungen wie eine visuell umgedrehte Dateiendung. Eine allgemeine
Verwechslungsprüfung ähnlich aussehender Buchstaben verschiedener
Schriftsysteme ist bewusst nicht implementiert, weil sie legitime Namen stark
einschränken und sprachabhängige Entscheidungen erzwingen würde.

## Migration und Rückwärtskompatibilität

Es gibt keine Daten- oder Datenbankmigration. Vorhandene nicht portable oder
nicht NFC-normalisierte Ressourcen bleiben unter ihrer exakten URL lesbar,
bearbeitbar, sperrbar, versionierbar und löschbar. Sie können gezielt per
`MOVE` auf einen freien sicheren Namen umbenannt werden; die Dokument-ID,
Versionen, Tags, Audit- und Aufbewahrungsdaten bleiben dabei erhalten. Das
gleichzeitige Anlegen eines kanonisch oder nur in der Großschreibung
abweichenden Duplikats wird verhindert.

Eine automatische Massenumbenennung findet nicht statt, weil sie externe
Verweise und Offline-Synchronisationen brechen könnte. Für einen Altbestand
sollte zuerst `PROPFIND` beziehungsweise die Dateimanageransicht geprüft und
dann jede problematische Ressource einzeln nachvollziehbar umbenannt werden.

## Fehler- und Ausfallverhalten

- Eine Richtlinienverletzung liefert deterministisch `409`; es entsteht keine
  Datei, kein Ordner und kein Lock-null-Platzhalter.
- Fehlerantworten enthalten den maschinenlesbaren Grund, aber nicht zusätzlich
  den möglicherweise sensiblen Namen.
- Ein gleichzeitiger Namenskonflikt wird unter der Mutationssperre erkannt.
- Bereits bestehende Ressourcen und Revisionen bleiben bei jeder Ablehnung
  unverändert.
- Die 200-Byte-Grenze schützt die atomare Ablage auf typischen Dateisystemen;
  Gesamtpfad- und volumespezifische Grenzen des Betriebssystems bleiben
  zusätzlich wirksam und werden als Speicher-/Dateisystemfehler behandelt.

## Automatisierte Tests

Die WebDAV-Tests prüfen:

- Unicode-/Emoji-Roundtrip über `PUT`, `PROPFIND` und `GET`;
- NFD-Namen, Windows-Gerätenamen einschließlich hochgestellter Varianten,
  reservierte Zeichen, Rand-Leerzeichen/-Punkte, Bidi-Steuerzeichen,
  Private-Use-Zeichen und zu lange UTF-8-Segmente;
- Großschreibungs- und Normalisierungskollisionen bei `PUT`, `MKCOL` und
  `COPY`;
- unveränderten Zugriff auf einen künstlich erzeugten NFD-Altbestand sowie
  dessen ID-stabile Bereinigung per `MOVE`;
- erlaubtes Case-only-`MOVE`, aber abgewiesenes Alias-`COPY`;
- Lock-null-Schutz und die Reihenfolge von Rechte- und Namensprüfung;
- datensparsames Audit ohne abgewiesene Klartextnamen.

Zusätzlich läuft die gesamte WebDAV- und Projekttestsuite in GitHub Actions.

## Bekannte Grenzen und Deaktivierung

Die portable Kollisionsidentität ist absichtlich strenger als viele
Linux-Dateisysteme. Sie verwendet keine NFKC- oder Homoglyphenprüfung. Das
Unicode-Verhalten entspricht der vom eingesetzten Python bereitgestellten
Unicode-Datenbank. Exotische volumespezifische Verbote, vollständige
Windows-Pfadlängen und Dateisysteme mit eigener sprachabhängiger
Großschreibung können zusätzliche Grenzen haben.

Die Richtlinie besitzt keinen unsicheren Laufzeit-Schalter. Zur Rückkehr auf
das vorherige Verhalten kann ausschließlich der zugehörige Code-Commit
zurückgenommen werden. Da keine Migration stattfindet, bleiben alle inzwischen
angelegten sicheren Dateien unverändert nutzbar; Audit-, Versions- und
Aufbewahrungsdaten dürfen dabei nicht gelöscht werden.
