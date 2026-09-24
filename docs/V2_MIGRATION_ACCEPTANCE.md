# V2 migration acceptance

Phase 14 is completed through an explicit, restartable sequence:

1. migration preflight,
2. source backup,
3. deterministic migration plan,
4. V1 -> V2 transfer,
5. full integrity verification,
6. final smoke test,
7. persisted completion marker,
8. documented restore path.

The existing commands continue to own steps 1-5 and 8. The acceptance step adds
the final smoke test and completion marker.

## Smoke test

Run:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents migration-smoke
```

The command is read-only. It first requires a clean migration-transfer
verification and clean V1/V2 shadow consistency. It then performs one
deterministic physical read from the migrated V2 blob store and checks the read
size and SHA-256 against the migration plan.

Full integrity verification still covers every migrated document; the single
read is an additional runtime smoke check, not a replacement for verification.

## Finalize

Preview:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents migration-finalize
```

The preview writes nothing. A successful preview returns ready state but no
completion marker.

Apply:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  migration-finalize \
  --apply \
  --acknowledge-local-plaintext
```

Applying persists the existing V2 storage-cutover state in verified shadow mode.
That file is the Phase-14 completion marker:

```text
.simpleoffice-v2/storage-cutover.json
```

The marker contains the verified migration fingerprint and timestamp already
used by the authoritative-storage cutover logic. No second competing migration
state file is introduced.

Finalization is idempotent. Re-running it in clean shadow mode verifies the
current fingerprint and reports completion. A dirty shadow state or fingerprint
drift fails closed.

## Rollback / restore

The migration acceptance step does not delete V1 content.

Two distinct rollback paths remain available:

- `storage-v1 --apply` returns the runtime selection to V1 while compatibility
  data is still retained.
- `migration-restore --backup ... --destination ... --apply` reconstructs the
  verified pre-migration backup into a new destination.

The restore command never overwrites an existing destination.

## Encryption boundary

Phase-14 finalization enters the existing local-plaintext shadow mode and
therefore requires explicit acknowledgement. It does not claim full encryption
at rest. Encrypted blob cutover and later plaintext compatibility cleanup remain
separate V2 steps.
