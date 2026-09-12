# Video-Vorschau und Video-Varianten

## Ziel

Ein Video bleibt in SimpleOffice4Me genau ein Dokument. Vorschaubilder und neu kodierte Wiedergabefassungen werden als technische Ableitungen mit diesem Dokument verknüpft und nicht als neue Dokumente indiziert.

## Vorschaubilder

Standard sind 10 Bilder pro Video. Der Wert ist mit `SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES` einstellbar; erlaubt sind 1 bis 30 Bilder.

Die Laufzeit wird mit ffprobe bestimmt. Die gesamte Laufzeit wird in gleich große Abschnitte geteilt. Pro Abschnitt wird ein Bild in der Mitte erzeugt:

`timestamp = (duration / frame_count) * (index + 0.5)`

Bei 100 Sekunden und 10 Bildern liegen die Zeitpunkte damit bei 5, 15, 25 bis 95 Sekunden. Die Segmentmitte vermeidet häufig schwarze Start- oder Endbilder.

Die Bilder werden mit ffmpeg extrahiert, auf kleine WebP-Vorschauen reduziert und unter dem privaten Preview-Cache gespeichert. Zusätzlich wird eine Collage für die normale Dokumentvorschau erzeugt.

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

## Player

Beim Öffnen der Vorschau eines Videos wird eine eigene Player-Seite angezeigt. Dort sind das Originalvideo, technische Metadaten und die Vorschaubilder sichtbar. Ein Klick auf ein Vorschaubild springt im Player an den zugehörigen Zeitpunkt.

Die unveränderte Originaldatei bleibt weiterhin direkt erreichbar.

## Neu kodierte Varianten

Eine in SimpleOffice4Me erzeugte Transkodierung wird unterhalb des Preview-Caches des Originaldokuments gespeichert. Die erste angebotene Variante ist MP4 mit H.264/AAC bis 720p.

Die Variante erscheint im Player als verknüpfte Wiedergabefassung. Sie wird nicht als neues Dokument indexiert. In ihren Metadaten wird unter anderem der SHA-256 des Originals als Herkunft gespeichert.

Damit gilt fachlich weiterhin: ein Video, ein Dokument, mehrere technische Fassungen.

## Cache-Lebenszyklus

Der Cache ist nach Dokument-ID und SHA-256 des Originals getrennt. Ändert sich der Originalinhalt, entsteht eine neue Cache-Generation. Veraltete Generationen werden nach erfolgreicher Neuberechnung entfernt.

Ändert sich die konfigurierte Zahl der Vorschaubilder, erkennt der Preview-Service die Abweichung und erzeugt die Bildserie beim nächsten Indexlauf neu. Bereits erzeugte Transcodes derselben Originalrevision bleiben bei einer reinen Preview-Regeneration erhalten.

## Warum ffmpeg und ffprobe

ffprobe liefert die tatsächliche Videolänge und technische Metadaten, ohne das gesamte Video zu dekodieren. ffmpeg extrahiert gezielt einzelne Frames und kann bei Bedarf eine kompatiblere Wiedergabevariante erzeugen.

Beide Programme werden ohne Shell aufgerufen und unterliegen dem vorhandenen Preview-Timeout und den Größenlimits.

Wenn ffprobe fehlt, kann die alte einzelne ffmpeg-Vorschau als Fallback erzeugt werden, aber keine echte normalisierte Zeitleiste über die vollständige Videolänge. Für die vollständige Funktion sollten ffmpeg und ffprobe gemeinsam installiert sein.

## Bewusste Grenze

Extern erzeugte, bereits neu kodierte Dateien werden noch nicht automatisch anhand eines visuellen Fingerprints als gleiche Videoinhalte zusammengeführt. Die von SimpleOffice4Me selbst erzeugten Transcodes sind dagegen eindeutig als Varianten des Originals verknüpft.
