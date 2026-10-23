# Mobile capture-first entry

The **Erfassen** action in the responsive Documents menu opens the existing
photo-import workflow without requiring a book, object, room, project, or
document type to be selected first. The page links to three existing paths:

- **Kamera aufnehmen** uses the existing single-photo upload and the browser's
  `capture="environment"` hint. Browser or operating-system support determines
  whether this opens a camera directly or offers a file-source chooser.
- **Mehrere Fotos importieren** uses the existing sequential photo uploader.
  Each file receives an individual request, progress result, SHA-256-backed
  document identity, upload timestamp, and optional EXIF metadata. If identical
  photo content is already visible in the selected inbox/archive destination,
  the importer returns the existing document instead of creating another copy;
  the original filename and capture metadata are not overwritten.
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

Camera availability, permission prompts, HEIC support, iOS file selection, and
physical-device retry behavior still require Android WebView and iOS device
acceptance. If a browser ignores the capture hint, the standard system file
chooser remains available.
