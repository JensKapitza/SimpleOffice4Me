# Python-Runtime-Support und Neuinstallation

Stand: 2. November 2026.

**Stabile Standardversion für Builds und Neuinstallationen: CPython 3.14.**
Im Serverbetrieb werden finale CPython-Versionen 3.11 bis 3.14 unterstützt.
Python 3.15 ist experimentell: Es ist kein Release-Ziel und seine CI-Prüfung
ist nicht blockierend. Python 3.10 und ältere Versionen werden nicht
unterstützt, auch nicht das Ubuntu-22.04-System-Python 3.10.

## Installation unter Linux

Die Anwendung verwendet eine eigene virtuelle Umgebung. Der Starter bevorzugt
CPython 3.14; für bestehende Installationen bleiben die Versionen 3.11 bis 3.14
kompatibel.

```bash
git clone https://github.com/JensKapitza/SimpleOffice4Me.git
cd SimpleOffice4Me
PYTHON=python3.14 ./start.sh --check-system
PYTHON=python3.14 ./start.sh
```

Ist kein geeignetes System-Python vorhanden, bietet der vorhandene Starter
einen Standalone-Installer für Python 3.14:

```bash
./start.sh --standalone
```

Dieser Installer lädt die Runtime unabhängig vom System-Python herunter.
Vor dem produktiven Einsatz die vom Starter angezeigten Abhängigkeiten und
Systemvoraussetzungen prüfen. Auf Ubuntu 22.04 **nicht** auf das dortige
Python 3.10 zurückfallen.

## Neuinstallation statt Migration

Alte Python-3.10-Umgebungen werden nicht übernommen. Bei einer bestehenden
Installation eine Datensicherung anlegen, eine frische Installation mit
unterstützter Runtime durchführen und nur benötigte Dokumente und Daten
anschließend gesondert einrichten. Es gibt keinen automatischen
Migrationspfad und keine Unterstützung für alte Python-Umgebungen.

## Android

Die Android-App wird als installierbare APK über GitHub Actions
`Android APK build` bereitgestellt. Die Python-Runtime wird in der APK
gebündelt; Nutzer installieren **kein** separates Python auf Android.
Der geprüfte ARM64-Build verwendet Pillow 13.0.0 und hash-verifizierte native
Wheels für Pillow, cryptography, cffi und argon2-cffi-bindings. CI baut die APK,
vergleicht die tatsächlich enthaltenen Python-Pakete mit den Gradle-Pins und
validiert die Android-SBOMs. ARM32 wird mangels verifizierter Pillow-13-Wheels
nicht angeboten. Der CI-Nachweis ersetzt keinen Laufzeit-/Gerätetest.

## Prüfung

```bash
python3.14 simpleoffice_runtime_support.py
./start.sh --check-system
```

Für produktive Installationen sind finale CPython-Versionen 3.11–3.14
freigegeben; neue Builds verwenden 3.14. Python 3.15 bleibt experimentell.

## Virtuelle Umgebung beim Start auswählen (Linux, macOS, Windows)

`start.sh` erkennt Linux (einschließlich Termux), macOS und Windows unter
**Git Bash/MSYS2/Cygwin**. Auf Windows ist hierfür eine Bash-Umgebung nötig;
die Datei ist kein natives PowerShell-Skript.

```bash
# Standard: vorhandene .venv unmittelbar starten, sonst einrichten
bash start.sh

# Eigene Umgebung: neu erstellen oder bestehende wiederverwenden
bash start.sh --venv .venv314
bash start.sh --venv /pfad/zur/umgebung

# Nur einrichten/aktualisieren, noch nicht starten
bash start.sh setup --venv .venv314

# Nur starten, ohne Installation oder Reparatur
bash start.sh run --venv .venv314
```

Relative venv-Pfade beziehen sich immer auf das Repository. Das Skript
löscht eine vorhandene Umgebung nicht automatisch. Bei inkompatiblen
Python-Versionen besser eine **neue** venv angeben. Der Einrichtungsmodus
installiert die Python-Abhängigkeiten über pip (Linux mit Standard-venv
kann weiterhin native Distributionspakete nutzen). Für neue Umgebungen wird
eine verfügbare stabile CPython-Version bevorzugt, insbesondere Python 3.14.
Eine explizite Interpreterwahl ist mit `PYTHON=/pfad/python3.14` möglich.

Unter Windows muss die Git-Bash-Installation eine aufrufbare Python-Laufzeit
(oder den `py`-Launcher) bereitstellen. Der Windows/macOS-Pfad verwaltet
aktuell die Python-Anwendung; optionale native Komponenten und Browser-
Export-Abhängigkeiten können separat eingerichtet werden. Der
`--standalone`-Installer bleibt auf Linux beschränkt.
