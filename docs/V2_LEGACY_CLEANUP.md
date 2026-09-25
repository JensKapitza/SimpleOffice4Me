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

The remaining compatibility dependency is narrower but still real: V2 writes
are projected back into `DocumentStore`, directory/access-policy handling uses
the compatibility filesystem namespace, and browser/search/metadata consumers
still read the legacy metadata projection. Therefore the global cleanup flag
must remain true and destructive cleanup remains blocked.

## Safety

The readiness API is read-only and exposes `deletion_supported = false`.
It inventories the known retained targets and returns explicit blockers. It does
not unlink, truncate, move or rewrite legacy data.

A later Phase-15 cleanup implementation should consume this gate rather than
adding an independent bypass. Actual deletion should only be added after the
projection-free authoritative runtime and dependent consumers have been
accepted.
