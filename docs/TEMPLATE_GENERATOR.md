# In-App Template-Generator

## Zielbild

SimpleOffice4Me erhält zusätzlich zum bestehenden Upload von dreiseitigen PDF-/Office-Hintergründen einen visuellen Template-Generator. Der neue Editor speichert strukturierte A4-Layouts als JSON und bleibt bis zur produktiven Rendering-Anbindung vollständig kompatibel mit dem bestehenden Business-Dokument-System.

## Architektur

### Bestehender Legacy-Pfad

- `app/business_document_generation.py`
  - dreiseitige Corporate-PDF-/Office-Vorlagen
  - PDF-Zusammenführung
  - Rechnungs-/Brief-Rendering
  - ZUGFeRD/PDF-A-Pipeline
- `templates/documents/business_templates.html`
  - Upload und Aktivierung
  - Rechnungssteller-/Bank-/Steuer-Stammdaten
- `.simpleoffice/business-templates/templates.json`
  - Index der hochgeladenen Hintergründe

Dieser Pfad bleibt unverändert produktiv.

### Neuer visueller Pfad

- `app/business_template_editor.py`
  - Schema und Validierung
  - A4-Basislayouts
  - atomare Persistenz
  - Audit-Ereignisse
  - Editor-Routen
- `templates/documents/business_template_overview.html`
  - Übersicht und Neuanlage
- `templates/documents/business_template_editor.html`
  - visueller Editor
- `static/js/business-template-editor.js`
  - Auswahl, Drag, Resize, Format, Token-Vorschau
- `static/css/business-template-editor.css`
  - A4-Canvas und farbige Editorbereiche
- `.simpleoffice/business-templates/visual/visual-templates.json`
  - Index visueller Vorlagen
- `.simpleoffice/business-templates/visual/<uuid>.json`
  - Layout einer Vorlage

## HTTP-Routen

Unter dem bestehenden Blueprint `/documents/business`:

| Methode | Route | Zweck |
| --- | --- | --- |
| GET | `/templates/editor` | visuelle Vorlagenübersicht |
| POST | `/templates/editor/new` | Basis-Layout anlegen, Admin |
| GET | `/templates/editor/<id>` | Editor öffnen |
| POST | `/templates/editor/<id>` | Layout speichern, Admin |
| POST | `/templates/editor/<id>/duplicate` | Vorlage duplizieren, Admin |

## Layoutschema v1

A4 wird intern in Millimetern modelliert.

```json
{
  "schema_version": 1,
  "page": {"format": "A4", "width_mm": 210, "height_mm": 297},
  "regions": {
    "header": {"enabled": true},
    "content": {"enabled": true},
    "footer": {"enabled": true},
    "margin_left": {"enabled": false},
    "margin_right": {"enabled": false}
  },
  "free_text": {
    "freitext1": "",
    "freitext2": "",
    "freitext3": ""
  },
  "elements": []
}
```

### Element

- `id`
- `type`: `text`, `token`, `line`, `rect`
- `region`: `header`, `content`, `footer`, `margin_left`, `margin_right`
- `x_mm`, `y_mm`, `width_mm`, `height_mm`
- `text`
- `style.font_size`
- `style.font_family`
- `style.bold`, `italic`, `underline`
- `style.color`
- `style.align`
- `z_index`
- `locked`
- `visible`

Serverseitig werden A4-Grenzen, Elementanzahl, Textlängen, Farben, Schriften und Ausrichtungen validiert.

## Standardtokens

- `{{firma.name}}`
- `{{firma.anschrift}}`
- `{{firma.ansprechpartner}}`
- `{{firma.email}}`
- `{{firma.iban}}`
- `{{firma.bic}}`
- `{{firma.bank}}`
- `{{firma.steuer_nr}}`
- `{{firma.ust_idnr}}`
- `{{rechnung.nummer}}`
- `{{rechnung.datum}}`
- `{{rechnung.zahlungsziel}}`
- `{{kunde.name}}`
- `{{kunde.anschrift}}`
- `{{datum.heute}}`
- `{{freitext1}}`, `{{freitext2}}`, `{{freitext3}}`

Die Editor-Vorschau und der PDF-Renderer lösen ausschließlich bekannte Tokens und die whitelist-basierte Formatsprache auf. Es wird kein HTML, Python, JavaScript oder Shell-Code ausgeführt.

## Editorbedienung

- Elemente aus der linken Palette hinzufügen
- Element auf A4 auswählen
- Drag zum Verschieben
- Resize über den Griff unten rechts
- X/Y/B/H alternativ numerisch in mm
- B/I/U
- Schriftgröße
- Farbe
- Ausrichtung
- Region zuweisen
- Position sperren
- Element duplizieren/löschen
- Header/Footer/Randbereiche ein-/ausblenden
- drei Freitext-Vorgaben
- Token-Vorschau mit Firmen-Testdaten
- Duplizieren einer ganzen Vorlage

Die farbigen Bereiche dienen nur als Editorhilfe und werden nicht Bestandteil eines späteren PDF-Exports.

## Implementierter Funktionsumfang im PR-Stack

### #1003 Makrosprache

Implementiert:

- `[B]...[/B]`, `[I]...[/I]`, `[U]...[/U]`
- `[SIZE=6..72]`
- `[COLOR=#RRGGBB]`
- `[ALIGN=left|center|right|justify]`
- verschachtelte Tokenauflösung mit Zyklus-/Tiefenlimit
- identische sichere Vorschau ohne `innerHTML`

### #1004 Rendering

Implementiert:

- gemeinsamer A4/mm-Renderer mit ReportLab
- dynamische Rechnungspositionen und Summen
- automatische Mehrseitenaufteilung
- Dokumenttext für Brief/Angebot/Lieferschein
- PDF-Vorschau aus dem Editor
- Auswahl visueller Vorlagen im bestehenden Rechnungs- und Briefworkflow
- Legacy-Dispatcher; PDF/A/ZUGFeRD bleiben hinter dem visuellen Renderer erhalten

### #1005 Editor UX

Implementiert:

- Drag/Resize plus numerische mm-Eingabe
- Snap-Raster aus/1/2/5 mm
- Undo/Redo (50 Zustände)
- Ebenensteuerung
- Sichtbarkeit und Positionssperre
- Pfeiltasten, Shift+Pfeil, Entf, Strg/Cmd+Z/Y
- fokussierbare Canvas-Elemente

### #1006 Verwaltung und Assets

Implementiert:

- versionierte Snapshots und Restore als neue Version
- aktive visuelle Vorlage je Dokumenttyp
- JSON-Export/Import mit Schema-Version
- Audit-Ereignisse
- sichere PNG/JPEG-Logo-/Bildassets
- Bilddekodierung/Normalisierung mit Pillow
- 750-KB-Einzellimit und 3-MB-Gesamtlimit
- Bildassets werden bei Duplikat sowie Export/Import mitgeführt

## Kompatibilitätsregel

Visuelle Vorlagen und dreiseitige Corporate-Hintergründe werden bis zur vollständigen Rendering-Migration getrennt gespeichert. Dadurch kann eine visuelle Vorlage den aktuellen Rechnungsrenderer nicht versehentlich als inkompatiblen Hintergrund erreichen.
