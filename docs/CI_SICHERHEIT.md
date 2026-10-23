# Automatische Tests und Abhängigkeitsprüfung

SimpleOffice4Me prüft jeden Push und Pull Request automatisch. Die Prüfung
kompiliert alle Python-Module, führt die vollständige Unittest-Suite mit
Python 3.11, 3.14 und 3.15 final aus und kontrolliert installierte
Python-Pakete mit `pip-audit` auf bekannte Schwachstellen. Pull Requests führen
zusätzlich eine vollständige Python-3.15-Final-Abnahme aus; Vorabversionen
bleiben gesperrt. Python 3.10 wird nicht mehr unterstützt. Die
[Runtime-Policy](PYTHON_RUNTIME_SUPPORT.md) beschreibt die Mindestversion.

## Sicherheitsregeln

- Der Workflow besitzt ausschließlich Leserechte auf Repository-Inhalte.
- GitHub Actions sind auf vollständige Commit-SHAs festgelegt.
- Ein Lauf wird nach spätestens 15 Minuten beendet.
- Dependabot erstellt nur Pull Requests. Updates werden nicht automatisch
  zusammengeführt.
- Fehlgeschlagene Prüfungen müssen vor einem Release untersucht werden.

Die aktuell festgelegten Actions entsprechen `actions/checkout` 7.0.1 und
`actions/setup-python` 7.0.0. Die Versionskommentare dienen nur der Lesbarkeit;
ausgeführt wird immer der angegebene Commit-SHA.

## Lokal prüfen

```bash
python -m pip install --editable ".[security]"
python -m compileall -q app tools
python -m unittest discover -s tests -v
python -m pip_audit
```

Systemprogramme wie Tesseract, Poppler, restic oder rsync werden dadurch nicht
installiert. Tests müssen fehlende optionale Programme kontrolliert behandeln.
Für einen produktiven Betrieb bleiben zusätzlich getestete Sicherungen,
HTTPS, restriktive Dateirechte und die Prüfung der konkreten Systempakete
erforderlich.
