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
Its range size/version lookup uses `StoragePort.stat()` and the active catalog,
rather than the optional legacy document `size` field. A range is published only
when verified content still matches that metadata snapshot.

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

Document reads for local replication and Restic backup now use verified private
StoragePort materialization too. Missing/stale projections do not substitute
for authoritative bytes. Each copied file is verified and atomically replaced;
a failed file copy preserves its previous target. A replication run is not a
transaction across all files: earlier verified copies can remain after a later
failure, but no successful manifest/status is published for that failed run.
Restic working plaintext is removed after success, failure or timeout; failed
staging is removed before Restic starts. Mirrors intentionally remain readable
copies. Document listing/tag metadata, control-file snapshots and importing a
mirror remain compatibility paths; this does not release the cleanup gate.

Source validation, snapshots and evidence freezing for rental approval now
read verified StoragePort bytes as well. Evidence is checked against the
snapshot hash before copying and checked again after copying. Missing or
corrupt sources reject approval; the existing staging rollback removes
unpublished artifacts and permits retry. Frozen evidence intentionally remains
part of the immutable approval package. Rental metadata still remains a compatibility dependency.

The outgoing legacy SOFP worker now materializes managed document bytes privately
through StoragePort for configured-peer and delegated-capability uploads. The
complete source is verified against the transfer blob hash before contacting the
target, including resumed transfers that already have all chunks. Stale or
missing V2 projection content is not uploaded; corrupt/unavailable authoritative
content fails the job without fallback. Temporary plaintext is removed after
success, preparation/upload failure or retry. HTTP error response bodies are not
persisted in transfer diagnostics. Chunk selection, resume bitmaps and target
capabilities retain their existing wire contract.

In authoritative V2 mode, digest-to-object lookup now uses an indexed query on
the active `ObjectCatalog` content hash; it does not require the legacy scan
index. V1 and shadow modes retain the compatibility-index lookup. The legacy
`/federation/v1/documents/<id>/manifest`, document/blob download, blob manifest,
chunk and availability endpoints now materialize and verify authoritative
StoragePort content before returning bytes or metadata. Streamed temporary
content remains alive until the response closes and is then removed. These
routes work with a missing projection and missing scan-index rows in V2 mode.

Content-defined block manifests, catalog listing, repair and rebalance
still have projection consumers, so Federation remains a cleanup blocker and
retained data must not be deleted.

The legacy Federation document catalog now obtains each advertised object's
hash, size and namespace location from `StoragePort.stat`. Authoritative V2
catalog listings therefore do not depend on physical projection files or scan
rows, including in encrypted V2 mode. The wire format, tags, origin metadata,
sort order, generation hash and pagination remain compatible. Existing legacy
deletion markers and authoritative tombstones still omit objects. V1/shadow
continues to use the legacy storage adapter and requires its readable namespace.

Storage failures return a sanitized 503 rather than a successful partial
generation, which could otherwise mark remote objects unavailable. The catalog
is a metadata advertisement: it does not materialize or verify all advertised
content bytes. Downloads still perform their own complete integrity checks.
Metadata enumeration, tags, origin and modification timestamps still use
`DocumentStore`; catalog listing is not yet a projection-free read model and
does not release the cleanup gate.

The document-scoped `/federation/v2/blocks/documents/<id>/manifest` and block
download routes now use a private verified StoragePort snapshot. Manifest
hashes and block bytes come from the same snapshot, including in encrypted V2
mode and with missing or stale projection files or scan-index rows. The
snapshot is removed on success, HEAD, invalid proofs and failures. Each request
verifies the complete current object before returning data; an old session is
invalid after an authoritative content revision. A retained legacy deletion
marker still denies access, and bearer, peer HMAC, nonce, rate-limit and
session-proof checks remain enforced. Integrity failures return a sanitized 503
without falling back to a valid projection or old block cache.

These scoped routes neither register temporary paths in the persistent block
index nor create permanent plaintext block copies. They recompute the manifest
per request and require temporary disk space and full-object I/O, even for one
block. Legacy v1 block manifests, global block indexing/cache, catalog listing,
repair and rebalance remain separate compatibility consumers. This change
does not permit plaintext cleanup or claim full encryption of the data root.

Federation admin document selection and transfer creation now generate their
manifest from verified private StoragePort materialization as well. This covers
document-ID and hash-based transfers, local digest selection for orchestration
and availability queries, and displayed size/hash. Missing or stale V2 projection
files do not block these operations; integrity failures create no transfer and
leave the admin page available with an error. Both creation routes reject inactive,
unknown or explicitly send-denying peers before materializing content. Admin
authorization and CSRF remain enforced. Manifest generation reads the complete
source, so selecting a large document requires temporary disk space and storage
I/O. The separate catalog listing and content-defined block manifest,
repair and rebalance still retain projection dependencies.

