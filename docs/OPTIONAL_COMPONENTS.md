# Optionale lokale Komponenten

Die Seite **Lokale Extras** zeigt erkannte Systemprogramme und verwaltete
Komponenten. Eine verwaltete Installation verwendet das private
`.simpleoffice-meta/components`-Verzeichnis im Anwendungsdatenbereich. Eine
vorhandene Systeminstallation wird bevorzugt und nicht ersetzt. Downloads
starten nur nach einer Administratoraktion; Dokumente, E-Mails, Fotos,
Sprachaufnahmen oder OCR-Inhalte werden dabei nicht übertragen.

## Geprüfte automatische Installer

| Komponente | Unterstützte Laufzeit | Verhalten |
| --- | --- | --- |
| Piper | Linux x86_64, glibc ≥2.28, CPython 3.12 | Runtime und Stimme sind getrennte Installationen. |
| FFmpeg, FFprobe, FFplay | Linux x86_64, glibc ≥2.28 | Festgelegtes Archiv mit SHA-256-Prüfung; bestehende Systemprogramme haben Vorrang. |
| RapidOCR / ONNX Runtime | Linux x86_64, glibc ≥2.28, CPython 3.12 | Isolierte private Python-Runtime; Modelle sind im verifizierten RapidOCR-Wheel enthalten. |

Die verwalteten Installer verwenden festgelegte Versionen, Größen und
SHA-256-Werte. Sie erkennen fehlenden Speicher, erlauben Abbruch und führen
vor der Freigabe einen Funktionstest aus. Unterstützte Installer werden nicht
automatisch auf andere Betriebssysteme oder Architekturen ausgeweitet.

## OCR-Installer

Die OCR-Laufzeit enthält RapidOCR 3.10.0, ONNX Runtime 1.32.0 und deren
vollständigen Satz festgelegter Linux-x86_64-Wheels. Zusammen sind es rund
147 MB Download und mindestens 800 MiB freier Speicher für Installation und
Prüfung. Die Modelle `PP-OCRv6_det_small.onnx`,
`PP-OCRv6_rec_small.onnx` und `ch_ppocr_mobile_v2.0_cls_mobile.onnx` sind im
RapidOCR-Wheel enthalten. Die Installerprüfung erzeugt ein lokales Testbild
und verlangt erkannte Zeichen, bevor die Runtime aktiviert wird.

Der Server hält bei Bedarf einen privaten OCR-Prozess mit geladenem Modell
offen, damit wiederholte Dokumentseiten nicht bei jedem Aufruf das Modell neu
initialisieren. An diesen Prozess wird nur ein lokaler Bildpfad über eine
private Pipe übergeben. Ergebnistext und Textblöcke kehren an den aufrufenden
OCR-Code zurück. Das Modell wird nicht während der Erkennung aus dem Netz
geladen. ONNX-Telemetrie ist für den verwalteten Prozess deaktiviert.
Die längste Modellkante ist auf 3.508 Pixel begrenzt, wie bei der bestehenden
RapidOCR-Integration. Die Expertenansicht führt Lizenzkennung, exakte Wheel-
Datei, Prüfsumme und PyPI-Dateiquelle einzeln auf; die mit den Wheels
gelieferten Lizenz- und Attribution-Dateien bleiben in der privaten Runtime.

Systemweit installierte RapidOCR-/ONNX-Pakete werden weiterhin verwendet,
wenn beide Pakete und alle drei Modelle in der Server-Python-Umgebung
verfügbar sind. Fehlen Hostmodelle, bietet die Komponentenkarte die verwaltete
Runtime an und der Aufrufpfad verwendet sie, sofern sie installiert ist. Auf anderen
Plattformen bleibt die vorhandene Server-Python-Erkennung verfügbar; falls
beide Engines fehlen, greift der bestehende Tesseract-Fallback, sofern
Tesseract installiert ist.

## Noch nicht abgedeckt

Der automatische RapidOCR-Installer unterstützt derzeit ausschließlich
Linux x86_64 mit glibc ≥2.28 und CPython 3.12. Windows, macOS, ARM64, Docker
mit anderen Python-Basisversionen, Termux und Android-APK erhalten damit
keine installierbare OCR-Runtime. APK-native ONNX- oder OCR-Bibliotheken,
verwaltete Updates und eine UI-Deinstallation sind nicht Teil dieses
Installers. Diese Plattformen dürfen erst als unterstützt gelten, wenn
Artefakte, Lizenz-/Integritätsprüfung und reale Funktionstests vorliegen.
