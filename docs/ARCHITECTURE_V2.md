# SimpleOffice4Me V2 architecture contract

This document is the executable design boundary for the incremental V2 work in
#314. Existing V1 behavior remains supported until a replacement, migration and
recovery path have been verified.

## Layer boundaries

V2 code is split into the following dependency direction:

`UI/API -> application services -> domain/contracts -> ports <- adapters`

The domain/contracts layer contains business identities, result types and ports.
It must not import Flask, HTTP routing, concrete filesystems, SQLite, federation
transports or cryptographic implementations.

Concrete adapters may depend on V1 modules during migration. V1 modules are not
allowed to become new V2 contracts by accident.

### Domain

Pure business rules and stable logical identities. A logical object identity is
not a filesystem path, filename, content hash or federation peer identifier.

### Storage

Application-facing logical object operations. All new copy, move, rename,
delete and content writes are introduced through a storage port before callers
are migrated. The first adapter will wrap the existing DocumentStore.

### Blob/content store

Physical byte/chunk persistence is separate from the domain ObjectStore.
Physical blob identifiers are opaque and are not public logical identities.

### Crypto

Crypto is an adapter/service boundary. Domain code never selects algorithms,
generates nonces or performs key wrapping directly.

### Metadata

Original metadata, observations, aliases, provenance, trust and verification
state are distinct concepts. Later observations never silently overwrite the
original source record.

### Jobs and events

Long-running work uses persistent idempotent jobs. A job records desired state,
not merely "a sender transmitted bytes". Completion is based on verified target
state. Events describe facts that already happened; they are not mutable jobs.

### Federation

Federation consumes application/storage/job ports. It must not manipulate V2
blob internals or use globally exposed plaintext content hashes as public
authorization identities.

### Audit/history

Audit events contain actor, operation, object, time, source/provenance and an
optional correlation id. Secret payloads are not part of the audit contract.

### API/UI

Flask routes, WebDAV, CLI, desktop/mobile shells and future VFS integrations call
application services. They do not become storage or crypto implementations.

## Allowed dependencies

- domain/contracts: Python standard library only
- application services: domain/contracts and explicit ports
- adapters: contracts plus the concrete technology they adapt
- UI/API: application services and presentation helpers
- recovery tooling: format readers, crypto adapters and storage adapters; no
  dependency on a running Flask application

A V2 module may temporarily call a V1 adapter, but new V2 domain code must not
import `flask`, `app.document_store`, `app.federation_*` or route modules.

## Internal interfaces

The initial stable interfaces live in `app.v2.contracts`:

- `OperationResult` / `OperationError`
- `LogicalObjectId` and `PhysicalBlobId`
- `PersistentFormat`
- `StoragePort`
- `JobRecord` / `JobStorePort`
- `AuditEvent` / `AuditPort`

Interfaces are intentionally minimal. They are extended only by a PR that also
updates tests, migration implications and this document.

## Error/result model

Expected domain and adapter failures cross V2 boundaries as
`OperationResult.failure(...)` with a stable `ErrorCode`. Exceptions remain
appropriate for programming errors and invariant violations.

Retryability is explicit. UI text is not part of the domain error code.

## Transaction boundaries

A mutation is considered committed only after its authoritative persistent state
and required integrity metadata have been atomically persisted.

Audit, cache refresh and optional secondary indexes must not make an already
committed domain mutation appear rolled back. When a secondary system is
required for correctness, it becomes part of the transaction and must fail
before commit.

Multi-resource operations use a persisted job/state machine rather than
pretending multiple files/databases form one local ACID transaction.

## Event and job model

Jobs are persistent, idempotent and restart-safe. Every job has:

- stable job id
- kind
- idempotency key
- explicit state
- bounded payload containing references rather than secret contents
- attempt counter

Terminal states are `succeeded`, `failed` and `cancelled`.

## Persistent format versioning

Every new V2 persistent format has a stable family name and positive integer
version. Readers must reject unknown newer mandatory versions instead of
silently guessing.

Format changes are one of:

1. additive and backwards readable,
2. migrated with an explicit preflight/backup/verify path, or
3. a new side-by-side format.

Physical storage layout is never treated as an implicit format contract.

## Migration rules

Every migration PR documents and tests:

1. preflight,
2. backup or recoverable source preservation,
3. transformation,
4. integrity verification,
5. smoke/read test,
6. completion marker,
7. retry behavior after interruption,
8. restore/rollback path.

Migrations are idempotent or explicitly detect an already-completed state.
Partial failures must not silently delete source data.

## Deprecation rules

Legacy paths remain until replacement behavior, migration and recovery have
passed CI. Deprecation requires:

- replacement interface and adapter,
- call-site migration inventory,
- compatibility window,
- removal criteria,
- recovery instructions.

No legacy persistent format is removed merely because no current UI links to it.

## Security/publication rule

Repository examples, fixtures, docs, tests and PR descriptions use synthetic
data only. Secrets, credentials, private keys, tokens and real operational
identifiers do not belong in V2 diagnostics or examples.

## Initial implementation sequence

1. architecture contract and tests
2. DocumentStore-backed StoragePort adapter
3. separate BlobStore
4. crypto adapter and threat model
5. independent recovery CLI
6. fragmentation/recovery model
7. persistent federation jobs
8. delegation/trusted relay
9. metadata V2
10. VFS
11. file browser migration
12. unified audit port
13. credential vault
14. V1 -> V2 migration
15. legacy cleanup
