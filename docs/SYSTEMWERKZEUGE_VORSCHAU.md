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
| Audio/Video | `ffmpeg` | `ffmpeg` |
| Office-Dateien | `libreoffice` oder `soffice` | `libreoffice` |

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install clamav clamav-daemon imagemagick poppler-utils ffmpeg libreoffice
sudo freshclam
sudo systemctl enable --now clamav-daemon
```

Fedora/RHEL nutzt `clamav clamav-update clamd ImageMagick poppler-utils
ffmpeg-free libreoffice-headless`. macOS nutzt `brew install clamav imagemagick
poppler ffmpeg` sowie `brew install --cask libreoffice`. Unter Windows stehen
der offizielle ClamAV-Installer sowie `winget install ImageMagick.Q16`,
`winget install Gyan.FFmpeg` und `winget install
TheDocumentFoundation.LibreOffice` zur Verfügung.

Aktuelle Originalanleitungen: [ClamAV](https://docs.clamav.net/manual/Installing.html),
[ImageMagick](https://imagemagick.org/script/download.php),
[Poppler](https://poppler.freedesktop.org/),
[FFmpeg](https://ffmpeg.org/download.html) und
[LibreOffice](https://www.libreoffice.org/download/download-libreoffice/).

ImageMagick verarbeitet nicht vertrauenswürdige Dateien nur mit einer restriktiven
[Security Policy](https://imagemagick.org/script/security-policy.php). Bis eine
ressourcenbegrenzte, isolierte Konverter-Pipeline implementiert ist, dient die
Prüfung nur als Betriebsdiagnose; fehlende Programme führen nicht zu einem
unsicheren Fallback oder automatischer Ausführung.
