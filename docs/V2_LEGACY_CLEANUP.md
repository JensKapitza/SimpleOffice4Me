# V2 legacy-cleanup readiness

Phase 15 must remove compatibility code only after its V2 replacement is
production-ready. Destructive cleanup is therefore intentionally not implemented
yet.

The read-only readiness gate reports whether the retained plaintext storage
layers are still required and which conditions block removal.

## Current cleanup targets

The storage migration currently retains two plaintext compatibility areas:

- the legacy DocumentStore projection and its document metadata below
  `.simpleoffice-meta/documents`,
- the pre-encryption V2 plaintext blob store below
  `.simpleoffice-v2/blob-store`.

They remain rollback/compatibility data and must not be deleted manually.

## Readiness conditions

Cleanup must remain blocked until all of these are true:

1. authoritative storage mode is V2,
2. encrypted V2 blob protection is active,
3. no storage master-key rotation is pending,
4. the authoritative runtime no longer synchronizes mutations into the legacy
   DocumentStore projection.

The authoritative adapter currently declares
`requires_legacy_projection = True`, so the gate is expected to report
`ready_for_cleanup = false` today.

The VFS regular-file **read/list** path is no longer one of those blockers in
authoritative V2 mode: active files are enumerated from `ObjectCatalog` and
content is read through `StoragePort` even when the retained plaintext file is
absent. V1 and shadow mode intentionally keep the legacy projection because
shadow verification compares both sides.

The ordinary document/image raw-preview route and thumbnail fallback read
through verified `StoragePort.copy_verified_to()`. Raw **video** playback now
uses `StoragePort.copy_verified_range_to()`, so browser seek/range requests no
longer require the projected physical document file.

The remaining compatibility dependency is narrower but still real. Video
transcoding is no longer a blocker: ffmpeg receives a private temporary file
streamed and verified through `StoragePort`, and that temporary plaintext is
removed when the operation ends.

ZUGFeRD/Factur-X document inspection and the PDF inspection performed when
attaching an existing document to a contact also use this verified temporary
materialization. They continue to work when a V2 plaintext projection is absent
or stale, including with the encrypted V2 backend. A failed integrity check
never falls back to that projection and does not attach the PDF or store its
invoice attributes. Temporary content is removed after inspection, including
on parser failure; accounts denied the documents feature cannot inspect or
attach documents.

Finalized invoice downloads, invoice ZIP exports and customer document archive
content now use verified StoragePort reads as well. ZIP members are opened only
after successful private temporary materialization; missing source content is
reported in the customer manifest, while integrity failures reject the export
without a successful export audit record. ZIPs are built on disk and copied in
bounded chunks. Invoice downloads preserve ETag and byte-range support. All
three download routes require the documents feature and return private,
non-cacheable responses. Invoice/contact metadata and the billing-page
availability read model still use the legacy projection; other business
consumers remain, so the overall cleanup gate stays blocked.

Photo metadata refresh also uses verified temporary StoragePort materialization
for Pillow and exiftool. Missing or stale projections no longer affect refresh;
integrity failures do not invoke parsers or update metadata/tags. Private
temporary content is removed on success and parser failure, and the documents
feature is required before materialization. Initial bulk photo import remains
a DocumentStore mutation/projection path and is not removed by this change.

The gate now publishes an explicit remaining-consumer inventory. Persistent V2
writes/recovery still project document content into `DocumentStore`, WebDAV
still has direct managed-file consumers, several business/rental/photo/contact/
replication paths still resolve managed document files directly, and the legacy
federation-transfer worker still has a direct managed-file path. Directory and
access-policy handling also continues to use the compatibility namespace.
Therefore the global cleanup flag must remain true and destructive cleanup
remains blocked.

These retained paths are an explicit post-V2 compatibility window tracked by
#471. They do not change V2 storage authority and must not be bypassed by a
premature destructive cleanup.

## Safety

The readiness API is read-only and exposes `deletion_supported = false`.
It inventories the known retained targets, reports
`remaining_content_projection_consumers`, and returns explicit blockers. It
does not unlink, truncate, move or rewrite legacy data.

A later Phase-15 cleanup implementation should consume this gate rather than
adding an independent bypass. Actual deletion should only be added after the
projection-free authoritative runtime and dependent consumers have been
accepted.
