# Serverseitige WebDAV-Suche nach RFC 5323

## Zweck und Nutzen

SimpleOffice bietet auf dem hierarchischen WebDAV-Dateibaum die Methode
`SEARCH` mit der Grammatik `DAV:basicsearch` an. Berechtigte Benutzer und
Automatisierungen können Dateien und Ordner nach Name, Größe, Zeitstempeln und
WebDAV-Eigenschaften suchen, ohne zunächst jeden Dateiinhalt herunterzuladen.
Auch mit `PROPPATCH` gesetzte Dead Properties – zum Beispiel ein portabler
Tag – sind suchbar.

Die Suche ist rein lesend. Sie verändert keine Datei, keine Eigenschaft, keine
Freigabe und kein Sync-Token. Das Ergebnis ist eine begrenzte
`207 Multi-Status`-Antwort mit den ausdrücklich ausgewählten Eigenschaften.

## Produktreferenz TagSpaces und eigenständige Umsetzung

Als Produktreferenz wurden die öffentliche
[TagSpaces-Projektbeschreibung](https://github.com/tagspaces/tagspaces), die
[Dokumentation zur Suche](https://docs.tagspaces.org/search/) und die
[Dokumentation zu Tags](https://docs.tagspaces.org/tagging/) betrachtet. Es
wurde kein TagSpaces-Code, kein UI und kein proprietäres Datenformat
übernommen. Die Funktion ist neu im vorhandenen Python-/WebDAV-Modell von
SimpleOffice implementiert.

Gut gelöste Ansätze in TagSpaces:

- Dateien bleiben das zentrale Arbeitsobjekt und können mit lokalen Werkzeugen
  weiterverwendet werden.
- Name, Pfad, Typ, Größe, Datum und Tags sind kombinierbare Suchkriterien.
- AND, OR und Ausschluss erlauben präzise statt nur textuelle Suchen.
- Die [Tag-Varianten](https://docs.tagspaces.org/tagging/) machen den
  Zielkonflikt zwischen sichtbaren Dateinamen-Tags und separaten Metadaten
  nachvollziehbar.
- Die lokale, plattformübergreifende Arbeitsweise vermeidet eine zwingende
  Übertragung an einen Suchdienst.

SimpleOffice verbessert diese Ideen für einen gemeinsam genutzten Server:

| Thema | TagSpaces-Idee | Umsetzung und Verbesserung in SimpleOffice |
|---|---|---|
| Tags | Name oder Sidecar | WebDAV Dead Properties; keine stille Umbenennung, keine sichtbaren `.ts`-Hilfsdateien und stabile Dokument-ID |
| Suche | lokaler Index und mehrere Kriterien | standardisierte `DAV:basicsearch`-Anfrage direkt am WebDAV-Bestand |
| Rechte | lokale Ablage | Prüfung von Benutzer-, Geräte- und Ordnergrenze vor dem Durchlauf; fremde Pfade ergeben keine Trefferliste |
| Konsistenz | lokaler Dateisystemzustand | kompletter Such-Snapshot unter derselben Mutationssperre wie PUT, COPY, MOVE und DELETE |
| Überlastschutz | begrenzte Indizierung | feste Grenzen für XML, Operatoren, Tiefe, Mitglieder, Treffer und Antwortbytes |
| Datenschutz | lokale Verarbeitung | keine externe Übertragung; Audit enthält Umfang und Ergebniszahl, aber keine Suchliterale oder Treffernamen |
| Konflikte | Datei-/Sidecar-Zuordnung | Eigenschaften sind an stabile Datei-/Ordner-IDs gebunden und folgen der vorhandenen COPY-/MOVE-Semantik |

Der allgemeine Vergleich einschließlich des optionalen neutralen Sidecar-
Exports steht in [TAGSPACES_ANSAETZE.md](TAGSPACES_ANSAETZE.md).

## Maßgeblicher Standard

Maßgeblich ist
[RFC 5323 – Web Distributed Authoring and Versioning (WebDAV) SEARCH](https://www.rfc-editor.org/rfc/rfc5323.html).

| Normative Anforderung | Abschnitt | Implementierte Entscheidung |
|---|---|---|
| `SEARCH` ist eine sichere Methode und ein Erfolg verwendet `207 Multi-Status`. | [§2](https://www.rfc-editor.org/rfc/rfc5323.html#section-2), [§2.3](https://www.rfc-editor.org/rfc/rfc5323.html#section-2.3) | Die Methode ist read-only; jeder Treffer ist ein eigenes `DAV:response` mit `propstat`. |
| XML-Server müssen `application/xml` oder `text/xml` verarbeiten; `searchrequest` enthält genau eine Suchgrammatik. | [§2.2.2](https://www.rfc-editor.org/rfc/rfc5323.html#section-2.2.2) | Beide Medientypen werden angenommen. Fehlende, mehrfache oder unbekannte Grammatiken werden strukturiert abgewiesen. |
| `OPTIONS` muss unterstützte Grammatiken im `DASL`-Header melden. | [§3](https://www.rfc-editor.org/rfc/rfc5323.html#section-3) | `Allow` enthält `SEARCH`; `DASL` enthält `<DAV:basicsearch>`. |
| Die geschützte Live Property `supported-query-grammar-set` beschreibt die Grammatik. | [§3.3](https://www.rfc-editor.org/rfc/rfc5323.html#section-3.3) | `PROPFIND` kann `DAV:basicsearch` auf jeder hierarchischen Ressource ausdrücklich abfragen. |
| Ein DASL-Server muss `DAV:basicsearch` unterstützen. | [§5.1](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.1) | `select`, `from`, optional `where`, `orderby` und `limit` werden gemäß der Grundstruktur ausgewertet. |
| `select` wählt Ergebnis-Properties; unbekannte Properties müssen wie bei PROPFIND in einem passenden `propstat` erscheinen. | [§5.3](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.3) | `allprop` und explizites `prop` verwenden dieselbe Property-Erzeugung wie PROPFIND, einschließlich `404` für fehlende Properties. |
| Ein Scope besteht aus `href` und Tiefe `0`, `1` oder `infinity`; ein Server kann Mehrfach-Scope ablehnen. | [§5.4](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.4) | Genau ein Scope innerhalb von Benutzer-, Zugang- und Arbitergrenze ist zulässig. Dateien erzwingen Tiefe `0`. Mehrfach-Scope ergibt `422 search-multiple-scope-supported`, ungültiger Scope `409 search-scope-valid`. |
| Nicht vorhandene oder nicht vergleichbare Properties verwenden dreiwertige Logik mit UNKNOWN. | [§5.5](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.5) | AND/OR/NOT propagieren UNKNOWN; `NOT UNKNOWN` wird nicht fälschlich zu einem Treffer. Gemischtes Property-XML ist nicht still als Text vergleichbar. |
| `orderby` legt eine geordnete Antwort fest. | [§5.6](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.6) | Bis zu acht Sortierschlüssel mit auf-/absteigender und optional caseless Sortierung; gleiche Werte werden stabil über die URL geordnet. |
| Logische Operatoren, Vergleichsoperatoren und Literale bilden den verpflichtenden Kern. | [§5.7–§5.10](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.7) | AND, OR, NOT sowie EQ, LT, LTE, GT und GTE sind umgesetzt. Größe und Zeitstempel werden typisiert statt lexikalisch verglichen. |
| `is-collection` und `is-defined` prüfen Ressourcentyp und Property-Existenz. | [§5.13](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.13), [§5.14](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.14) | Beide Operatoren sind umgesetzt und funktionieren auch für Dead Properties. |
| `like` darf als optionaler Platzhaltervergleich angeboten werden; `%`, `_` und `\` haben definierte Bedeutung. | [§5.15](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.15) | `like` unterstützt die drei Zeichen einschließlich Escape-Prüfung und optionaler Unicode-caseless-Suche. |
| Ein Client kann die Ergebniszahl durch `limit/nresults` begrenzen. | [§5.17](https://www.rfc-editor.org/rfc/rfc5323.html#section-5.17) | Das Limit wird erst nach Filterung und Sortierung angewandt. |
| Ein Server darf Ergebnisse begrenzen; eine gekürzte Antwort sollte als solche erkennbar sein. | [§2.3.1](https://www.rfc-editor.org/rfc/rfc5323.html#section-2.3.1) | Bei Servergrenzen gibt es keine scheinbar vollständige Teilliste: `207` enthält für den Scope `507`, zusätzlich nennt `X-SimpleOffice-Search-Limit` den Grund. |
| Suchergebnisse dürfen keine Informationen offenbaren, die der Benutzer nicht anderweitig lesen dürfte; Server müssen Denial-of-Service-Risiken beachten. | [§7](https://www.rfc-editor.org/rfc/rfc5323.html#section-7) | Scope und jedes Mitglied bleiben im bereits autorisierten WebDAV-Baum. XML, Tiefe, Kandidaten, Operatoren, Sortierung, Treffer und Antwortgröße sind begrenzt; DTD und Entities werden abgewiesen. |

## Unterstützte Suchfelder und Operatoren

Suchbar sind die bereits angebotenen WebDAV Live Properties, insbesondere:

- `DAV:displayname`, `getcontentlength`, `getcontenttype`, `getetag`,
  `creationdate`, `getlastmodified`;
- `DAV:resourcetype`, Rechte-/Principal- und Lock-Discovery-Properties, soweit
  ein einfacher Vergleich fachlich sinnvoll ist;
- sämtliche für das Konto und die Ressource gespeicherten Dead Properties.

Die Operatoren `and`, `or`, `not`, `eq`, `lt`, `lte`, `gt`, `gte`, `like`,
`is-collection` und `is-defined` sind verfügbar. `caseless="yes"` arbeitet bei
Textwerten Unicode-basiert. `getcontentlength` wird numerisch verglichen;
`creationdate` und `getlastmodified` werden als UTC-Zeitwerte verglichen.

### Beispiel: Name enthält „rechnung“

```xml
<d:searchrequest xmlns:d="DAV:">
  <d:basicsearch>
    <d:select>
      <d:prop><d:displayname/><d:getetag/><d:getlastmodified/></d:prop>
    </d:select>
    <d:from>
      <d:scope><d:href>/webdav/files/jens</d:href><d:depth>infinity</d:depth></d:scope>
    </d:from>
    <d:where>
      <d:like caseless="yes">
        <d:prop><d:displayname/></d:prop><d:literal>%rechnung%</d:literal>
      </d:like>
    </d:where>
    <d:orderby>
      <d:order><d:prop><d:getlastmodified/></d:prop><d:descending/></d:order>
    </d:orderby>
    <d:limit><d:nresults>50</d:nresults></d:limit>
  </d:basicsearch>
</d:searchrequest>
```

Die Anfrage wird beispielsweise mit dem bestehenden App-Passwort gesendet:

```bash
curl --fail-with-body --user 'jens:APP-PASSWORT' \
  --request SEARCH --header 'Content-Type: application/xml' \
  --data-binary @suche.xml https://office.example/webdav/files/jens
```

## Tags und Metadaten

Ein schreibender Zugang kann nach der Anleitung in
[WEBDAV_EIGENSCHAFTEN_RFC4918.md](WEBDAV_EIGENSCHAFTEN_RFC4918.md) eine eigene
Dead Property wie `urn:example:tags:tag` setzen. Anschließend kann
`is-defined` oder `like` darauf suchen. SimpleOffice schreibt solche Tags
weder in den Dateinamen noch in ein sichtbares Sidecar. Dadurch bleiben
LibreOffice-Links und portable Pfade stabil. COPY und MOVE verwenden die
bereits dokumentierte Property- und Dokument-ID-Semantik.

## Bedienung und Desktop-Kompatibilität

- **LibreOffice:** Öffnen und Speichern funktionieren unverändert. LibreOffice
  nutzt die neue SEARCH-Methode nicht zwingend; Suchergebnisse können über ein
  Skript oder eine künftige SimpleOffice-Oberfläche ermittelt und dann über die
  normale WebDAV-URL geöffnet werden.
- **Nautilus/GNOME Files, Windows Explorer und Finder:** Einbinden, Lesen und
  Schreiben bleiben unverändert. Diese Dateimanager garantieren keine
  Weiterleitung ihrer Suchoberfläche als RFC-5323-SEARCH; die Serverfunktion
  ist deshalb eine zusätzliche, nicht vorausgesetzte Interoperabilität.
- **FreeFileSync:** Der Abgleich nutzt weiterhin das eingehängte WebDAV-Laufwerk
  und `PROPFIND`/Dateioperationen. SEARCH kann vorab in Automatisierungen eine
  Auswahl prüfen, ersetzt aber keinen vollständigen bidirektionalen Abgleich.
- **curl und andere DASL-Clients:** `OPTIONS`, der `DASL`-Header und
  `supported-query-grammar-set` erlauben echte Feature-Erkennung ohne
  Client-spezifische Annahmen.

## Rechte, Sicherheit und Datenschutz

- Suche verlangt ein gültiges, getrenntes WebDAV-App-Passwort. Ein reiner
  Lesezugang darf suchen, kann aber dadurch keine Property oder Datei ändern.
- Ein ordnergebundener Zugang kann nur innerhalb dieses Ordners suchen. Auch
  ein im XML genannter Wurzel-, Geschwister-, Fremdbenutzer- oder Fremdhost-
  Scope wird vor der Bestandsaufnahme abgewiesen.
- Interne `.simpleoffice`-/Historien-/Richtlinienpfade, Symlinks und
  Spezialdateien werden nicht als Kandidaten aufgenommen.
- Die Suche lädt und indexiert keine Dateiinhalte. Damit werden Office-Makros,
  Mail-Anhänge oder andere aktive Inhalte nicht ausgeführt.
- Anfrage und Antwort tragen `Cache-Control: private, no-store`; das Ergebnis
  variiert nach `Authorization`.
- Das Audit protokolliert Akteur, Scope, Tiefe, geprüfte und gefundene Anzahl,
  Operatorzahl, Clientlimit und Grenzfehler. Suchbegriffe, Literale und
  Treffernamen werden bewusst nicht gespeichert.
- HTTPS bleibt für Basic Authentication zwingende betriebliche Voraussetzung.

## Leistungs- und Ausfallverhalten

Eine Suchanfrage ist auf 64 KiB XML, 256 XML-Knoten, 64 Operatoren, 16
Ausdrucksebenen, acht Sortierschlüssel, 2.000 sichtbare Mitglieder, 64
Ordnerebenen, 500 Server-Treffer und 8 MiB Antwort-XML begrenzt. Dead
Properties werden einmal pro Anfrage geladen. Der sichtbare Kandidatenbestand
und seine Properties werden unter der WebDAV-Mutationssperre gelesen, sodass
ein paralleles MOVE oder DELETE keine widersprüchliche Teilliste erzeugt.

Relevante Fehler:

- `400`: ungültige XML-Struktur, Operatoranzahl oder Attribute;
- `409 search-scope-valid`: Scope fehlt, existiert nicht oder liegt außerhalb
  der zulässigen Grenze;
- `413`: XML, Knoten oder Literal sind zu groß;
- `415`: anderer Content-Type als `application/xml` oder `text/xml`;
- `422`: Grammatik, Mehrfach-Scope oder optionaler Operator wird nicht
  unterstützt;
- `207` mit enthaltenem `507`: Kandidaten-, Tiefen-, Treffer- oder
  Antwortgrenze verhindert eine verlässlich vollständige Antwort.

## Bewusst nicht implementiert und Interoperabilitätsgrenzen

- kein Volltextindex und kein `contains`; Dateiinhalte werden nicht gelesen;
- keine `language-defined`, `language-matches`, `typed-literal` oder
  `literal`-Typkonvertierung außerhalb Größe und Datum;
- keine Query Schema Discovery (`DAV:query-schema-discovery`);
- kein Mehrfach-Scope, keine serverseitig gespeicherten Abfragen und keine
  Cursor-/Paging-Erweiterung;
- keine automatische Dateifreigabe und kein ACL-Schreibzugriff;
- keine Behauptung, dass Finder, Explorer, Nautilus, LibreOffice oder
  FreeFileSync ihre jeweilige UI-Suche als RFC 5323 senden.

Nicht unterstützte optionale Operatoren werden mit `422` abgewiesen, statt
still andere Suchergebnisse zu liefern. Diese Teilmenge wird über
`DAV:basicsearch` beworben; die genauen Operatorgrenzen bleiben daher
dokumentationspflichtig.

## Migration, Rückwärtskompatibilität und Deaktivierung

Es gibt keine Datenbank- oder Dateimigration. Vorhandene Dateien, Properties,
Sidecars, App-Passwörter, ETags, Locks und Sync-Token bleiben unverändert.
Ältere Clients ignorieren das zusätzliche `SEARCH` in `Allow`, den `DASL`-
Header und die beiden Discovery-Properties.

Die Funktion ist mit dem WebDAV-Dateibaum aktiv und vollständig durch dessen
App-Passwörter geschützt. Zur vollständigen Deaktivierung werden wie bisher
alle WebDAV-Zugänge widerrufen oder der WebDAV-Blueprint entfernt. Eine
separate Abschaltung nur der sicheren, read-only SEARCH-Methode ist nicht
erforderlich; ein Reverse Proxy kann sie bei zwingender Altclient-
Kompatibilität blockieren, ohne bestehende Dateien zu verändern.

## Automatisierte Tests

Geprüft werden:

- OPTIONS-/Property-Discovery für Schreib- und Lesezugänge;
- caseless Namenssuche ohne Auslieferung von Dateiinhalten;
- Tags als Dead Properties, `is-defined`, AND und dreiwertiges NOT;
- numerische Größenfilter, absteigende Sortierung und Clientlimit;
- Benutzer-, Ordner-, Scope- und Authentifizierungsgrenzen;
- falscher Medientyp, ungültiges XML, Entities, Fremdhost, Mehrfach-Scope und
  nicht unterstützte Operatoren;
- Treffergrenze mit vollständigem `207`/`507` statt stiller Teilliste;
- Dateiscope mit erzwungener Tiefe 0 sowie Ausschluss interner Steuerdaten;
- Audit ohne Suchliteral und bestehende PROPFIND-/PUT-/COPY-/MOVE-/Lock-
  Regressionen.
