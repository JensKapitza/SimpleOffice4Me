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
