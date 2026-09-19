# V2 blob/content store format v1

The V2 blob store is introduced side-by-side below `.simpleoffice-v2/blob-store`.
It does not migrate or reinterpret existing DocumentStore data.

## Identity separation

- logical object id: stable domain identity supplied by the application
- version id: random UUID identifying one immutable manifest
- physical chunk id: random UUID identifying one physical chunk representation
- SHA-256: integrity metadata only; never a public storage or authorization id

This prevents the physical namespace from becoming a plaintext-content equality
oracle by construction.

## Commit sequence

1. write and fsync chunks under random physical ids,
2. write immutable version manifest atomically,
3. update the object's current pointer atomically,
4. remove the staging transaction.

A crash before step 2 can leave unreferenced chunks. They are not deleted during
the failed write. Inventory/GC later treats them as orphan candidates and uses an
age grace period.

## Integrity

Every manifest records chunk size and SHA-256 plus a whole-content SHA-256.
Reads verify chunk order, size, per-chunk digest, total size and whole-content
digest. Missing or modified chunks fail closed.

## Garbage collection

GC only considers chunks unreferenced by **all retained version manifests**.
Default operation is dry-run. Automatic age-free deletion is intentionally not
part of format v1.

## Recovery

`inventory()` is read-only and reports invalid manifests, missing chunks,
orphan chunks and unfinished staging transactions. Stale staging cleanup is a
separate explicit operation. The independent recovery CLI is added in the next
phase and will consume this documented format without Flask.
