# V2 independent recovery CLI

`simpleoffice-v2-recovery` reads the V2 blob-store format without importing
Flask, application routes or the user database.

## Safe default

Inventory, verification, descriptor checks and cleanup previews are read-only.
Content export, stale staging deletion and orphan deletion require explicit
`--apply`.

Examples use synthetic identifiers only:

```text
simpleoffice-v2-recovery --root /srv/example inventory
simpleoffice-v2-recovery --root /srv/example verify
simpleoffice-v2-recovery --root /srv/example describe --object-id document-123
simpleoffice-v2-recovery --root /srv/example export --object-id document-123 --output /safe/export.bin --apply
```

## Recovery descriptor

The v1 descriptor contains only non-secret recovery coordinates and integrity
information:

- logical object id
- immutable version id
- blob-format family/version
- expected byte size
- content SHA-256 integrity value
- chunk count
- canonical manifest SHA-256

It contains no master key, password, recovery key or plaintext credential.
Descriptor verification binds these fields to the immutable manifest and then
verifies the reconstructed content.

## Damaged stores

`inventory` reports missing chunks, orphan chunks, invalid manifests and stale
staging transactions. `verify` independently reconstructs and hashes every
selected version.

Erasure coding is intentionally not simulated here. The later fragment/recovery
phase extends the descriptor and recovery service with k-of-n reconstruction
while keeping this read-only-default CLI boundary.
