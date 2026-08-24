# Schnelle Dokument- und Retrieval-Suche

## Zweck und Nutzen

Die Dokumentübersicht bietet eine kurze Suche nach Dateiname oder Tag. Für
präzise Abfragen steht die erweiterte Retrieval-Suche bereit. Beide lesen nur
den vorhandenen, lokalen Suchindex; eine Anfrage startet weder einen
Dateisystemscan noch OCR oder Textextraktion. Damit bleibt die Oberfläche auch
bei großen Beständen reaktionsfähig.

## Bedienung

In **Dokumente – Schnell finden** genügt ein Wortanfang. `rech` findet etwa den
Tag `rechnung` und Dateinamen, die mit diesem Token beginnen. Höchstens 25
Treffer werden direkt angezeigt.

Die **Erweiterte Suche** unterstützt:

| Ausdruck | Bedeutung |
|---|---|
| `rechnung angebot` | beide Begriffe (implizites UND) |
| `rechnung UND bezahlt` | beide Begriffe |
| `rechnung ODER gutschrift` | mindestens ein Begriff |
| `tag:rechnung UND (name:angebot ODER text:liefertermin)` | Felder und Gruppierung |
| `name:ange*` | Wortanfang |
| `text: "nächste Woche"` | genaue Wortgruppe |

Englische Operatoren `AND` und `OR` sind gleichwertig. Verfügbare Felder sind
`tag:`, `name:`, `text:`, `notiz:`, `status:` und `attribut:`; die deutschen
Aliase `datei:`, `inhalt:` und `pfad:` werden ebenfalls akzeptiert.

## Architektur, Sicherheit und Datenschutz

Die Syntax wird durch einen begrenzten Parser in eine parametrisierte
SQLite-FTS5-Abfrage übersetzt. Feldnamen stammen ausschließlich aus einer
festen Positivliste; Suchwerte werden nicht als SQL übernommen. Abfragen sind
auf 2.000 Zeichen und 100 Syntaxteile begrenzt. Ungültige Felder, fehlende
Begriffe und unausgeglichene Klammern führen zu einer verständlichen Meldung
und niemals zu einem breiten Ersatzscan.

Ist FTS5 in einer SQLite-Installation nicht verfügbar, verwendet dieselbe
geprüfte Syntax eine parametrisierte `LIKE`-Abfrage gegen die Indexprojektion.
Auch dieser Rückfall liest keine Originaldateien. Suchvorgänge ändern weder
Dateien noch Tags, Freigaben, Rechte oder Aufbewahrungsregeln.

## Formate, Fehler und Ausfallverhalten

Durchsuchbar sind die bereits indexierten Pfade, Zustände, Tags, Notizen,
Attribute sowie vorhandener extrahierter Inhalt. Ein noch laufender
Hintergrundindex kann deshalb zunächst Teiltreffer liefern. Die Suche wartet
nicht auf ihn. Die manuelle Funktion **Fehlende Texte nachträglich
extrahieren** bleibt eine getrennte, ausdrücklich ausgelöste Aktion.

## Migration, Rückwärtskompatibilität und Deaktivierung

Es gibt keine Datenmigration und kein neues dauerhaftes Dateiformat. Einfache
Suchbegriffe bleiben gültig. Zur Rückkehr zum alten Verhalten kann die neue
Oberfläche entfernt werden; der bestehende Suchindex und alle Dokumentdaten
bleiben unverändert.

## Tests und bekannte Grenzen

Automatisierte Tests decken UND/ODER, Klammern, Feldfilter, Präfixe, Unicode,
ungültige Syntax, Komplexitätsgrenzen und eine kombinierte Abfrage gegen den
realen Index ab. Nicht implementiert sind `NICHT`/`NOT`, Relevanzgewichtung,
unscharfe Schreibweisen und semantische Vektorsuche. Die Ergebnismenge ist
bewusst seitenweise begrenzt.

## Standards

Für diese interne Retrieval-Grammatik existiert kein einschlägiges IETF-
Protokoll, dessen MUST-/SHOULD-/MAY-Anforderungen anzuwenden wären. WebDAV-
SEARCH nach [RFC 5323](https://www.rfc-editor.org/rfc/rfc5323.html) bleibt eine
separate Schnittstelle; diese Änderung erweitert die authentifizierte
Weboberfläche und verändert das WebDAV-Protokoll nicht.
