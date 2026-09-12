# Video-Vorschau und Video-Varianten

## Ziel

Ein Video bleibt in SimpleOffice4Me genau ein Dokument. Vorschaubilder und neu kodierte Wiedergabefassungen werden als technische Ableitungen mit diesem Dokument verknüpft und nicht als neue Dokumente indiziert.

Das ist wichtig, weil eine neu kodierte MP4-Datei zwar binär anders ist, fachlich aber weiterhin dasselbe Video darstellt. Tags, Beziehungen, Aufgaben, Rechte und Federation sollen deshalb am Originaldokument hängen und nicht je Codec-Fassung dupliziert werden.

## Vorschaubilder

Standard sind **10 Bilder pro Video**. Erlaubt sind 1 bis 30 Bilder.

Die Anzahl ist direkt im Video-Player als globale Anwendungseinstellung änderbar. Gespeichert wird sie in der privaten SimpleOffice-Metadatenablage in `video-preview-settings.json`. Die Änderung wird über die bestehende RevisionHistory protokolliert.

Für Installationen, bei denen noch keine Einstellung gespeichert wurde, kann der Startwert weiterhin mit

```text
SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES=10
```

vorgegeben werden. Sobald ein Wert über die Anwendung gespeichert wurde, hat die persistierte Anwendungseinstellung Vorrang.

Die Laufzeit wird mit `ffprobe` bestimmt. Die gesamte Laufzeit wird in gleich große Abschnitte geteilt. Pro Abschnitt wird ein Bild in der Mitte erzeugt:

```text
timestamp = (duration / frame_count) * (index + 0.5)
```

Bei 100 Sekunden und 10 Bildern liegen die Zeitpunkte damit bei 5, 15, 25 bis 95 Sekunden. Die Segmentmitte wurde bewusst gewählt: Bilder exakt am Start oder Ende eines Videos sind häufiger schwarz, enthalten nur einen Einblendframe oder können am Dateiende nicht mehr zuverlässig dekodiert werden.

Die Bilder werden mit `ffmpeg` extrahiert, auf kleine WebP-Vorschauen reduziert und unter dem privaten Preview-Cache gespeichert. Zusätzlich wird eine Collage für die normale Dokumentvorschau erzeugt.

## Metadaten

Die vorhandenen Preview-Metadaten des Dokuments erhalten für Videos einen Bereich `video` mit:

- Dauer in Sekunden
- Codec und Container
- Breite und Höhe
- gewünschter und tatsächlich erzeugter Bildanzahl
- Abstand der Abschnitte
- Kennzeichen, ob die Zeitleiste normalisiert werden konnte
- Liste der Vorschaubilder mit Index, Zeitstempel und Cache-Pfad
- Liste abgeleiteter Video-Varianten

Die Metadaten hängen am bestehenden Dokument. Es entsteht kein eigener Dokumentdatensatz für jedes Vorschaubild.

Beispiel:

```json
{
  "preview": {
    "status": "ready",
    "source_sha256": "...",
    "video": {
      "duration_seconds": 100.0,
      "codec": "h264",
      "width": 1920,
      "height": 1080,
      "frame_target_count": 10,
      "frame_count": 10,
      "frame_interval_seconds": 10.0,
      "normalized_timeline": true,
      "frames": [
        {"index": 0, "timestamp_seconds": 5.0, "path": ".webcache/.../video-frame-001.webp"}
      ],
      "variants": []
    }
  }
}
```

## Player

Beim Öffnen der Vorschau eines Videos wird eine eigene Player-Seite angezeigt. Dort sind das Originalvideo, technische Metadaten und die Vorschaubilder sichtbar. Ein Klick auf ein Vorschaubild springt im Player an den zugehörigen Zeitpunkt.

Die unveränderte Originaldatei bleibt weiterhin direkt erreichbar. Der Player ist nur eine andere Darstellung desselben Dokumentes.

## Neu kodierte Varianten

Eine in SimpleOffice4Me erzeugte Transkodierung wird unterhalb des Preview-Caches des Originaldokuments gespeichert. Die erste angebotene Variante ist MP4 mit H.264/AAC bis 720p.

Die Variante erscheint im Player als verknüpfte Wiedergabefassung. Sie wird nicht als neues Dokument indexiert. In ihren Metadaten werden unter anderem gespeichert:

- Variant-ID und Bezeichnung
- SHA-256 der abgeleiteten Datei
- Dateigröße
- Erstellzeitpunkt und Benutzer
- `derived_from_sha256` des Originals

Damit gilt fachlich weiterhin: **ein Video, ein Dokument, mehrere technische Fassungen**.

## Cache-Lebenszyklus

Der Cache ist nach Dokument-ID und SHA-256 des Originals getrennt. Ändert sich der Originalinhalt, entsteht eine neue Cache-Generation. Veraltete Generationen werden nach erfolgreicher Neuberechnung entfernt.

Ändert sich die konfigurierte Zahl der Vorschaubilder, erkennt der Preview-Service die Abweichung und erzeugt die Bildserie beim nächsten Indexlauf neu. Bereits erzeugte Transcodes derselben Originalrevision bleiben bei einer reinen Preview-Regeneration erhalten.

Frames und Varianten werden bei der HTTP-Auslieferung nur verwendet, wenn `preview.source_sha256` weiterhin zum aktuellen SHA-256 des Dokumentes passt. Damit werden Ableitungen einer alten Originalrevision nicht versehentlich zu einer neuen Revision angezeigt.

## Warum ffmpeg und ffprobe

`ffprobe` liefert die tatsächliche Videolänge und technische Metadaten, ohne das gesamte Video zu dekodieren. `ffmpeg` extrahiert gezielt einzelne Frames und kann bei Bedarf eine kompatiblere Wiedergabevariante erzeugen.

Beide Programme werden ohne Shell aufgerufen und unterliegen dem vorhandenen Preview-Timeout und den Größenlimits.

Wenn `ffprobe` fehlt, kann eine einzelne ffmpeg-Vorschau als Fallback erzeugt werden, aber keine echte normalisierte Zeitleiste über die vollständige Videolänge. Für die vollständige Funktion sollten `ffmpeg` und `ffprobe` gemeinsam installiert sein.

## Warum `.webcache`

Der bestehende Indexdienst ignoriert `.webcache`. Genau das ist für diese Funktion erwünscht:

- Vorschaubilder tauchen nicht als neue Dokumente auf.
- Transcodes tauchen nicht als zweite Kopie des Videos auf.
- der Cache kann aus dem Original rekonstruiert werden.
- fachliche Beziehungen bleiben am Originaldokument.

## Bewusste Grenze

Extern erzeugte, bereits neu kodierte Dateien werden noch nicht automatisch anhand eines visuellen Fingerprints als gleiche Videoinhalte zusammengeführt. Die von SimpleOffice4Me selbst erzeugten Transcodes sind dagegen eindeutig als Varianten des Originals verknüpft.
