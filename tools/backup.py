#!/usr/bin/env python3
"""Create and verify portable, integrity-checked SimpleOffice backups."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_NAME = "_simpleoffice_backup_manifest.json"
ARCHIVE_PREFIX = "SimpleOffice4Me"
MAX_MANIFEST_SIZE = 64 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_relative_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} contains an unsafe path")
    parts = value.split("/")
    if value.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"{label} contains an unsafe path")
    return value


def _manifest_files(manifest: object) -> dict[str, dict[str, Any]]:
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError("backup manifest has an unsupported format")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("backup manifest has no file list")
    expected: dict[str, dict[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("backup manifest contains an invalid file entry")
        relative = _safe_relative_path(item.get("path"), "backup manifest")
        size = item.get("size")
        digest = item.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"backup manifest has an invalid size: {relative}")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError(f"backup manifest has an invalid checksum: {relative}")
        if relative in expected:
            raise ValueError("backup manifest contains duplicate paths")
        expected[relative] = item
    return expected


def create_backup(root: Path, destination: Path, allow_other_filesystems: bool = False) -> dict[str, Any]:
    """Create a new atomic tar.gz backup without following symbolic links."""
    root = root.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("document root is not an existing directory")
    if _inside(destination, root):
        raise ValueError("backup destination must be outside the document root")
    if destination.exists():
        raise FileExistsError(f"backup already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        raise FileExistsError(f"incomplete backup already exists: {temporary}")

    root_device = root.stat().st_dev
    files: list[dict[str, Any]] = []
    skipped_symlinks: list[str] = []
    skipped_filesystems: list[str] = []
    try:
        with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT, dereference=False) as archive:
            for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
                current_path = Path(current)
                retained_directories: list[str] = []
                for name in sorted(directories):
                    candidate = current_path / name
                    relative = candidate.relative_to(root).as_posix()
                    _safe_relative_path(relative, "document tree")
                    if candidate.is_symlink():
                        skipped_symlinks.append(relative)
                    elif not allow_other_filesystems and candidate.stat().st_dev != root_device:
                        skipped_filesystems.append(relative)
                    else:
                        retained_directories.append(name)
                directories[:] = retained_directories

                relative_directory = current_path.relative_to(root)
                if relative_directory.parts:
                    archive.add(
                        current_path,
                        arcname=f"{ARCHIVE_PREFIX}/{relative_directory.as_posix()}",
                        recursive=False,
                    )
                for name in sorted(filenames):
                    path = current_path / name
                    relative = path.relative_to(root).as_posix()
                    _safe_relative_path(relative, "document tree")
                    if path.is_symlink():
                        skipped_symlinks.append(relative)
                        continue
                    stat_before = path.stat()
                    if not allow_other_filesystems and stat_before.st_dev != root_device:
                        skipped_filesystems.append(relative)
                        continue
                    digest_before = _sha256(path)
                    archive.add(path, arcname=f"{ARCHIVE_PREFIX}/{relative}", recursive=False)
                    stat_after = path.stat()
                    digest_after = _sha256(path)
                    if (
                        stat_before.st_size != stat_after.st_size
                        or stat_before.st_mtime_ns != stat_after.st_mtime_ns
                        or digest_before != digest_after
                    ):
                        raise RuntimeError(f"file changed while backup was running: {relative}")
                    files.append({"path": relative, "size": stat_after.st_size, "sha256": digest_after})

            manifest = {
                "version": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": str(root),
                "files": files,
                "skipped_symlinks": skipped_symlinks,
                "skipped_filesystems": skipped_filesystems,
            }
            data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            info = tarfile.TarInfo(f"{ARCHIVE_PREFIX}/{MANIFEST_NAME}")
            info.size = len(data)
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(data))
        temporary.replace(destination)
        return manifest
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def verify_backup(backup: Path) -> dict[str, Any]:
    """Verify checksums and reject archive members outside the manifest."""
    backup = backup.expanduser().resolve()
    with tarfile.open(backup, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("backup archive contains duplicate members")
        by_name = {member.name: member for member in members}
        manifest_name = f"{ARCHIVE_PREFIX}/{MANIFEST_NAME}"
        manifest_member = by_name.get(manifest_name)
        if manifest_member is None or not manifest_member.isfile():
            raise ValueError("backup manifest is missing or has the wrong type")
        if manifest_member.size > MAX_MANIFEST_SIZE:
            raise ValueError("backup manifest exceeds the safety limit")
        manifest_file = archive.extractfile(manifest_member)
        if manifest_file is None:
            raise ValueError("backup manifest is unreadable")
        try:
            manifest = json.loads(manifest_file.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("backup manifest is not valid UTF-8 JSON") from exc
        expected = _manifest_files(manifest)

        archived_files: dict[str, tarfile.TarInfo] = {}
        archived_paths: set[str] = set()
        prefix = f"{ARCHIVE_PREFIX}/"
        for member in members:
            if member.name == manifest_name:
                continue
            if not member.name.startswith(prefix):
                raise ValueError(f"backup member is outside the archive root: {member.name}")
            relative = member.name[len(prefix):].rstrip("/")
            _safe_relative_path(relative, "backup archive")
            if relative in archived_paths:
                raise ValueError(f"backup archive contains an ambiguous path: {relative}")
            archived_paths.add(relative)
            if member.isdir():
                continue
            if not member.isfile() or member.issparse():
                raise ValueError(f"backup member has a forbidden type: {relative}")
            if relative not in expected:
                raise ValueError(f"backup contains an unmanifested file: {relative}")
            archived_files[relative] = member

        missing = sorted(set(expected) - set(archived_files))
        if missing:
            raise ValueError(f"backup member is missing: {missing[0]}")
        for relative, item in expected.items():
            member = archived_files[relative]
            if not member.isfile() or member.size != item["size"]:
                raise ValueError(f"backup member has wrong type or size: {relative}")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"backup member is unreadable: {relative}")
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
            if digest.hexdigest() != item["sha256"]:
                raise ValueError(f"checksum mismatch: {relative}")
        return {"files": len(expected), "created_at": manifest.get("created_at"), "valid": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or verify a SimpleOffice4Me backup")
    parser.add_argument("document_root", nargs="?", type=Path)
    parser.add_argument("backup", type=Path)
    parser.add_argument("--allow-other-filesystems", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        result = verify_backup(args.backup)
    else:
        if args.document_root is None:
            parser.error("document_root is required unless --verify-only is used")
        result = create_backup(args.document_root, args.backup, args.allow_other_filesystems)
        result = {
            "files": len(result["files"]),
            "skipped_symlinks": len(result["skipped_symlinks"]),
            "skipped_filesystems": len(result["skipped_filesystems"]),
            "backup": str(args.backup.expanduser().resolve()),
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
