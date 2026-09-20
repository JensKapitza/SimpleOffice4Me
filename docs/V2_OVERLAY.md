# V2 overlay import journal

Phase 10 introduces a side-by-side overlay import path without replacing the
existing `VirtualFileSystem` used by WebDAV, SFTP and rsync.

## Purpose

Normal filesystem-style writes can be staged first and then committed through
the V2 `StoragePort`. The journal persists enough state to recover safely after
process interruption.

The existing V1 filesystem tree remains the production namespace until later
migration phases explicitly move callers to the V2 service boundary.

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

## Migration boundary

This PR does not mount a FUSE/WinFSP filesystem and does not redirect WebDAV,
SFTP, rsync or the existing `VirtualFileSystem` to V2. It establishes the
crash-safe import state machine and StoragePort boundary required before those
surfaces are migrated.
