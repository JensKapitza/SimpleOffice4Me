# Python-Runtime-Support

Stand: 7. Oktober 2026. Ubuntu 22.04 bleibt ein offizielles, begrenztes Ziel:
`setup.py` erhält dessen Legacy-Editable-Pfad; die CI prüft jetzt ausdrücklich
Canonicals Systeminterpreter auf `ubuntu-22.04` statt Upstream-3.10 auf 24.04.

| Runtime | Vertrag |
| --- | --- |
| CPython 3.11–3.14 final | Produktionsbetrieb; aktuelle Sicherheits-Patchversion verwenden. CI: 3.11 und 3.14. |
| CPython 3.10 auf Ubuntu 22.04 | Ausschließlich unverändertes Canonical-Paket `python3.10-minimal`, einschließlich daraus erzeugter venvs, bis einschließlich 31.05.2027. Sicherheitsquellen müssen aktiviert und Updates installiert sein. |
| Upstream-/PPA-/selbst gebautes 3.10, andere Distributionen | Nicht unterstützt; Installation aus Source und Anwendung/Starter brechen mit Diagnose ab. |
| 3.15.0rc3 | Separate Vorabkompatibilität, nur mit `SIMPLEOFFICE_ALLOW_PRERELEASE=1` für Tests. Keine Produktionsfreigabe. |
| 3.15 final und spätere Versionen | Bis zur gesonderten Abnahme nicht freigegeben. Final-Releasedatum allein schaltet nichts automatisch frei. |

Python 3.10.22 war am 1. Oktober 2026 die letzte Upstream-Version:
[Python.org](https://www.python.org/downloads/release/python-31022/).
Canonicals [Release-Zyklus](https://ubuntu.com/about/release-cycle) weist für
22.04 Standard-Sicherheitswartung bis Mai 2027 aus. Unsere Ausnahme endet daher
am 31. Mai; Ubuntu Pro/ESM verlängert diesen Projektvertrag nicht automatisch.
Die [Ubuntu-Sicherheitsquellen](https://documentation.ubuntu.com/security/security-updates/)
und aktuelle Pakete sind Betreiberpflicht. Backports ändern nicht zwingend die
Upstream-Patchnummer: `3.10.22` ist deshalb keine sinnvolle Jammy-Mindestprüfung.

## Technische Durchsetzung

`simpleoffice_runtime_support.py` ist die gemeinsame, unabhängige Prüfung für
Source-/Editable-Builds (`setup.py`, auch PEP 517), Web-/SFTP-Starter,
Systempaket-Build/-Installation und den Import der Anwendung. Direkte Starts
von Netzwerk-Worker, Firewall-Agent und HTTPS-CONNECT-Tunnel sowie der
Launcher prüfen dieselbe Policy vor Listenern oder Konfigurationsänderungen.
Stop/Status und Firewall-Rollback bleiben zur Wiederherstellung zugänglich. Eine vorhandene
venv hat beim Start und bei `--check-system` Vorrang; der Bootstrap-Interpreter
wird nur für eine neue bzw. bewusst neu erzeugte Umgebung benötigt. Eine
abgewiesene venv wird bei einer Supportverletzung nicht gelöscht, auch unter
Termux. `--check-system` bleibt ohne Systemänderungen.
Die 3.10-Erkennung prüft Ubuntu-ID/Version, den aufgelösten Basisinterpreter
`/usr/bin/python3.10`, Paketbesitz sowie Provenienz, aktuellen APT-Kandidaten und `dpkg --verify` für
`python3.10-minimal`, `python3.10`, `libpython3.10-minimal` und
`libpython3.10-stdlib`. Fehlende oder fehlerhafte Nachweise werden abgewiesen.
Die lokalen APT-Indizes müssen vorhanden sein und die installierte Version
mit Release-Origin/Label `Ubuntu`, Codename `jammy`, Komponente `main` und
Suite `jammy`, `jammy-updates` oder `jammy-security` führen. Offizielle und
interne Mirrors einschließlich `mirror+file:` werden über diese Release-
Metadaten erkannt. Beide Update-Suites `jammy-updates` und `jammy-security` müssen in den lokalen
Indizes vorhanden sein; jedes installierte Runtime-Paket muss dem aktuellen
APT-Kandidaten entsprechen. Zusätzlich darf keine neuere offizielle Version
in den Indizes verfügbar sein, auch wenn APT-Pinning den Kandidaten festhält;
der Vergleich verwendet Debians Versionssemantik (`dpkg --compare-versions`).
Veraltete Pakete, PPA-Origin oder gelöschte Indizes
werden abgewiesen, ebenso identische Versionen aus zusätzlichen Fremdquellen.
Vor Installation bzw. nach Updates `sudo apt-get update` und
die verfügbaren Runtime-Updates installieren. Zusätzlich lädt die Laufzeitprüfung die ausgewählte, über APT authentisierte
Canonical-Paketdatei in ein temporäres Verzeichnis und vergleicht deren
Dateiinhalte und Symlink-Ziele mit der installierten Runtime. Damit reicht ein
identischer Versionsstring eines früher installierten Fremdpakets nicht aus.
Für die Jammy-Ausnahme muss der konfigurierte Ubuntu-Mirror daher während der
Prüfung erreichbar sein; schlägt der Nachweis fehl, wird 3.10 fail-closed
abgewiesen. Das prüft Paketidentität, Integrität und den lokalen Update-Stand;
die Aktualität der APT-Indizes bleibt Betreiberpflicht.

`requires-python >=3.10` in beiden Metadaten bleibt die **Syntaxgrenze** für
die Jammy-Ausnahme. Python-Paketmarker können weder Distribution,
Paketprovenienz noch Wartungsdatum ausdrücken. Die Produktionsfreigabe steht
daher in dieser Policy und der gemeinsamen Laufzeitprüfung. Die Classifier
nennen nur 3.11–3.14. Eine fertige Wheel-Datei kann technisch unter anderen
Interpretern installiert werden; die Anwendung verweigert trotzdem den Start.

```bash
python simpleoffice_runtime_support.py
./start.sh --check-system
```

Außerhalb der Ausnahme eine freigegebene Runtime auswählen, zum Beispiel
`PYTHON=python3.12 ./start.sh`. Eine bestehende venv mit nicht mehr unterstütztem
Interpreter nach Sicherung durch eine venv der freigegebenen Runtime ersetzen;
Dokumente, `instance` und Datenbanken dabei erhalten. Die Starter installieren
keine Fremd-PPA und ersetzen keine System-Runtime durch ungewartetes 3.10.

## Python 3.15

[RC3 ist veröffentlicht](https://www.python.org/downloads/release/python-3150rc3/);
3.15.0 final ist derzeit für den 9. Oktober 2026 vorgesehen. Die vorhandene
RC3-Matrix führt Installation, Policy, Compile und die vollständige Suite
separat aus. Der Testschalter gibt ausschließlich 3.15-Vorabversionen frei;
er umgeht weder 3.10-Provenienz noch die Sperre für ungeprüfte Final-Versionen.
Nach tatsächlichem Final-Release folgt die Abnahme in
[#474](https://github.com/JensKapitza/SimpleOffice4Me/issues/474): Dependency- und
Security-Gates, Installer und vollständige Suite; erst danach Policy und
Classifier ändern. Der laufende Tracker bleibt offen.
