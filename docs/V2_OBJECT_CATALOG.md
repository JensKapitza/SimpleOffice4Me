# V2 Object Catalog

The V2 object catalog is the logical namespace layer between application-facing
storage operations and the physical V2 BlobStore.

Persistent format identity:

- family: `simpleoffice-v2-object-catalog`
- schema version: `1`
- storage: `.simpleoffice-v2/catalog.sqlite3`

## Responsibilities

The catalog owns:

- stable `LogicalObjectId`
- current `StorageLocation`
- current BlobStore version reference
- content size and SHA-256 integrity metadata
- active, deleted and recovery state
- transactional namespace conflicts

The catalog does **not** own blob bytes, chunks, cryptographic keys, user-facing
document metadata, shares or audit payloads.

## Invariants

- A path is never an object identity.
- Moving an object changes only its location.
- Replacing content changes the referenced blob version, not the logical ID.
- Two live objects cannot occupy the same location.
- A tombstone releases its old location but retains object identity and version
  information for recovery.
- Restoring a tombstone never overwrites a location that has since been reused.
- Optional expected-version checks reject stale concurrent mutations.
- Re-registering the exact same active mapping is idempotent.
- Unknown persistent format families or schema versions fail closed.

## Cutover role

This catalog is only phase A of #378. Adding it does not switch application
reads or writes away from the existing DocumentStore-backed StoragePort.

Later steps must:

1. populate the catalog from verified V1 -> V2 migration data,
2. implement a BlobStore-backed StoragePort using the catalog,
3. compare V1 and V2 in a shadow/read-only phase,
4. perform an explicit reversible cutover,
5. keep legacy data until recovery and compatibility acceptance are complete.
