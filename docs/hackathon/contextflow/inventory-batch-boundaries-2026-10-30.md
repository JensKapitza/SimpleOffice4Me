# Inventar-Buchgruppen: Uploadgrenzen-Tests (30.10.2026)

Branch-Kontext: Sitzungskopie `muse/inventory-batch-20261030`, Worktree
`inventory-batch-20261030`. Laufzeit Meta-only, Modell-Pin
`muse-spark-1.3-contributor` unverändert; keine externen KI-Aufrufe,
nur synthetische Bytes über bestehende Test-Clients/Fixtures.

## Scope (eng begrenzt)

Nur Testabdeckung für bereits implementierte Fotoeingang-/
Buchgruppen-Uploadgrenzen vervollständigt. Geändert:

- `tests/test_inventory_capture_batch.py` (+2 Testmethoden in
  `BatchRouteTests`, keine bestehenden Tests angefasst)
- diese Notiz (neu)

Keine Produktionsdatei geändert. Serienlogik, Meta-Modelle,
Einwilligung und Adopt-Pfade unverändert.

## Abgedeckte Implementierung (unverändert, nur gelesen)

- `app/inventory.py`: `MAX_PHOTO_BYTES = 12 * 1024 * 1024`,
  `_image_extension()` prüft Magic Bytes (JPEG/PNG/WebP, sonst
  `ValueError` „Nur JPEG, PNG und WebP …").
- `app/inventory_capture.py` → `store_upload()`: liest
  `MAX_PHOTO_BYTES + 1`, lehnt leere/zu große Uploads mit
  „Foto fehlt oder überschreitet 12 MiB." ab, danach
  Magic-Byte-Prüfung. Größenprüfung läuft vor dem Store-Write.
- `app/inventory_capture_routes.py` → `capture_upload`: mappt
  `ValueError` auf 400, `CaptureInboxConflict` auf 409,
  `CaptureInboxIndexError` auf 503.
- Globaler `MAX_CONTENT_LENGTH`-Default (512 MiB) lässt 12-MiB-Fotos
  bis zur Routenprüfung durch; kein 413-Risiko für diese Tests.

## Neue Tests (keine Doppelabdeckung)

Vorher gab es keinen Routen-Test für Übergröße oder gefälschte
Bytes auf `POST /inventory/capture/upload` (nur Unit-Signaturcheck
`test_photo_type_is_checked_by_signature` in `tests/test_inventory.py`
und `test_inventory_suggest_rejects_bad_image` für die andere Route
`/contextflow/inventory-suggest`).

1. `test_upload_rejects_oversize_photo_without_side_effects`:
   valider PNG-Magic + Nullen auf exakt `MAX_PHOTO_BYTES + 1`
   (12 MiB + 1 Byte) mit `batch_id` → 400 mit „12 MiB".
   Beweist, dass das Größen-Gate (nicht das Typ-Gate) ablehnt.
2. `test_upload_rejects_unsupported_or_mismatched_photo` (2 subTests):
   Textbytes als `foto.png`/`image/png` sowie GIF-Magic als
   `foto.jpg`/`image/jpeg` → je 400 mit „JPEG, PNG".
   Beweist Magic-Byte-Prüfung statt Client-MIME-Vertrauen.

Beide verifizieren nach jedem Fehler: kein Inbox-Indexeintrag
(`CaptureInboxStore.list("anna") == []`), kein Originalmedium
(`media/inbox` ohne Dateien), keine Batch-Zuordnung
(`list_batch(..., BATCH_A) == []`), kein Inventarobjekt
(`InventoryEnrichmentStore.object_metas() == {}` und
`ObjectStore.objects() == []`).

## Verifikation

- Die Muse-Sandbox hatte keinen Python-Interpreter. Danach im
  Haupt-Checkout mit `.venv\Scripts\python.exe` ausgeführt:
  **30/30 Tests bestanden**.

```bat
.venv\Scripts\python.exe -m unittest tests.test_inventory_capture_batch -v
```

- Die frühere Erwartung von 29 Tests bezog sich auf den Muse-Worktree;
  der Haupt-Checkout enthält 28 Bestandstests plus 2 neue Tests.
- Keine Produktionsänderung nötig: die bestehende Implementierung
  weist beide Grenzfälle vor dem Store-Write zurück.
- Danach bei Bedarf Gates:
  `python tools/check_project_policy.py .`,
  `python tools/check_file_size.py . --limit 1000`,
  `python tools/check_function_size.py app tools --limit 300`,
  `python -m compileall -q app tools`.
