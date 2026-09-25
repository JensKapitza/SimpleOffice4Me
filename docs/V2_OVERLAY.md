# V2 overlay import journal

Phase 10 introduced the crash-aware overlay import journal. Since that first
step, the existing `VirtualFileSystem` used by WebDAV, SFTP and rsync has also
migrated its file-content/location operations onto the shared runtime
`StoragePort`.

## Purpose

Normal filesystem-style writes can be staged first and then committed through
the V2 `StoragePort`. The journal persists enough state to recover safely after
process interruption.

The compatibility filesystem tree remains the presentation namespace while the
runtime storage mode selects V1, shadow or authoritative V2 storage centrally.
WebDAV/SFTP/rsync continue to resolve user-visible paths through
`VirtualFileSystem`, but file read/write/copy/move/delete operations no longer
choose a concrete storage backend themselves.

## States

Each import has exactly one state:

- `pending`: staged content is complete and ready to process
- `processing`: a storage commit is currently in progress
- `committed`: the StoragePort returned a committed object
- `damaged`: staged bytes are missing or fail size/hash verification
- `recovery-needed`: the storage outcome is ambiguous or a non-retryable
  storage failure requires operator/service reconciliation

These states intentionally match the Phase 10 requirements in #314.

## Commit sequence

1. validate target through `StorageLocation`
2. write staging bytes with private permissions
3. fsync the staging file
4. atomically rename staging into its final staging name
5. persist journal row as `pending`
6. transition to `processing`
7. verify staged size and SHA-256
8. call `StoragePort.create_bytes`
9. persist the returned logical object id/version as `committed`
10. remove staging bytes

The staging hash is an integrity check only. It is not a logical object identity.

## Crash recovery

A process restart must not guess whether an external storage commit completed.
Any persisted `processing` row becomes `recovery-needed`.

A retry is allowed only after a caller explicitly confirms that no matching
storage commit exists. If reconciliation confirms that the object did commit,
the caller can acknowledge the committed `StoredObject` and the journal closes
without writing the content again.

This avoids duplicate writes after a crash at the storage transaction boundary.

## Retryable errors

Explicit retryable storage errors return the import to `pending`. The staged
content is retained and may be processed again later.

Unknown exceptions are treated more conservatively as `recovery-needed`
because the storage outcome cannot be proven from the exception alone.

## Rollback

Only `pending` and `damaged` imports may be deleted as safely uncommitted
staging. `processing` and `recovery-needed` entries require reconciliation
first.

## Security and namespace rules

- staging files use mode 0600
- staging lives below `.simpleoffice-v2/overlay/staging`
- target paths use the existing V2 `StorageLocation` validation
- no physical path becomes the logical object identity
- staged content is size-limited
- no Flask dependency is required
- no direct manipulation of blob/chunk internals occurs

## Current migration boundary

The crash-safe overlay state machine remains available for staged imports, and
the production `VirtualFileSystem` now calls the same runtime `StoragePort`
for regular-file reads and mutations. WebDAV, SFTP and restricted rsync inherit
that boundary because they operate through `VirtualFileSystem`.

Directory creation/removal, access-policy metadata, timestamps and the
presentation namespace still use the compatibility filesystem/metadata model.
A native FUSE/WinFSP mount is not implemented; WebDAV/SFTP provide the existing
mountable filesystem surfaces. Native OS mounting therefore remains an optional
platform integration rather than a hidden requirement for storage correctness.
