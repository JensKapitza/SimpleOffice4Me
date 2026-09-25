# SimpleOffice4Me 2.0 release cutover

Date: 2026-09-25

This release cutover turns the accepted V2 architecture into the application
release line **2.0.0**. It does not weaken the Phase-15 legacy-cleanup safety
gate.

## Release boundary

Version 2.0.0 uses the V2 architecture accepted in `docs/V2_ACCEPTANCE.md`:

- V2 BlobStore/ObjectCatalog is the content and logical-namespace authority in
  authoritative V2 mode.
- Storage mutations use `StoragePort`.
- encrypted V2 blob storage, recovery, federation jobs and the V2 security
  boundaries remain unchanged.
- the package metadata in `pyproject.toml` and the legacy `setup.py` path are
  aligned on 2.0.0.

Desktop and Android wrapper builds keep their own platform build/version numbers;
they are not the application release authority.

## Legacy cleanup remains fail-closed

Version 2.0 does **not** mean that retained rollback data may be deleted.

`legacy_cleanup_status(...)` still requires:

1. authoritative V2 mode,
2. encrypted V2 blob protection,
3. no pending storage-key rotation,
4. no production consumer that still requires persistent plaintext document
   projection.

The gate now reports the remaining projection consumers explicitly. No
destructive deletion or bypass is introduced by this release cutover.

## First projection-free consumer

Video transcoding no longer opens the retained DocumentStore plaintext file.
When ffmpeg requires a filesystem path, the source is streamed through
`StoragePort.copy_verified_to()` into a private short-lived temporary file.
The file is yielded only after full-object verification and is removed when the
operation ends.

This removes video transcoding from the legacy projection blocker list.

## Remaining post-2.0 cleanup

Issue #471 continues to track the remaining compatibility window:

- DocumentStore mutation/recovery still persists plaintext document files,
- WebDAV still has direct managed-file path consumers,
- business/rental/photo/contact/replication paths still contain direct document
  file consumers,
- legacy federation transfer still contains a direct managed-file path consumer.

Those paths must move to StoragePort/V2 materialization before the global
`requires_legacy_projection` capability can safely become false.

## Release claim

After this change is merged with all repository gates green, the repository
application release is **SimpleOffice4Me 2.0.0**.

Full data-root encryption-at-rest must still not be claimed until #471 reaches a
clean projection-free cleanup gate and the retained plaintext rollback stores
have been handled by an explicit tested cleanup procedure.
