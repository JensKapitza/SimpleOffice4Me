# V2 independent recovery CLI

`simpleoffice-v2-recovery` reads V2 recovery formats without importing Flask,
application routes or the user database.

## Safe default

Inventory, verification, descriptor checks, fragment assessment and cleanup
previews are read-only. Content export, fragment reconstruction export, stale
staging deletion and orphan deletion require explicit `--apply`.

Examples use synthetic identifiers only:

```text
simpleoffice-v2-recovery --root /srv/example inventory
simpleoffice-v2-recovery --root /srv/example verify
simpleoffice-v2-recovery --root /srv/example describe --object-id document-123
simpleoffice-v2-recovery --root /srv/example export --object-id document-123 --output /safe/export.bin --apply
```

## Blob-store recovery descriptor

The v1 blob descriptor contains only non-secret recovery coordinates and
integrity information:

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

## k-of-n fragment recovery

A portable `simpleoffice-v2-fragments/v1` recovery set can be assessed without
a running SimpleOffice installation and without `--root`. Fragment paths are
supplied explicitly as `INDEX=PATH`; no untrusted physical fragment ID is ever
used as a filesystem path.

Example:

```text
simpleoffice-v2-recovery fragment-assess \
  --descriptor /recovery/recovery.json \
  --fragment 0=/media/a/f0.bin \
  --fragment 3=/media/b/f3.bin \
  --fragment 5=/media/c/f5.bin
```

The command reports every expected index as `present_valid`,
`present_corrupt` or `missing`. Only fragments whose size and SHA-256 match
the recovery descriptor count toward `k`.

Reconstruction is read-only by default. Writing recovered content requires
`--apply`:

```text
simpleoffice-v2-recovery fragment-recover \
  --descriptor /recovery/recovery.json \
  --fragment 0=/media/a/f0.bin \
  --fragment 3=/media/b/f3.bin \
  --fragment 5=/media/c/f5.bin \
  --output /safe/recovered.bin \
  --apply
```

The codec name/version in the descriptor selects the registered decoder. The
current production adapter is `zfec/1` and is installed with the optional
`erasure` extra. Unknown codecs fail closed. The reconstructed payload is
written only after fragment integrity, k-of-n reconstruction, final byte length
and whole-content SHA-256 all verify.

## Damaged stores

`inventory` reports missing chunks, orphan chunks, invalid manifests and stale
staging transactions. `verify` independently reconstructs and hashes every
selected blob-store version.

Fragment assessment is deliberately separate from deduplication. Recovery
integrity hashes are not public peer-discovery or federation-existence
identifiers.
