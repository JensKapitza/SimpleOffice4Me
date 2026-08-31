# UI-Quick-Wins

Die HTML-Oberfläche besitzt projektweit einheitliche Grundregeln für Bedienbarkeit
und Barrierefreiheit. Die Änderungen sind bewusst klein und ändern keine fachlichen
Abläufe.

## Umgesetzte Verbesserungen

| Bereich | vorher | nachher | zusätzliche Stellen |
| --- | ---: | ---: | ---: |
| Sprungziel für den Hauptinhalt | 3 | 70 | 67 |
| Expliziter Button-Typ | 42 | 235 | 193 |
| Tabellenkopf mit `scope` | 7 | 177 | 170 |
| Suchfelder mit `type="search"` | 2 | 13 | 11 |
| Live-Status für Informationsmeldungen | 1 | 53 | 52 |
| Suchformular als Landmarke | 2 | 13 | 11 |
| Einklappschalter mit ARIA-Zustand und Ziel | 2 | 6 | 4 |
| Verzögertes und asynchrones Laden von Bildern | 0 | 1 | 1 |

Damit wurden mehr als 500 konkrete HTML-Elemente verbessert. Zusätzlich bleiben
alle Adress-Autocomplete-Felder gegen konkurrierendes Browser-Autofill geschützt.

## Abgesicherte Regeln

`tests/test_template_accessibility.py` prüft bei jeder CI-Ausführung:

- jedes `main` ist das Ziel des Skip-Links;
- jeder Button benennt sein Standardverhalten;
- Tabellenköpfe unterscheiden Zeilen und Spalten;
- Informationsmeldungen werden von Hilfstechnologien angekündigt;
- Adress-Autocomplete wird nicht von Browser-Autofill überlagert;
- Such-Landmarken enthalten ausschließlich echte Suchformulare;
- einklappbare Bereiche veröffentlichen Zustand und Ziel.
