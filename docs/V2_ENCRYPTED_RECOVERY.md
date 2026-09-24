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

## Portable encrypted recovery descriptor

A verified encrypted blob version can be exported as a self-describing recovery
descriptor. The descriptor is independent of the normal application database
and contains the authenticated encrypted manifest plus normalized ciphertext
chunk references for later peer/fragment discovery.

To inspect the descriptor on stdout without writing a file:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  encrypted-describe \
  --recovery-bundle /media/offline/simpleoffice-v2-recovery.json \
  --recovery-key-file /media/offline/simpleoffice-v2-recovery.key \
  --object-id OBJECT_ID \
  --version-id VERSION_ID
```

To write a portable descriptor:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  encrypted-describe \
  --recovery-bundle /media/offline/simpleoffice-v2-recovery.json \
  --recovery-key-file /media/offline/simpleoffice-v2-recovery.key \
  --object-id OBJECT_ID \
  --version-id VERSION_ID \
  --output /media/recovery/object.recovery.json \
  --apply
```

The output file is mode 0600 on POSIX systems and is rejected inside the
managed SimpleOffice data root. Existing files are not replaced unless
`--overwrite` is supplied.

The descriptor contains:

- descriptor format/version and a stable descriptor ID,
- logical object and encrypted blob version IDs,
- the recovery-profile hash used to select the matching recovery material,
- the complete encrypted blob manifest,
- a SHA-256 digest of that manifest,
- normalized encrypted chunk IDs, sizes and ciphertext digests.

It deliberately does **not** add a plaintext content hash, raw master key,
recovery key or decrypted footer metadata. The plaintext digest remains inside
the authenticated encrypted footer.

A descriptor can be structurally checked later without the SimpleOffice root:

```bash
simpleoffice-v2-recovery encrypted-check-descriptor \
  /media/recovery/object.recovery.json
```

This check validates format and internal cross-bindings. Final authenticity is
still established only when the embedded manifest/chunks are opened with the
matching recovered master key and the AEAD/footer verification succeeds.

## Damage behavior

A corrupt, missing, reordered or substituted ciphertext chunk causes recovery
to fail closed. A damaged authenticated footer also prevents publication.

This command recovers encrypted blob versions. Erasure-coded fragment recovery
remains available through the separate `fragment-assess` and
`fragment-recover` commands. The portable encrypted recovery descriptor now supplies the self-describing
ciphertext references needed by a future authorized remote peer-fragment search.
The network search/authorization protocol itself remains a separate
federation/recovery step.
