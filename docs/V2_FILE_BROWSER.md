# V2 file browser migration

Phase 11 migrates browser-side document mutations incrementally onto the V2
storage service boundary. The existing document UI and metadata projection stay
in place while mutating operations are moved behind `StoragePort`.

## First migrated operation: move

The document detail action "In Ordner verschieben" now:

1. resolves the existing document metadata by stable document id,
2. derives only the current filename from metadata,
3. validates the requested destination through `StorageLocation`,
4. calls `StoragePort.move`,
5. renders the returned logical location,
6. preserves the original `LogicalObjectId`.

The route no longer calls `DocumentStore.move_document` directly.

The current adapter remains `DocumentStoreStorageAdapter`, so existing audit,
history, recovery and filesystem behavior are preserved while the browser no
longer depends on the concrete V1 mutation API.

## Second migrated operation: copy

The document detail page now also exposes an explicit copy action. It calls
`StoragePort.copy` with the stable source `LogicalObjectId` and a validated
`StorageLocation`.

Unlike move, copy must return a different logical object identity. The current
DocumentStore adapter keeps the existing verified copy implementation, metadata
copy rules and audit/history behavior while the browser no longer calls the
concrete V1 copy API.

## Third migrated operation: delete/recovery

The document detail page now exposes an explicit recoverable delete action.
Deletion requires a deliberate confirmation and calls `StoragePort.delete`
with the current content version as the optimistic concurrency guard.

The transitional DocumentStore adapter keeps the existing soft-delete behavior:
the active file is moved into the recovery area, audit/history remains intact,
and the existing recovery page is the next user-visible step after success.

The browser route does not call `DocumentStore.soft_delete_document` directly
and does not manipulate recovery paths.

## Fourth migrated operation: streaming upload/import

The multi-file browser upload now calls `StoragePort.import_stream` instead of
calling `DocumentStore.import_upload` directly.

The transitional adapter deliberately delegates to the existing streaming V1
importer. This preserves:

- bounded chunked reads instead of loading large uploads fully into memory,
- the configured upload-size limit,
- staging and SHA-256 verification,
- inbox versus hash-based archive placement,
- collision-safe generated filenames,
- existing upload audit/history events.

Post-import organizational defaults such as tags/state remain on the existing
metadata projection for now; they are not blob/storage manipulation.

## Error behavior

Storage errors remain represented by the V2 result contract. The browser shows
the controlled storage error message and keeps the user on the existing
document detail flow. No physical blob/chunk path is exposed to the route.

## Scope boundary

The browser file mutations migrated so far are move, copy, recoverable delete and streaming upload/import.

Still to migrate in separate bounded changes:

- content replacement mutations
- any file-browser operation that still writes through a concrete
  `DocumentStore` implementation

Listing, search, metadata projection and document detail reads remain on the
current read model for now. They do not manipulate V2 blob internals.

## Compatibility

The route contract, form field and redirects are unchanged. Existing
DocumentStore files are not migrated, renamed or rewritten merely because this
service boundary is introduced.