The gate now publishes an explicit remaining-consumer inventory. Persistent V2
writes/recovery still project document content into `DocumentStore`, and WebDAV
still has direct managed-file consumers. The earlier business/rental/photo/
contact/replication content readers have been reduced by the StoragePort
migrations documented above; the remaining dependency is DocumentStore-based
enumeration and metadata/policy read models, including catalog metadata.
Legacy global Federation block indexing/cache, repair and rebalance also retain
compatibility-path dependencies. Therefore the global cleanup flag must remain
true and destructive cleanup remains blocked.

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

Rental tenant-package federation now imports approved ZIP packages through the
existing streaming StoragePort import and builds its transfer manifest from
verified private materialization. The import must match the approved package
hash before a job is created. Missing/stale V2 projections and encrypted V2 are
supported; integrity or manifest failures create no transfer/export success.
Network failures do not record a successful rental export. Admin/CSRF and the
explicit rental/document peer-send policy are retained before import. Temporary
materialized content is cleaned on success and failure. The approved package
remains an immutable approval artifact; an already completed document import may
remain after a later transfer failure and keeps its normal storage audit history.
Queued federation exports retain the existing bookkeeping and are not delivery
receipts. Metadata/index, other legacy Federation paths and cleanup/restore
acceptance still block destructive cleanup.

Legacy SOFP HTTP document/blob downloads, document and chunk manifests, chunks
and availability now read verified private StoragePort materialization. Blob
lookup reuses the worker's indexed identity binding and rejects a mismatch with
HTTP 409; unavailable/integrity-failing storage returns a sanitized HTTP 503,
missing/forbidden objects return 404. A stale or absent V2 projection is not a
content source. The complete object is verified before even HEAD, conditional
304 or range/chunk responses. ETags, range/suffix resume, chunk URLs and bearer
or scoped transfer authentication retain their existing contracts. HEAD now
reports the actual representation length instead of an empty body's length.
Temporary materialization is removed after complete consumption, early response
close, stream/construction failure and non-streaming responses. Verification
requires temporary disk space and full-object I/O per request, including ranges
and HEAD; this is not a range-only optimization. Legacy catalogs, block indexes,
metadata selection and incoming-transfer import still require separate work.

Existing legacy document tombstones also hide V2 content-addressed downloads,
including the worker path. V2 digest lookup does not require a scan row or a
legacy metadata record, but a retained tombstone must not be bypassed by the
active V2 catalog. Other metadata and namespace consumers still retain the
overall compatibility dependency.

Mail-case draft attachment downloads and SMTP submission now use verified
StoragePort reads in V1, shadow, plaintext V2 and encrypted V2. The complete
object is verified before content is released, and its version/hash/size must
match the clean scan and frozen draft reference. Only the declared attachment
size plus one byte is buffered (at most 50 MiB plus one byte); authoritative
storage failures never fall back to a valid projection. V2 reads tolerate a
missing or stale content projection. Mail-case authorization, malware gating,
SMTP approval/delegation, revocation and audit stay in their existing services.
Document metadata, folder policies and other mail consumers remain projection
dependencies; this scoped change for #471 does not release the cleanup gate.

Local mail archive listing/search, EML previews, MIME attachment reads and
manual IMAP archive duplicate detection now read verified StoragePort content.
The authoritative V2 archive namespace uses ObjectCatalog locations, so missing
or stale projection files and absent scan rows do not substitute for blob
content. Full-object verification precedes MIME parsing; the buffer is limited
to 100 MiB plus one byte and SHA-512 filenames must match the verified message.
Invalid/unavailable entries are omitted from local search with sanitized
technical diagnostics, while direct previews/downloads fail closed. Existing
case grants are restricted to a linked message on the two case read routes;
revocation, ClamAV download gating, history and private generic-document access
remain enforced. V1/shadow keep their existing namespace and unmanaged EML
compatibility. Metadata, folder policies and other mail mutation/archiving paths
remain compatibility consumers. This #471 increment does not release cleanup.

Confirmed EML attachment extraction now obtains its source from verified
StoragePort content in V1/shadow/plaintext V2/encrypted V2. Preview MIME parts and
manifest SHA-256 are derived from the same verified bytes; confirmation rechecks
current document visibility/deletion, actor, expiry and manifest hash before
parsing or scanning. Missing/stale V2 projection content is not a source, and
integrity failures publish neither preview nor import. The EML read buffer is
bounded to 512 MiB plus one byte; existing decoded attachment quotas, ClamAV,
quarantine, provenance, imports and audit remain in their existing services.
Manifest schema stays compatible. Separate document inventory scans and other
archiving mutations still have projection paths; metadata/policy and the global
cleanup/rollback gate remain retained dependencies for #471.
