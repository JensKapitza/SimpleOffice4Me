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

## Authorized peer availability search

Known federation peers can check whether this instance currently holds selected
ciphertext chunks from a portable descriptor without receiving plaintext or key
material.

The endpoint is:

```text
POST /federation/v1/recovery/encrypted/availability
```

It is intentionally stricter than the historical SOFP blob endpoints. A request
must pass all of these checks:

1. normal receiver-side Federation Bearer authentication;
2. peer-bound HMAC authentication over peer ID, method, path, exact request
   body, timestamp and one-time nonce;
3. an effective V2 `READ` capability whose subject is that authenticated peer
   and whose object reference is exactly
   `recovery:<descriptor_id>`;
4. the local Federation `storage` policy, including explicit blocks,
   object-specific blocks and configured trust requirements;
5. the persistent per-peer query rate limit.

The capability is bound to the descriptor ID, which itself binds the recovery
profile hash, object ID, version ID and encrypted-manifest digest. A peer cannot
replace the manifest with arbitrary physical chunk IDs while reusing the same
grant.

Availability requests are bounded to 256 selected chunks. The response contains
only descriptor/object/version IDs plus requested, available and missing chunk
indexes. It does not return physical chunk IDs, filesystem paths, keys or
plaintext hashes.

Each accepted/denied query is recorded in the Federation event log without
physical chunk identifiers or secret payloads. Replay of a signed request is
rejected through the existing persistent Federation nonce store.

## Descriptor-scoped ciphertext transfer

Authorized peers can now transfer the ciphertext referenced by that exact
descriptor without opening a general physical-blob API.

Two peer-signed POST endpoints are available:

- `/federation/v1/recovery/encrypted/chunk` requires an effective `READ`
  grant for `recovery:<descriptor_id>` and returns exactly one verified
  ciphertext chunk.
- `/federation/v1/recovery/encrypted/store` requires an effective `STORE`
  grant for the same descriptor scope and accepts exactly one ciphertext chunk.

Both endpoints repeat the normal Federation Bearer check, peer-bound request
signature/replay protection, storage policy evaluation, persistent rate limit
and audit event. Responses and events use only descriptor IDs and chunk indexes;
physical chunk IDs and filesystem paths are not exposed as the transport API.

Received foreign ciphertext is kept below the private
`.simpleoffice-v2/recovery-fragments/<descriptor_id>/` cache. It does not become
an authoritative local document/blob merely because a peer uploaded it. A
fragment is counted as available only after its size and SHA-256 match the
descriptor exactly. POSIX cache directories are mode 0700 and fragment files
mode 0600.

The client helpers `remote_encrypted_recovery_chunk(...)` and
`remote_store_encrypted_recovery_chunk(...)` sign the exact request body and
verify the returned/confirmed descriptor binding. Recovery search therefore now
has a bounded path from discovery to authorized ciphertext retrieval and
explicit remote storage, while key material and plaintext remain out of the
Federation transport.

## Recovery CLI peer search and fetch

The independent recovery CLI can use the same descriptor-scoped Federation
boundary without starting Flask or unlocking a normal web session.

Query one configured peer for selected ciphertext chunks:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  encrypted-peer-availability \
  --descriptor /media/recovery/object.recovery.json \
  --peer PEER_ID \
  --authorization-ref RECOVERY_GRANT_ID \
  --chunk-index 0 \
  --chunk-index 1
```

Fetch is read-only by default. Without `--apply`, the CLI only confirms that
the selected peer reports the requested chunk as available:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  encrypted-peer-fetch \
  --descriptor /media/recovery/object.recovery.json \
  --peer PEER_ID \
  --authorization-ref RECOVERY_GRANT_ID \
  --chunk-index 0
```

Add `--apply` to fetch the verified ciphertext and cache it below the private
recovery-fragment area. The remote helper verifies descriptor binding, size and
ciphertext SHA-256 before the local cache accepts the chunk. The cached fragment
does not become an authoritative document merely because it was recovered from
a peer.

The peer ID and authorization reference identify already configured Federation
state. Tokens, recovery keys and master keys are not command-line arguments.

## Damage behavior

A corrupt, missing, reordered or substituted ciphertext chunk causes recovery
to fail closed. A damaged authenticated footer also prevents publication.

This command recovers encrypted blob versions. Erasure-coded fragment recovery
remains available through the separate `fragment-assess` and
`fragment-recover` commands. Descriptor-scoped remote availability, verified
ciphertext retrieval and explicit remote storage are implemented through the
authorized Federation recovery boundary described above.
