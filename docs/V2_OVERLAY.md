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


## Filesystem watcher reconciliation

The existing document index worker remains the single recursive filesystem
watcher. In shadow/V2 mode it now performs a second bounded step after the
DocumentStore scan:

1. the scan identifies or preserves the stable document ID and verified SHA-256;
2. the reconciler adopts create/modify/move observations into the V2 blob store
   and catalog with optimistic catalog version checks;
3. an out-of-band delete reads the still-authoritative V2 payload and creates a
   private recoverable compatibility payload before publishing the catalog
   deletion;
4. a rename already recognized under the same document ID is not misclassified
   as a deletion;
5. failures mark the cutover state dirty and, for known objects, move the
   catalog object to recovery-needed instead of silently choosing one side.

The periodic full index run reconciles both known compatibility paths and all
active V2 catalog locations. It therefore repairs a missed watch notification
or surfaces the divergence as recovery-needed.

The watcher ignores `.simpleoffice-v2` itself, in addition to the existing
metadata/history/cache directories, so blob/chunk writes do not recursively
feed back into the document scanner.

Encrypted V2 uses the same configured external storage-password-file boundary
as other runtime storage consumers. No master key, password or recovery key is
stored in the document tree or passed on the command line.
