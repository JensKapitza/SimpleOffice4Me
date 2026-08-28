# Fristen, Aussonderung und verwaltete Objekte

## Sicherheitsregeln

SimpleOffice4Me unterscheidet zwei Fristarten:

- `work`: Sobald eine zutreffende Frist abgelaufen ist, sind normale
  Änderungen am Dokument gesperrt. Das Hinzufügen einer weiteren Frist und
  die ausdrücklich gestartete Aussonderung bleiben möglich.
- `retention`: Ein Dokument ist erst aussonderbar, wenn alle eigenen,
  geerbten und transitiv verbundenen Aufbewahrungsfristen abgelaufen sind.
  Das späteste Ende gewinnt.

Fehlt einem Dokument der Verknüpfungsgruppe eine auswertbare
Aufbewahrungsfrist oder enthält eine Regel einen Fehler, bleibt die gesamte
Gruppe gesperrt. Die Anwendung löscht keine Dokumentdateien. Der manuelle
Aufräumlauf zeigt zuerst eine Vorschau und verschiebt nach der Eingabe
`AUSSONDERN` nur in einen Ordner innerhalb des Dokumentenspeichers.

## Frist am Dokument

In der Dokumentansicht lassen sich beliebig viele Fristen mit Art, Datum und
Begründung ergänzen. Frühere Fristen werden nicht überschrieben. Alternativ:

```bash
python -m flask --app app document-deadline DOKUMENT 2034-12-31 \
  --kind retention --label "Steuerunterlagen" --user jens

python -m flask --app app retention-status DOKUMENT
```

## Geerbte Ordner- und Tagregeln

Auf der Seite **Fristen** können Ordnerregeln über ein Formular angelegt,
aufgelistet und gezielt entfernt werden. Dafür werden Ordner, Bezeichnung,
Wirkung und optional ein Tagmuster angegeben. Als Laufzeit ist entweder eine
Anzahl Jahre oder ein festes Ablaufdatum zulässig. Jede Änderung wird mit
Benutzer und Zeitpunkt protokolliert.

Technisch stehen die Regeln weiterhin transparent im Abschnitt
`retention.rules` einer `.simpleoffice-folder.json`. Sämtliche Regeln vom
Wurzelordner bis zum Dokumentordner werden berücksichtigt. Eine absolute
Ordnerfrist sieht so aus:

```json
{
  "id": "projekt-ende",
  "kind": "retention",
  "expires_at": "2032-12-31",
  "label": "Projektunterlagen"
}
```

Eine Tagregel kann ab dem Zeitpunkt laufen, zu dem das Tag erstmals gesetzt
wurde:

```json
{
  "id": "steuer-2026",
  "kind": "retention",
  "tag": "steuer*",
  "years": 8,
  "label": "Steuerunterlagen acht Jahre"
}
```

Das Tagmuster ist ohne Beachtung der Großschreibung auswertbar und unterstützt
`*`. Der erstmalige Setzzeitpunkt bleibt in `tagged_at` erhalten. Jede Anzeige
nennt Datum, Begründung, Quelle, Ordner beziehungsweise Tag und den Status.

## Transitive Verknüpfungen

Beim Verbinden zweier Dokumente kann „Aufbewahrungsfristen transitiv
verbinden“ aktiviert werden. Die Kante erhält dann
`propagates_retention: true`. Die Anwendung behandelt den erreichbaren Graphen
zyklensicher als eine Aufbewahrungsgruppe. Das späteste Fristende aller
Dokumente gilt für jedes Mitglied; eine fehlende Frist blockiert die Gruppe.

## Manueller Aufräumlauf

Die Seite **Fristen** zeigt fehlende Fristen und aussonderbare Dokumente. Der
Aufräumlauf verschiebt Originale und protokolliert Benutzer, Zeitpunkt,
SHA-256, alten und neuen Pfad. Eine Kommandozeilenvorschau verändert nichts:

```bash
python -m flask --app app retention-cleanup --user jens
```

Erst die bewusste Bestätigung verschiebt:

```bash
python -m flask --app app retention-cleanup --user jens \
  --destination Aussonderung --apply --confirm AUSSONDERN
```

Das spätere physische Löschen bleibt eine getrennte manuelle Entscheidung und
ist in dieser Ausbaustufe nicht Bestandteil der Anwendung.

## Allgemeine Objekte

Der Reiter **Objekte** verwaltet reale und virtuelle Objekte, beispielsweise
Geräte, Fahrzeuge, Räume, Softwarelizenzen, Domains, virtuelle Maschinen oder
frei benannte Marker. Ein Objekt erhält eine stabile UUID sowie Name, frei
definierbaren Typ, Status, Kennung, Ort, Ablaufdatum, Tags, Beschreibung,
Notizen und beliebige Schlüssel-Wert-Felder. Dokumente werden über ihre stabile
Dokument-ID verbunden; das Lösen einer Verbindung verändert die Datei nicht.

Jede Anlage, Änderung, Notiz und Dokumentverknüpfung wird mit Benutzer und
Zeitpunkt in der Revisionshistorie festgehalten. Die Objekte liegen einzeln als
JSON-Dateien unter `.simpleoffice-meta/objects/`, sodass parallele Änderungen
an unterschiedlichen Objekten nicht dieselbe Datendatei überschreiben.

Historische oder teilweise migrierte JSON-Datensätze erhalten beim Lesen ein
vollständiges, aktuelles Objektmodell mit sicheren Standardwerten für Listen,
freie Felder, Notizen, Dokumentverknüpfungen und Rechnungsdaten. Eine reine
Listen- oder Detailansicht schreibt die Quelldatei dabei nicht um. Dadurch
bleiben alte Daten nachvollziehbar und `/documents/objects` kann auch
unvollständige Altbestände darstellen. Tritt künftig dennoch ein Jinja-Fehler
auf, enthält das Fehlerlog Request-ID, Endpoint, Template, Zeile und die
fehlende Variable.

Das Ablaufdatum eines Objekts ist zunächst ein sichtbares Stammdatenfeld. Eine
spätere Ausbaustufe kann es als zusätzliche Fristquelle in den bestehenden
Fristenkern einspeisen. Bis dahin verändert ein Objektablauf ausdrücklich
keine Dokument-Aufbewahrungsregel.
