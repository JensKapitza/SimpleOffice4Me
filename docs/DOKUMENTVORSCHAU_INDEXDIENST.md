# Schnelle Dokumentvorschauen aus dem Indexdienst

## Zweck und Nutzen

Die Dokumentansicht liefert für Bilder, PDFs, Office-Dateien und Videos kleine WebP-Vorschauen statt große Originaldateien. Mehrseitige PDFs und aus Office-Dateien erzeugte PDFs erhalten zusätzlich eine Collage der ersten vier Seiten. Login, Dateiliste und Webserver führen keine Konverter aus: Der bereits getrennte, niedrig priorisierte Indexprozess erledigt die Arbeit parallel.

Die Originaldatei bleibt unverändert. Der Cache liegt unter `.webcache/<Dokument-ID>/<SHA-256>/`. Weil Dokument-ID und Inhalts-Hash statt des Pfads den Schlüssel bilden, bleibt eine fertige Vorschau beim Umbenennen oder Verschieben gültig. Nach einer Inhaltsänderung entsteht ein neuer Hash; erst nach erfolgreicher Erzeugung wird die alte Cachegeneration entfernt.

## Werkzeuge und Priorität

Beim Start erkennt der Indexdienst Werkzeuge ausschließlich mit einer Pfadsuche; er startet dabei noch keinen Konverter. Das Ergebnis erscheint als eine Zeile `Vorschauwerkzeuge` im Dienstprotokoll.

| Inhalt | Bevorzugter Weg | Rückfall / Grenze |
|---|---|---|
| JPEG, PNG, GIF, WebP, TIFF, BMP, AVIF, HEIC | Pillow, EXIF-orientiert, maximal 1200 × 1200 | ImageMagick `magick` oder `convert`, sofern Pillow das Format nicht dekodiert |
| PDF | Poppler `pdftoppm`, Seiten 1–4, danach Pillow | Ohne `pdftoppm` weiterhin Browser-Originalansicht, keine Cachevorschau |
| DOC(X), ODT, RTF, XLS(X), ODS, PPT(X), ODP | LibreOffice/`soffice` headless → temporäres PDF → Poppler | Nur wenn LibreOffice **und** Poppler vorhanden sind |
| Video | FFmpeg: genau ein Bild bei Sekunde 1 | Ohne FFmpeg weiterhin Original öffnen |

Die Implementierung folgt den dokumentierten Mechanismen von [ImageMagick `-thumbnail`](https://imagemagick.org/command-line-options/#thumbnail), [Poppler `pdftoppm` mit Seitenbereich und Skalierung](https://manpages.debian.org/trixie/poppler-utils/pdftoppm.1.en.html), [LibreOffice-Kommandozeilenparametern](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html) und [FFmpeg-Ausgabebegrenzung/Filterung](https://ffmpeg.org/ffmpeg.html). Das sind Werkzeug- und Formatspezifikationen; für eine interne Rastervorschau existiert kein einschlägiges IETF-Protokoll. WebDAV liefert weiterhin unveränderte Ressourcen und ETags.

## Installation und Konfiguration

Pillow ist eine normale Python-Abhängigkeit des Projekts. Auf Debian/Ubuntu können die optionalen Konverter beispielsweise so installiert werden:

```sh
sudo apt update
sudo apt install imagemagick poppler-utils libreoffice ffmpeg
```

Danach den SimpleOffice-Dienst neu starten. Es gibt keine geheimen Zugangsdaten und keine externe Übertragung. Die Erkennung akzeptiert Programme nur über den Dienst-`PATH`.

Umgebungseinstellungen:

- `SIMPLEOFFICE_PREVIEWS=0` deaktiviert neue Vorschauerzeugung. Vorhandene Cachedateien können anschließend bei gestopptem Dienst durch Löschen von `.webcache` entfernt werden.
- `SIMPLEOFFICE_PREVIEW_TIMEOUT_SECONDS` begrenzt jeden Konverter, Standard 45, zulässig 5–300 Sekunden.
- `SIMPLEOFFICE_PREVIEW_MAX_BYTES` begrenzt Quelldateien, Standard 256 MiB, zulässig bis 2 GiB.
- `SIMPLEOFFICE_PREVIEW_MAX_PIXELS` begrenzt die von Pillow dekodierte Pixelzahl, Standard 80 Millionen.

## Sicherheit, Rechte und Datenschutz

- Vorschau- und Collage-Endpunkte verlangen dieselbe Anmeldung und Dokument-Leseberechtigung wie das Original.
- `.webcache` ist für Scan, WebDAV, SFTP und virtuelle Pfadauflösung reserviert; Benutzer können ihn weder durchsuchen noch überschreiben.
- Konverter erhalten feste Argumentlisten ohne Shell. Dateinamen werden nicht als Befehle interpretiert. Eingabegröße, Pixelzahl und Laufzeit sind begrenzt.
- Konvertiert wird in einem privaten temporären Verzeichnis. Erst vollständige Ausgaben werden atomar in den Cache verschoben.
- Fehlerdaten enthalten nur `conversion_failed`, keine möglicherweise sensiblen Dateinamen, Dokumentinhalte oder Konverterausgaben.
- Cacheantworten sind `private`; hashgebundene Ergebnisse dürfen unveränderlich zwischengespeichert werden. Das Original bleibt separat erreichbar.

Konverter verarbeiten dennoch nicht vertrauenswürdige Dateien. Betriebssystempakete müssen deshalb regelmäßig aktualisiert und der Indexdienst möglichst mit einem eigenen, nicht privilegierten Konto betrieben werden. Eine Vorschau ersetzt keinen Virenscan; ClamAV-Regeln für Import und Anhänge bleiben unverändert.

## Fehler- und Ausfallverhalten

Fehlende Programme verhindern weder Anwendungs- noch Indexstart. Nicht unterstützte, zu große, beschädigte oder zeitüberschreitende Dateien erhalten den Zustand `unsupported`, `skipped_limit` oder `failed`. Die Oberfläche bietet weiter „Original öffnen“ an; bei Bildern darf der Thumbnail-Endpunkt bis zur fertigen Cachedatei kontrolliert das Original liefern. Ein Konverterfehler wird beim nächsten Indexlauf erneut versucht.

Die Erzeugung belastet nur den separaten Indexprozess, der bereits mit niedriger Prozesspriorität und konfigurierbaren kurzen Pausen arbeitet. Der HTTP-Threadpool und der Python-GIL des Webprozesses werden nicht für PDF-, Office- oder Videokonvertierung verwendet.

## Migration, Rückwärtskompatibilität und Rückkehr

Es gibt keine Datenbank- oder Datenmigration und keine Rechteausweitung. Alte Metadaten ohne `preview` bleiben gültig. Desktop-Clients, WebDAV, FreeFileSync, LibreOffice und SFTP sehen weiterhin ausschließlich die Originaldateien. Zum Rückkehr zum bisherigen Verhalten `SIMPLEOFFICE_PREVIEWS=0` setzen; die Original-Endpunkte und Browserwiedergabe bleiben erhalten.

## Tests und bekannte Grenzen

Automatisiert geprüft werden Werkzeugerkennung, unveränderte Originale, Bildskalierung, Cacheausschluss aus dem Scan, Cachegültigkeit nach Verschieben, Traversalabwehr, Zeitlimit sowie redigiertes Fehlerverhalten. Reale PDF-/Office-/Videokonvertierung hängt von den lokal installierten Versionen und deren Codecs/Importfiltern ab. Passwortgeschützte Dokumente, animierte Collagen, vollständige PDF-Seitenraster und SVG werden bewusst nicht automatisch gerendert.
