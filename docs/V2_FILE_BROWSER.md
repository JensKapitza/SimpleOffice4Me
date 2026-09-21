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

## Error behavior

Storage errors remain represented by the V2 result contract. The browser shows
the controlled storage error message and keeps the user on the existing
document detail flow. No physical blob/chunk path is exposed to the route.

## Scope boundary

This PR intentionally migrates only the existing browser move action.

Still to migrate in separate bounded changes:

- delete/recovery mutations
- content replacement/upload mutations
- any file-browser operation that still writes through a concrete
  `DocumentStore` implementation

Listing, search, metadata projection and document detail reads remain on the
current read model for now. They do not manipulate V2 blob internals.

## Compatibility

The route contract, form field and redirects are unchanged. Existing
DocumentStore files are not migrated, renamed or rewritten merely because this
service boundary is introduced.
