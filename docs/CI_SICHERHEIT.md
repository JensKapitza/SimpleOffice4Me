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

Externe GitHub Actions werden mit vollständigen Commit-SHAs festgelegt; ein
Versionskommentar dokumentiert den zugehörigen Upstream-Tag. Ein Regressionstest
prüft alle Workflow-Dateien auf veränderliche Tags oder Branch-Referenzen.
Action-Updates müssen den geprüften SHA und den Kommentar gemeinsam aktualisieren.

Die CI-SBOM-Helfer `packaging` und `defusedxml` werden aus
`requirements-ci-tools.txt` mit SHA-256-Hashes installiert. Android-Workflows
verwenden zusätzlich `requirements-ci-android-wheels.txt` für pip, cibuildwheel
und wheel sowie `requirements-ci-android-apk.txt` für Pillow. Die `.in`-Dateien
sind die lesbaren Eingaben zur Hash-Lock-Erzeugung. Das pip-Bootstrap-Upgrade
in CI wird aus `requirements-ci-pip.txt` mit vollständigen PyPI-Hashes installiert;
damit ist auch der Installer selbst reproduzierbar gepinnt. Ruff in den
Qualitätsprüfungen wird aus `requirements-ci-ruff.txt` mit SHA-256-Hashes
installiert. Browser-Testabhängigkeiten werden für beide Ubuntu-x86_64-
CPython-3.12-CI-Verbraucher aus `requirements-ci-browser.txt` mit SHA-256-Hashes
und ausschließlich als geprüfte Wheels installiert. Dieser Lock ist absichtlich
auf dieses Runner-Ziel beschränkt; Updates müssen passende PyPI-Wheel-Hashes
eintragen und den pip-Download mit `--require-hashes --only-binary=:all:` sowie
den Workflow-Target-Parametern verifizieren. Andere Runner dürfen den Lock erst
nach passender Wheel-/Hash-Prüfung verwenden. Alle Lock-Updates müssen PyPI-
Artefakthashes, `.in`-Eingabedateien und Workflow-Regressionstests gemeinsam
aktualisieren. Die Security-Quick-Wins-Prüfung installiert `pip-audit` und
`bandit` aus `requirements-ci-security-tools.txt` mit SHA-256-Hashes und erlaubt
ausschließlich Wheels. Die lesbare `.in`-Datei hält die gewünschten Scanner-
Versionen fest; `typing-extensions` ist zusätzlich explizit gepinnt, da der
Python-3.12-CI-Runner es als CycloneDX-Abhängigkeit benötigt. Bei Updates sind
Lock, Workflow und Regressionstest gemeinsam zu prüfen. Der optionale
AWS-S3-Kompatibilitätstest installiert AWS CLI aus
`requirements-ci-awscli.txt`; dessen Version 1.45.28 enthält die bekannten
Credential-Datei- und EMR-SSH-Hostkey-Korrekturen für AWS CLI v1. AWS CLI v1
befindet sich im Maintenance-Modus und bleibt hier nur für den optionalen Smoke
gegen den isolierten lokalen S3-Endpoint. Versionsupdates müssen alle Workflows,
die sie installieren, durch ihre Pull-Request-Pfadfilter neu anlaufen lassen.
Die generischen
Lock-Dateien werden mit pip-tools 7.6.2 und
`pip-compile --generate-hashes --allow-unsafe --strip-extras` aktualisiert; der
Browser-Lock wird gezielt für sein dokumentiertes Ubuntu-x86_64/CPython-3.12-
Target gepflegt und mit dem unten stehenden pip-Download geprüft.

## Lokal prüfen

```bash
python -m pip download --no-deps --require-hashes --only-binary=:all: \
  --platform manylinux_2_17_x86_64 \
  --platform manylinux_2_24_x86_64 \
  --platform manylinux_2_28_x86_64 \
  --platform manylinux1_x86_64 \
  --implementation cp --python-version 3.12 --abi cp312 \
  --dest /tmp/ci-browser-wheel-check \
  -r requirements-ci-browser.txt
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
