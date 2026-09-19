# V2 baseline inventory

This inventory records the V1 persistence areas that must remain readable while
V2 is introduced. It is a migration checklist, not a license to couple new V2
code to these layouts.

## Current authoritative or durable areas

| Area | Current implementation / format | V2 treatment |
| --- | --- | --- |
| Documents | managed filesystem plus metadata below `.simpleoffice-meta` | wrap through StoragePort first; no bulk move |
| Document scan index | SQLite cache used by DocumentStore | rebuildable cache; not a logical object identity |
| Revision history | `.simpleoffice-history` snapshots/events/event-chain plus optional Git | preserve/read during migration; expose through AuditPort later |
| Projects / tasks | JSON stores below control directory, with project-task migration to VTODO data | keep migrations idempotent; do not rewrite during storage PR |
| Federation | `federation.sqlite3`, incoming area and transfer records | migrate behind V2 jobs/storage ports before protocol changes |
| Federation content blocks | `federation-blocks.sqlite3` and block cache using SHA-512 manifests | treat as legacy optimization; do not expose hashes as V2 public identities |
| User/application DB | SQLite schema initialized by `app.db` | additive schema migration only until explicit V2 migration PR |
| Settings / contacts / calendar / mail | existing JSON/SQLite stores in their current modules | preserve via adapters; migrate per domain |
| Credentials/password hashes | Argon2id for new password records with legacy verification paths | authentication hashes are not vault encryption; keep separate |
| Replication/archive metadata | existing control-directory JSON/sidecar formats | inventory before backend migration |

## Baseline rules

- V1 data stays in place while adapters are introduced.
- A cache that can be rebuilt is not promoted to V2 source-of-truth status.
- Existing schema/version markers remain authoritative for their V1 readers.
- No V2 PR may silently reinterpret a path, filename or content hash as a stable
  logical object id.
- Before a persistent format is changed, the PR adds a synthetic upgrade test,
  interrupted/retry case where relevant, and a documented restore path.
- Recovery readers must be possible without a running Flask request context.

## Known baseline defect gate

The open RuntimeError reports #242, #245 and #246 are handled before V2 storage
changes because audit-Git failures currently leak into document/project writes.
The V2 sequence assumes that regression is resolved and CI is green first.
