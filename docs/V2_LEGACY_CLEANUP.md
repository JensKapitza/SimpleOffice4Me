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
`ready_for_cleanup = false` today. This is deliberate: file browser/search and
older protocol consumers still depend on the compatibility projection.

## Safety

The readiness API is read-only and exposes `deletion_supported = false`.
It inventories the known retained targets and returns explicit blockers. It does
not unlink, truncate, move or rewrite legacy data.

A later Phase-15 cleanup implementation should consume this gate rather than
adding an independent bypass. Actual deletion should only be added after the
projection-free authoritative runtime and dependent consumers have been
accepted.
