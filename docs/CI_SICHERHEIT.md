# Automatische Tests und Abhängigkeitsprüfung

GitHub Actions prüft jeden Push auf `main` und jeden Pull Request:

- Installation auf der ältesten unterstützten Python-Version 3.10,
- Installation auf Python 3.14,
- Kompilierung aller Python-Module,
- vollständige Unittest-Suite,
- bekannte Schwachstellen der installierten Python-Abhängigkeiten mit
  `pip-audit`.

Der Workflow besitzt nur Leserechte auf Repository-Inhalte. Externe Actions
sind auf vollständige Commit-SHAs festgelegt, damit ein nachträglich
verschobener Versions-Tag keinen anderen Code einschleusen kann.

Dependabot prüft montags Python- und GitHub-Actions-Abhängigkeiten. Verfügbare
Aktualisierungen werden gruppiert, damit nicht für jedes Paket ein eigener
Pull Request entsteht. Änderungen werden nicht automatisch zusammengeführt.

## Lokale Prüfung

```bash
python -m pip install --editable ".[security]"
python -m compileall -q app tools
python -m unittest discover -s tests -v
python -m pip_audit
```

Fehlende optionale Systemprogramme wie Tesseract dürfen in den Tests nicht
stillschweigend durch echte Dokumentdaten ersetzt werden. OCR-Tests verwenden
Mocks bzw. überspringen klar dokumentiert, wenn eine Systemkomponente nicht
Teil des Testfalls ist.
