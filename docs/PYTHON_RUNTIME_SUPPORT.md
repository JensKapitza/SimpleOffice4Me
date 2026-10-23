# Python-Runtime-Support und Neuinstallation

Stand: 21. Oktober 2026.

**Unterstützt: CPython 3.11 bis 3.15 (jeweils finale, aktuelle Patchversion).**
Python 3.10 und ältere Versionen werden nicht mehr unterstützt. Das gilt auch
für Ubuntu 22.04 mit dessen System-Python 3.10. Es gibt keine Legacy-Ausnahme,
keine Sicherheitsfreigabe für ältere Pillow-Versionen und keine Migration.

## Installation unter Linux

Die Anwendung verwendet eine eigene virtuelle Umgebung. Python 3.12 ist eine
praktische Wahl; jede freigegebene Version 3.11–3.15 ist möglich.

```bash
git clone https://github.com/JensKapitza/SimpleOffice4Me.git
cd SimpleOffice4Me
PYTHON=python3.12 ./start.sh --check-system
PYTHON=python3.12 ./start.sh
```

Ist kein geeignetes System-Python vorhanden, bietet der vorhandene Starter
einen Standalone-Installer für Python 3.12:

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
python3.12 simpleoffice_runtime_support.py
./start.sh --check-system
```

Nur finale CPython-Versionen 3.11–3.15 sind freigegeben.
