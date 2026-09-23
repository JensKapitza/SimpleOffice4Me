# V2 independent encrypted-object recovery

Encrypted V2 content can be inspected, verified and exported without starting
Flask and without the live SimpleOffice user database, ObjectCatalog or runtime
storage password file.

The recovery path needs only:

- the SimpleOffice data root containing
  `.simpleoffice-v2/encrypted-blob-store`,
- the portable recovery bundle for the dedicated `v2-local-storage` profile,
- the matching offline recovery-key file.

The raw recovery key and raw master key are never accepted as command-line
arguments and are never printed. The recovery bundle and recovery-key file must
remain outside the SimpleOffice data root; the encrypted store cannot supply
its own recovery credentials.

## Commands

Inventory:

```text
python -m app.v2.recovery_cli --root /srv/simpleoffice/documents \
  encrypted-recovery-inventory \
  --bundle /offline/storage-recovery.json \
  --recovery-key-file /offline/storage-recovery.key
```

Verify every encrypted version:

```text
python -m app.v2.recovery_cli --root /srv/simpleoffice/documents \
  encrypted-recovery-verify \
  --bundle /offline/storage-recovery.json \
  --recovery-key-file /offline/storage-recovery.key
```

A single object/version can be selected with `--object-id` and optional
`--version-id`.

Export is read-only by default. Without `--apply` the command verifies the
requested object and does not create a plaintext file:

```text
python -m app.v2.recovery_cli --root /srv/simpleoffice/documents \
  encrypted-recovery-export \
  --bundle /offline/storage-recovery.json \
  --recovery-key-file /offline/storage-recovery.key \
  --object-id OBJECT_ID \
  --version-id VERSION_ID \
  --output /recovery/export.bin
```

Add `--apply` only after the verification result is acceptable.

## Security properties

The recovery bundle is checked against the dedicated
`v2-local-storage` profile identity before a master key is accepted. A bundle
from another master-key profile is rejected.

Encrypted chunks are decrypted and authenticated one chunk at a time. Export
writes into a restrictive same-directory temporary file and publishes it with
an atomic replace only after the complete object, encrypted footer, byte count
and whole-content SHA-256 have verified successfully. A damaged late chunk
therefore never publishes a partial recovery file.

Recovered plaintext must be exported outside the SimpleOffice data root. This
prevents a recovery operation from silently introducing plaintext into the
encrypted storage tree.

The encrypted store's public manifests are sufficient to discover and verify
versions. Recovery does not depend on `catalog.sqlite3`, legacy document
metadata or the normal runtime unlock environment variable.

## Scope

This recovery path covers the encrypted blob backend itself. k-of-n fragment
reconstruction remains a separate portable recovery layer and can be used to
restore missing encrypted-store material before object-level verification.
