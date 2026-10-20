# Systemwerkzeuge für Virenschutz und Vorschauen

SimpleOffice kann Browser-Originalvorschauen ohne Zusatzprogramme ausliefern.
Für kleine Vorschaubilder, PDF-Seiten, Medien und Office-Dokumente sind native
Konverter optional. Die Startprüfung erkennt das Betriebssystem und meldet nur
fehlende Programme; sie installiert oder startet nichts automatisch.

| Funktion | Erkannter Befehl | Debian/Ubuntu-Paket |
|---|---|---|
| Virenschutz | `clamdscan` oder `clamscan` | `clamav clamav-daemon` |
| Signaturen | `freshclam` | `clamav` |
| Bilder/Collagen | `magick` oder kompatibles `convert` | `imagemagick` |
| erste PDF-Seite | `pdftoppm` | `poppler-utils` |
| PDF-S/W-Export | `gs` | `ghostscript` |
| OCR-Textebene | `tesseract` | `tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng` |
| Audio/Video | `ffmpeg` | `ffmpeg` |
| Office-Dateien | `libreoffice` oder `soffice` | `libreoffice` |

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install clamav clamav-daemon imagemagick poppler-utils ghostscript \
  tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng ffmpeg libreoffice
sudo freshclam
sudo systemctl enable --now clamav-daemon
```

Fedora/RHEL nutzt `clamav clamav-update clamd ImageMagick poppler-utils
ghostscript tesseract tesseract-langpack-deu tesseract-langpack-eng ffmpeg-free
libreoffice-headless`. macOS nutzt `brew install clamav imagemagick poppler
ghostscript tesseract ffmpeg` sowie `brew install --cask libreoffice`. Unter Windows stehen
der offizielle ClamAV-Installer sowie `winget install ImageMagick.Q16`,
`winget install Gyan.FFmpeg` und `winget install
TheDocumentFoundation.LibreOffice` zur Verfügung.

Aktuelle Originalanleitungen: [ClamAV](https://docs.clamav.net/manual/Installing.html),
[ImageMagick](https://imagemagick.org/script/download.php),
[Poppler](https://poppler.freedesktop.org/),
[FFmpeg](https://ffmpeg.org/download.html) und
[LibreOffice](https://www.libreoffice.org/download/download-libreoffice/).

## Dokumentvorschau-Limits

Administratoren können in **Einstellungen → Dokumente & Medien** die
Vorschaugenerierung aktivieren, das Zeitlimit (Standard 45 s; 5–300 s), die maximale
Quelldateigröße (Standard 256 MiB; 1 KiB–2 GiB), die maximale Pixelzahl (Standard 80
Mio.; 1–250 Mio.) und die Video-Framezahl (Standard 10; 1–30) einstellen. Werte werden
im gemeinsamen SettingsStore gespeichert, revisionssicher und im Security-Audit
protokolliert. Änderungen gelten für neue Preview-Aufträge; bestehende Cache-Dateien
werden nicht automatisch gelöscht. Der laufende Indexdienst erkennt zentrale Änderungen
innerhalb höchstens einer Sekunde, ohne Neustart. Eine geänderte Video-Framezahl markiert
die Serie beim nächsten Indexlauf als veraltet.

Die bisherigen Environment-Variablen `SIMPLEOFFICE_PREVIEWS`,
`SIMPLEOFFICE_PREVIEW_TIMEOUT_SECONDS`, `SIMPLEOFFICE_PREVIEW_MAX_BYTES`,
`SIMPLEOFFICE_PREVIEW_MAX_PIXELS` und `SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES` werden beim
Upgrade für jeden Medienwert als Fallback gelesen, solange der jeweilige zentrale Wert
noch fehlt. Der nächste SettingsStore-Schreibvorgang übernimmt die wirksamen Fallbackwerte
in den zentralen Store. Danach steuern ausschließlich die gespeicherten Werte den Dienst.

ImageMagick verarbeitet nicht vertrauenswürdige Dateien nur mit einer restriktiven
[Security Policy](https://imagemagick.org/script/security-policy.php). Bis eine
ressourcenbegrenzte, isolierte Konverter-Pipeline implementiert ist, dient die
Prüfung nur als Betriebsdiagnose; fehlende Programme führen nicht zu einem
unsicheren Fallback oder automatischer Ausführung.
