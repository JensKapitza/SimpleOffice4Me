"""Command-line recovery entrypoint for V2 stores.

Read-only commands are the default. Mutating cleanup/export operations require
an explicit --apply switch.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .cutover import cutover_status, prepare_shadow, return_to_v1
from .recovery import RecoveryService
from .migration import build_migration_plan, create_migration_backup, inspect_migration, restore_migration_backup, transfer_legacy_documents, verify_migration_transfer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="simpleoffice-v2-recovery")
    parser.add_argument("--root", required=True, help="SimpleOffice document root")
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
    shadow = sub.add_parser("storage-shadow", help="Prepare or enter verified V2 shadow mode")
    shadow.add_argument("--apply", action="store_true")
    shadow.add_argument(
        "--acknowledge-local-plaintext",
        action="store_true",
        help="Explicitly acknowledge that V2 local blob storage is not encrypted at rest",
    )
    legacy = sub.add_parser("storage-v1", help="Return explicitly to V1 compatibility mode")
    legacy.add_argument("--apply", action="store_true")

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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

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
        result = cutover_status(args.root)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["verification_ready"] else 2

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
