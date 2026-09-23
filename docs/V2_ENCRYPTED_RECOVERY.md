# Independent V2 encrypted-blob recovery

Encrypted V2 blob recovery can run through `simpleoffice-v2-recovery` without
Flask, the normal user database or an unlocked web session.

The recovery operator needs:

- the SimpleOffice data root containing the encrypted V2 blob store,
- the portable master-key recovery bundle,
- the separate offline recovery-key file,
- the logical object ID,
- optionally a specific blob version ID.

The recovery key itself is never accepted as a command-line value and the raw
master key is never printed or exported.

## Inspect the store

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  encrypted-inventory \
  --recovery-bundle /media/offline/simpleoffice-v2-recovery.json \
  --recovery-key-file /media/offline/simpleoffice-v2-recovery.key
```

This reports physical encrypted-store inventory, authenticates the supplied
recovery material and scans encrypted version manifests directly. Recoverable
object/version IDs and the current-pointer flag are therefore discoverable even
when the normal application database is unavailable. The version index is
paginated with `--offset` and `--limit` (default 1000, maximum 10000). It
does not modify the store.

## Verify an object

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  encrypted-verify \
  --recovery-bundle /media/offline/simpleoffice-v2-recovery.json \
  --recovery-key-file /media/offline/simpleoffice-v2-recovery.key \
  --object-id OBJECT_ID \
  --version-id VERSION_ID
```

Omit `--version-id` to use the encrypted store's current pointer.

Verification decrypts chunks incrementally, verifies each AEAD record, verifies
the authenticated encrypted footer, total size, chunk count and whole plaintext
digest, but does not materialize the complete plaintext in memory.

## Export recovered plaintext

Export is read-only by default. A command without `--apply` verifies the
object and reports what would be recovered.

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  encrypted-export \
  --recovery-bundle /media/offline/simpleoffice-v2-recovery.json \
  --recovery-key-file /media/offline/simpleoffice-v2-recovery.key \
  --object-id OBJECT_ID \
  --version-id VERSION_ID \
  --output /media/recovery/recovered-file.bin
```

To publish the recovered plaintext:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  encrypted-export \
  --recovery-bundle /media/offline/simpleoffice-v2-recovery.json \
  --recovery-key-file /media/offline/simpleoffice-v2-recovery.key \
  --object-id OBJECT_ID \
  --version-id VERSION_ID \
  --output /media/recovery/recovered-file.bin \
  --apply
```

The output is streamed into a private mode-0600 temporary file. The final file
is atomically published only after chunk authentication and final footer
integrity verification succeed. If recovery fails, an existing output is not
replaced.

Plaintext recovery output is deliberately rejected inside the SimpleOffice data
root. This prevents an offline recovery command from overwriting encrypted
store metadata or managed production files. Recover to a separate location,
inspect the result and then re-import it through the normal storage boundary if
needed.

Use `--overwrite` only when replacement of an existing regular output file is
intentional.

## Damage behavior

A corrupt, missing, reordered or substituted ciphertext chunk causes recovery
to fail closed. A damaged authenticated footer also prevents publication.

This command recovers encrypted blob versions. Erasure-coded fragment recovery
remains available through the separate `fragment-assess` and
`fragment-recover` commands. Combining encrypted-blob discovery with remote
peer fragment search remains a later federation/recovery step.
