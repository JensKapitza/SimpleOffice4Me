"""Command-line recovery entrypoint for V2 stores.

Read-only commands are the default. Mutating cleanup/export operations require
an explicit --apply switch.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from .cutover import (
    LOCAL_ENCRYPTED_BLOB,
    activate_v2,
    cutover_status,
    load_cutover_state,
    prepare_shadow,
    return_to_v1,
)
from .encrypted_cutover import encrypted_blob_cutover
from .encrypted_recovery import EncryptedBlobRecoveryService
from .fragment_recovery_io import (
    assessment_to_dict,
    export_recovered_payload,
    load_fragment_specs,
    load_recovery_descriptor,
)
from .fragments import assess_fragments, recover_payload
from .recovery import RecoveryService
from .migration import build_migration_plan, create_migration_backup, inspect_migration, restore_migration_backup, transfer_legacy_documents, verify_migration_transfer
from .master_keys import (
    MasterKeyProfileStore,
    load_master_password_file,
    load_recovery_bundle_file,
    load_recovery_key_file,
    recover_master_key_from_bundle,
    recovery_bundle_info,
)
from .runtime_keys import PASSWORD_FILE_ENV, STORAGE_PROFILE_ID
from .storage_keys import export_storage_recovery_bundle, provision_storage_profile
from .storage_key_rotation import (
    rollback_storage_master_key_rotation,
    rotate_storage_master_key,
    rotation_status,
)
from .zfec_codec import codec_for_plan


_FRAGMENT_COMMANDS = {"fragment-assess", "fragment-recover"}


def _add_fragment_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--descriptor", required=True, help="Portable fragment recovery-set JSON")
    parser.add_argument(
        "--fragment",
        action="append",
        default=[],
        metavar="INDEX=PATH",
        help="Path for one fragment; repeat for every available fragment",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="simpleoffice-v2-recovery")
    parser.add_argument("--root", help="SimpleOffice document root")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("inventory", help="Inspect formats, chunks and recovery state")
    sub.add_parser("migration-preflight", help="Read-only V1/V2 migration readiness check")
    sub.add_parser("migration-plan", help="Build a read-only V1 document migration plan")
    backup = sub.add_parser("migration-backup", help="Create a source backup before migration")
    backup.add_argument("--destination", required=True)
    backup.add_argument("--apply", action="store_true")
    transfer = sub.add_parser("migration-transfer", help="Copy verified V1 documents into the V2 blob store")
    transfer.add_argument("--backup", required=True)
    transfer.add_argument("--apply", action="store_true")
    restore = sub.add_parser("migration-restore", help="Restore a migration backup into a new destination")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--destination", required=True)
    restore.add_argument("--apply", action="store_true")
    sub.add_parser("migration-verify", help="Verify V1/V2 content transfer without writing")
    sub.add_parser("storage-cutover-status", help="Show explicit V2 storage cutover state and verification")

    key_init = sub.add_parser(
        "storage-key-init",
        help="Create the dedicated V2 storage master-key profile and offline recovery material",
    )
    key_init.add_argument("--password-file", required=True)
    key_init.add_argument("--recovery-key-output", required=True)
    key_init.add_argument("--recovery-bundle-output", required=True)
    key_init.add_argument("--apply", action="store_true")

    key_bundle = sub.add_parser(
        "storage-key-export-bundle",
        help="Export a fresh protected recovery bundle for the V2 storage profile",
    )
    key_bundle.add_argument("--output", required=True)
    key_bundle.add_argument("--apply", action="store_true")

    key_rotation_status = sub.add_parser(
        "storage-key-rotation-status",
        help="Show whether an encrypted-storage master-key rotation is pending",
    )

    key_rotate = sub.add_parser(
        "storage-key-rotate",
        help="Start or resume encrypted-storage master-key rotation without re-encrypting payload chunks",
    )
    key_rotate.add_argument("--password-file", required=True)
    key_rotate.add_argument("--recovery-key-output", required=True)
    key_rotate.add_argument("--recovery-bundle-output", required=True)
    key_rotate.add_argument("--apply", action="store_true")
    key_rotate.add_argument(
        "--acknowledge-maintenance-window",
        action="store_true",
        help="Confirm all normal application writers are stopped during key rotation",
    )

    key_rollback = sub.add_parser(
        "storage-key-rotation-rollback",
        help="Rollback a pending storage master-key rotation before profile commit",
    )
    key_rollback.add_argument("--password-file", required=True)
    key_rollback.add_argument("--apply", action="store_true")
    key_rollback.add_argument(
        "--acknowledge-maintenance-window",
        action="store_true",
        help="Confirm all normal application writers are stopped during rotation rollback",
    )

    encrypted = sub.add_parser(
        "storage-encrypted",
        help="Verify or migrate authoritative V2 blobs to the encrypted runtime backend",
    )
    encrypted.add_argument("--apply", action="store_true")
    encrypted.add_argument(
        "--acknowledge-maintenance-window",
        action="store_true",
        help="Confirm normal application writers are stopped for this offline cutover",
    )

    shadow = sub.add_parser("storage-shadow", help="Prepare or enter verified V2 shadow mode")
    shadow.add_argument("--apply", action="store_true")
    activate = sub.add_parser("storage-v2", help="Promote verified shadow mode to authoritative V2 storage")
    activate.add_argument("--apply", action="store_true")
    activate.add_argument("--acknowledge-local-plaintext", action="store_true")
    shadow.add_argument(
        "--acknowledge-local-plaintext",
        action="store_true",
        help="Explicitly acknowledge that V2 local blob storage is not encrypted at rest",
    )
    legacy = sub.add_parser("storage-v1", help="Return explicitly to V1 compatibility mode")
    legacy.add_argument("--apply", action="store_true")

    fragment_assess = sub.add_parser(
        "fragment-assess",
        help="Assess valid, corrupt and missing k-of-n recovery fragments",
    )
    _add_fragment_inputs(fragment_assess)
    fragment_recover = sub.add_parser(
        "fragment-recover",
        help="Reconstruct and export content from a verified k-of-n recovery set",
    )
    _add_fragment_inputs(fragment_recover)
    fragment_recover.add_argument("--output", required=True)
    fragment_recover.add_argument("--overwrite", action="store_true")
    fragment_recover.add_argument("--apply", action="store_true")

    master_check = sub.add_parser(
        "master-key-recovery-check",
        help="Verify an offline V2 master-key recovery bundle without exporting the key",
    )
    master_check.add_argument("--bundle", required=True)
    master_check.add_argument(
        "--recovery-key-file",
        required=True,
        help="File containing the base64url recovery key; the key itself is never accepted as an argument",
    )

    def add_encrypted_recovery_material(command: argparse.ArgumentParser) -> None:
        command.add_argument("--recovery-bundle", required=True)
        command.add_argument(
            "--recovery-key-file",
            required=True,
            help="File containing the offline recovery key; the key itself is never accepted as an argument",
        )

    encrypted_inventory = sub.add_parser(
        "encrypted-inventory",
        help="Inspect the encrypted V2 blob store using portable recovery material",
    )
    add_encrypted_recovery_material(encrypted_inventory)
    encrypted_inventory.add_argument("--offset", type=int, default=0)
    encrypted_inventory.add_argument("--limit", type=int, default=1000)

    encrypted_verify = sub.add_parser(
        "encrypted-verify",
        help="Verify one encrypted V2 object/version without the application database",
    )
    add_encrypted_recovery_material(encrypted_verify)
    encrypted_verify.add_argument("--object-id", required=True)
    encrypted_verify.add_argument("--version-id", default="")

    encrypted_export = sub.add_parser(
        "encrypted-export",
        help="Atomically export one verified encrypted V2 object/version",
    )
    add_encrypted_recovery_material(encrypted_export)
    encrypted_export.add_argument("--object-id", required=True)
    encrypted_export.add_argument("--version-id", default="")
    encrypted_export.add_argument("--output", required=True)
    encrypted_export.add_argument("--overwrite", action="store_true")
    encrypted_export.add_argument("--apply", action="store_true")

    verify = sub.add_parser("verify", help="Verify one object/version or all versions")
    verify.add_argument("--object-id", default="")
    verify.add_argument("--version-id", default="")

    describe = sub.add_parser("describe", help="Create a non-secret recovery descriptor")
    describe.add_argument("--object-id", required=True)
    describe.add_argument("--version-id", default="")
    describe.add_argument("--output", default="")

    check = sub.add_parser("check-descriptor", help="Validate a recovery descriptor")
    check.add_argument("descriptor")

    export = sub.add_parser("export", help="Export verified content")
    export.add_argument("--object-id", required=True)
    export.add_argument("--version-id", default="")
    export.add_argument("--output", required=True)
    export.add_argument("--overwrite", action="store_true")
    export.add_argument("--apply", action="store_true")

    staging = sub.add_parser("cleanup-staging", help="Remove stale unfinished transactions")
    staging.add_argument("--minimum-age-seconds", type=int, default=3600)
    staging.add_argument("--apply", action="store_true")

    gc = sub.add_parser("gc-orphans", help="List or remove unreferenced chunks")
    gc.add_argument("--minimum-age-seconds", type=int, default=86400)
    gc.add_argument("--apply", action="store_true")
    return parser


def _run_fragment_command(args: argparse.Namespace) -> int:
    recovery_set = load_recovery_descriptor(args.descriptor)
    available = load_fragment_specs(recovery_set, args.fragment)
    assessment = assess_fragments(recovery_set, available)
    report = assessment_to_dict(assessment)

    if args.command == "fragment-assess":
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if assessment.recoverable else 2

    if not assessment.recoverable:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2
    if not args.apply:
        print(json.dumps(report, indent=2, sort_keys=True))
        print("read-only mode: add --apply to export reconstructed content")
        return 3

    codec = codec_for_plan(recovery_set.plan)
    payload = recover_payload(recovery_set, available, codec)
    target = export_recovered_payload(payload, args.output, overwrite=bool(args.overwrite))
    print(str(target))
    return 0


def _run_master_key_recovery_check(args: argparse.Namespace) -> int:
    try:
        bundle = load_recovery_bundle_file(args.bundle)
        recovery_key = load_recovery_key_file(args.recovery_key_file)
        info = recovery_bundle_info(bundle)
        master_key = recover_master_key_from_bundle(bundle, recovery_key)
        del master_key
    except (OSError, ValueError):
        print(json.dumps({
            "valid": False,
            "error": "recovery inputs are invalid or authentication failed",
        }, indent=2, sort_keys=True))
        return 2

    print(json.dumps({
        **info,
        "valid": True,
        "master_key_exported": False,
    }, indent=2, sort_keys=True))
    return 0


def _run_encrypted_recovery(args: argparse.Namespace) -> int:
    try:
        service = EncryptedBlobRecoveryService(
            args.root,
            args.recovery_bundle,
            args.recovery_key_file,
        )
    except (OSError, RuntimeError, ValueError):
        print(json.dumps({
            "valid": False,
            "error": "encrypted recovery material is invalid or authentication failed",
        }, indent=2, sort_keys=True))
        return 2

    try:
        if args.command == "encrypted-inventory":
            print(json.dumps(
                service.inventory(offset=args.offset, limit=args.limit),
                indent=2,
                sort_keys=True,
            ))
            return 0
        if args.command == "encrypted-verify":
            report = service.verify(
                args.object_id,
                version_id=args.version_id or None,
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        if args.command == "encrypted-export":
            if not args.apply:
                report = service.verify(
                    args.object_id,
                    version_id=args.version_id or None,
                )
                print(json.dumps(report, indent=2, sort_keys=True))
                print("read-only mode: add --apply to export verified plaintext")
                return 3
            report = service.export(
                args.object_id,
                args.output,
                version_id=args.version_id or None,
                overwrite=bool(args.overwrite),
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
    except (FileNotFoundError, OSError, RuntimeError, TypeError, ValueError):
        print(json.dumps({
            "valid": False,
            "error": "encrypted object verification or export failed",
        }, indent=2, sort_keys=True))
        return 2
    return 1


def _storage_master_key(root: str | Path) -> bytes:
    configured = str(os.environ.get(PASSWORD_FILE_ENV) or "").strip()
    if not configured:
        raise ValueError(
            f"{PASSWORD_FILE_ENV} must point at the external protected storage password file"
        )
    password = load_master_password_file(configured, forbidden_root=root)
    profile = MasterKeyProfileStore(root, "v2-storage-cutover")
    return profile.unlock_with_password(STORAGE_PROFILE_ID, password)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.command in _FRAGMENT_COMMANDS:
        return _run_fragment_command(args)
    if args.command == "master-key-recovery-check":
        return _run_master_key_recovery_check(args)
    if not args.root:
        parser.error("--root is required for this command")
    if args.command in {"encrypted-inventory", "encrypted-verify", "encrypted-export"}:
        return _run_encrypted_recovery(args)

    if args.command == "migration-preflight":
        result = inspect_migration(args.root)
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return 0 if result.ready else 2

    if args.command == "migration-plan":
        result = build_migration_plan(args.root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ready"] else 2

    if args.command == "migration-backup":
        if not args.apply:
            print("read-only mode: add --apply to create the migration backup")
            return 3
        result = create_migration_backup(args.root, args.destination)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "migration-transfer":
        if not args.apply:
            print("read-only mode: add --apply to copy verified V1 content")
            return 3
        result = transfer_legacy_documents(args.root, args.backup)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "migration-restore":
        if not args.apply:
            print("read-only mode: add --apply to restore the migration backup")
            return 3
        result = restore_migration_backup(args.backup, args.destination)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "migration-verify":
        result = verify_migration_transfer(args.root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ready"] else 2

    if args.command == "storage-cutover-status":
        state = load_cutover_state(args.root)
        if state.mode == "v2" and state.protection_mode == LOCAL_ENCRYPTED_BLOB:
            try:
                master_key = _storage_master_key(args.root)
                result = cutover_status(args.root, master_key=master_key)
                result["encrypted_backend_verification"] = encrypted_blob_cutover(
                    args.root,
                    master_key,
                    apply=False,
                )
                del master_key
            except (OSError, RuntimeError, ValueError) as exc:
                result = cutover_status(args.root)
                result["encrypted_backend_verification"] = {
                    "ready": False,
                    "blockers": [str(exc)],
                }
        else:
            result = cutover_status(args.root)
        print(json.dumps(result, indent=2, sort_keys=True))
        encrypted_ready = result.get("encrypted_backend_verification", {}).get("ready", True)
        return 0 if result["verification_ready"] and encrypted_ready else 2

    if args.command == "storage-key-init":
        if not args.apply:
            print("read-only mode: add --apply to create the V2 storage key profile")
            return 3
        result = provision_storage_profile(
            args.root,
            password_file=args.password_file,
            recovery_key_output=args.recovery_key_output,
            recovery_bundle_output=args.recovery_bundle_output,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "storage-key-export-bundle":
        if not args.apply:
            print("read-only mode: add --apply to export the recovery bundle")
            return 3
        result = export_storage_recovery_bundle(args.root, args.output)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    if args.command == "storage-key-rotation-status":
        print(json.dumps(rotation_status(args.root), indent=2, sort_keys=True))
        return 0

    if args.command == "storage-key-rotate":
        if args.apply and not args.acknowledge_maintenance_window:
            print(
                "refusing write: stop normal application writers and add "
                "--acknowledge-maintenance-window"
            )
            return 3
        try:
            result = rotate_storage_master_key(
                args.root,
                password_file=args.password_file,
                recovery_key_output=args.recovery_key_output,
                recovery_bundle_output=args.recovery_bundle_output,
                apply=bool(args.apply),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, indent=2, sort_keys=True))
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if args.apply else 3

    if args.command == "storage-key-rotation-rollback":
        if args.apply and not args.acknowledge_maintenance_window:
            print(
                "refusing write: stop normal application writers and add "
                "--acknowledge-maintenance-window"
            )
            return 3
        try:
            result = rollback_storage_master_key_rotation(
                args.root,
                password_file=args.password_file,
                apply=bool(args.apply),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}, indent=2, sort_keys=True))
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if args.apply else 3

    if args.command == "storage-encrypted":
        if args.apply and not args.acknowledge_maintenance_window:
            print(
                "refusing write: stop normal application writers and add "
                "--acknowledge-maintenance-window"
            )
            return 3
        master_key = _storage_master_key(args.root)
        try:
            result = encrypted_blob_cutover(
                args.root,
                master_key,
                apply=bool(args.apply),
            )
        finally:
            del master_key
        print(json.dumps(result, indent=2, sort_keys=True))
        if args.apply:
            return 0 if result["ready"] and result["encrypted_blob_backend_active"] else 2
        return 0 if result["ready"] else 3

    if args.command == "storage-shadow":
        if args.apply and not args.acknowledge_local_plaintext:
            print("refusing write: add --acknowledge-local-plaintext for the current local plaintext V2 store")
            return 3
        result = prepare_shadow(
            args.root,
            apply=bool(args.apply),
            acknowledge_local_plaintext=bool(args.acknowledge_local_plaintext),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if args.apply else 3

    if args.command == "storage-v2":
        if args.apply and not args.acknowledge_local_plaintext:
            print("refusing write: add --acknowledge-local-plaintext for the current local plaintext V2 store")
            return 3
        result = activate_v2(
            args.root,
            apply=bool(args.apply),
            acknowledge_local_plaintext=bool(args.acknowledge_local_plaintext),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if args.apply else 3

    if args.command == "storage-v1":
        result = return_to_v1(args.root, apply=bool(args.apply))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if args.apply else 3

    service = RecoveryService(args.root)

    if args.command == "inventory":
        print(json.dumps(service.inventory(), indent=2, sort_keys=True))
        return 0

    if args.command == "verify":
        if args.object_id:
            result = service.verify(args.object_id, args.version_id or None)
            print(json.dumps(result.__dict__, indent=2, sort_keys=True))
            return 0 if result.valid else 2
        results = service.verify_all()
        print(json.dumps([item.__dict__ for item in results], indent=2, sort_keys=True))
        return 0 if all(item.valid for item in results) else 2

    if args.command == "describe":
        descriptor = service.descriptor(args.object_id, args.version_id or None)
        payload = json.dumps(descriptor, indent=2, sort_keys=True) + "\n"
        if args.output:
            target = Path(args.output).expanduser().resolve()
            if target.exists():
                raise FileExistsError("descriptor output already exists")
            target.write_text(payload, encoding="utf-8")
        else:
            print(payload, end="")
        return 0

    if args.command == "check-descriptor":
        descriptor = json.loads(Path(args.descriptor).read_text(encoding="utf-8"))
        result = service.verify_descriptor(descriptor)
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))
        return 0 if result.valid else 2

    if args.command == "export":
        if not args.apply:
            print("read-only mode: add --apply to export verified content")
            return 3
        target = service.export(
            args.object_id,
            args.output,
            version_id=args.version_id or None,
            overwrite=args.overwrite,
        )
        print(str(target))
        return 0

    if args.command == "cleanup-staging":
        if not args.apply:
            inventory = service.inventory()
            print(json.dumps({"staging_transactions": inventory["staging_transactions"], "applied": False}, indent=2))
            return 0
        removed = service.store.recover_staging(minimum_age_seconds=args.minimum_age_seconds)
        print(json.dumps({"removed": removed, "applied": True}, indent=2))
        return 0

    if args.command == "gc-orphans":
        removed = service.store.collect_orphans(
            dry_run=not args.apply,
            minimum_age_seconds=args.minimum_age_seconds,
        )
        print(json.dumps({"candidates": removed, "applied": bool(args.apply)}, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
