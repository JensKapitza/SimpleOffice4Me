# Mobile capture-first entry

The **Erfassen** action in the responsive Documents menu opens the existing
photo-import workflow without requiring a book, object, room, project, or
document type to be selected first. The page links to three existing paths:

- **Kamera aufnehmen** uses the existing single-photo upload and the browser's
  `capture="environment"` hint. Browser or operating-system support determines
  whether this opens a camera directly or offers a file-source chooser. Select
  **OCR sofort ausführen und Fotodetails öffnen** to request local OCR and go
  straight to that photo's detail view. The option is off by default, so ordinary
  snapshots still return to the gallery. An identical photo reuses its existing
  details and OCR status. When OCR found text, expand **Erkannter Text** in the
  photo details; the words are also available through **Schnellsuche**. The photo
  remains a photo, not a PDF/EPUB/TXT Reader document.
- **Mehrere Fotos importieren** uses the existing sequential photo uploader.
  Each file receives an individual request, progress result, SHA-256-backed
  document identity, upload timestamp, and optional EXIF metadata. If identical
  photo content is already visible in the selected inbox/archive destination,
  the importer returns the existing document instead of creating another copy;
  the original filename and capture metadata are not overwritten. After an
  authorized file replacement, the importer compares the current bytes,
  not the original capture hash. Re-uploading the same current bytes keeps
  the existing OCR state.
- The photo-metadata pipeline supports JPEG, PNG, GIF, and WebP. HEIC remains a
  regular file-import fallback; the app does not claim photo-specific EXIF/OCR
  processing for it.
- **Andere Dateien / PDF** opens the existing multi-file document importer.
  Files enter the regular DocumentStore and can be managed and associated later.
- **Noch nicht zugeordnet** opens the existing DocumentStore inbox. When V3 Inbox
  is enabled, **Uploadstatus** also shows each upload's validation and malware-scan
  state.

These actions preserve the existing DocumentStore, photo importer, inbox, and
web-upload security checks. When V3 Inbox is enabled, uploads also use its
existing per-file status and optional malware-scan flow before being marked
complete. The capture page does not create a parallel attachment store. A
mobile upload is not automatically linked to a business object; the user can
add existing document relationships later.

## Recognize a photo after upload

Bulk photo upload can defer OCR. Open its document detail and choose **Run
OCR** in **Image analysis** to process just that photo locally, without
uploading it again. A successful result appears as recognized text and can be
found using `text:` in advanced document search. If Tesseract or its local
language packs are unavailable, retry after restoring them. The app tells
apart no readable text and a failed run. A failed retry on identical bytes
keeps the previously recognized text; changed image bytes require OCR again
and do not inherit the old full text. New OCR words are searchable from
the current file version in Quick Search and are not saved as tags. Existing
tags are retained: earlier versions did not distinguish OCR-generated words
from tags entered by a person. If a legacy tag still produces an unwanted
Quick Search hit, remove that tag in the document detail. The retry does not
modify or send the original file.

Camera availability, permission prompts, HEIC support, iOS file selection, and
physical-device retry behavior still require Android WebView and iOS device
acceptance. If a browser ignores the capture hint, the standard system file
chooser remains available.


### JPEG-Ausrichtung bei der Texterkennung

Bei JPEG-Fotos mit EXIF-Dreh- oder Spiegelhinweis richtet die lokale OCR ihre
Arbeitskopie aus. Die gespeicherte Aufnahme samt EXIF und Prüfsumme bleibt
unverändert. Die temporäre Graustufen-PNG enthält keine übernommenen Metadaten
und wird nach dem OCR-Versuch entfernt. Der JPEG-Decoder kann größere Fotos
vorab verkleinern; die Arbeitskopie ist auf 24 Millionen Pixel begrenzt.
Die Korrektur gilt für JPEGs bis 64 MiB. Für größere Dateien und andere
Formate bleibt der bisherige OCR-Pfad unverändert. Ohne EXIF-Hinweis wird
keine Drehung geraten.
