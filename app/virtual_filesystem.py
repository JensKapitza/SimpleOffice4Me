"""Permission-aware virtual namespace shared by WebDAV and SFTP.

The ordinary filesystem remains the durable source of truth. This module is the
mandatory authorization boundary for remote filesystem protocols: callers
address normalized relative paths and never receive the physical storage path
unless the effective folder policy permits the requested operation.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .document_store import (
    CONTROL_DIR,
    HISTORY_DIR,
    POLICY_FILE,
    DocumentStore,
    atomic_json_write,
    utc_now,
)
from .file_lock import exclusive_file_lock
from .safe_paths import normalize_path, resolve_for_write_under, resolve_under


ROLES = {"read": 1, "write": 2, "manage": 3}


@dataclass(frozen=True)
class VirtualEntry:
    path: str
    name: str
    collection: bool
    size: int
    modified_ns: int


class VirtualFileSystem:
    """Resolve paths and enforce inherited per-folder user grants."""

    def __init__(self, root: str | Path, administrators: Iterable[str] = ()):
        self.store = DocumentStore(root)
        self.root = normalize_path(self.store.root, strict=True)
        self.administrators = {value.strip() for value in administrators if value.strip()}

    @staticmethod
    def username(actor: str) -> str:
        return actor.split(":", 1)[1] if actor.startswith(("webdav:", "sftp:", "rsync:")) else actor

    @classmethod
    def from_environment(cls, root: str | Path) -> "VirtualFileSystem":
        admins = os.environ.get("SIMPLEOFFICE_DOCUMENT_ADMINS", "")
        return cls(root, admins.split(","))

    def _virtual_relative(self, value: str | Path) -> Path:
        """Translate a client path to a lexical path below the virtual root."""
        raw = str(value or "").replace("\\", "/")
        supplied = Path(raw)
        if supplied.is_absolute():
            try:
                relative = normalize_path(supplied).relative_to(self.root)
                raw = relative.as_posix()
            except ValueError:
                # DAV/SFTP clients conventionally spell their virtual root with
                # a leading slash. Never interpret it as an OS absolute path.
                raw = raw.lstrip("/")
        raw = raw.strip("/")
        relative = Path(os.path.normpath(raw or "."))
        if relative.is_absolute() or "\x00" in raw or any(part == ".." for part in relative.parts):
            raise ValueError("path must remain inside the virtual filesystem")
        if any(part in {CONTROL_DIR, HISTORY_DIR, POLICY_FILE, ""} for part in relative.parts):
            raise ValueError("path contains a reserved segment")
        return relative

    def resolve(self, value: str | Path, *, allow_missing: bool = True) -> Path:
        """Normalize a remote path and prove containment before filesystem I/O."""
        relative = self._virtual_relative(value)
        lexical = self.root if relative == Path(".") else self.root / relative

        # The namespace does not expose symlinks, even when their destination is
        # technically inside the root. Check every existing lexical component
        # before realpath can collapse it.
        current = self.root
        for part in (() if relative == Path(".") else relative.parts):
            current = current / part
            if current.is_symlink():
                raise ValueError("symbolic links are not available")

        try:
            if allow_missing:
                candidate = resolve_for_write_under(self.root, relative)
            else:
                candidate = resolve_under(self.root, relative, strict=True)
        except (OSError, ValueError) as exc:
            raise ValueError("path must remain inside the virtual filesystem") from exc
        if lexical.exists() and lexical.is_symlink():
            raise ValueError("symbolic links are not available")
        if not allow_missing and not candidate.exists():
            raise FileNotFoundError(relative.as_posix())
        return candidate

    def relative(self, path: str | Path) -> str:
        if isinstance(path, Path) and path.is_absolute():
            try:
                resolved = normalize_path(path).relative_to(self.root)
            except ValueError as exc:
                raise ValueError("path must remain inside the virtual filesystem") from exc
            return "." if not resolved.parts else resolved.as_posix()
        resolved = self.resolve(path)
        return "." if resolved == self.root else resolved.relative_to(self.root).as_posix()

    def _policy_directories(self, path: Path) -> list[Path]:
        target = path if path.is_dir() else path.parent
        if not target.exists():
            target = target.parent if target != self.root else target
        try:
            relative = resolve_under(self.root, target.relative_to(self.root), strict=True).relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise ValueError("path must remain inside the virtual filesystem") from exc
        directories = [self.root]
        current = self.root
        for part in relative.parts:
            current /= part
            directories.append(current)
        return directories

    def role(self, actor: str, path: str | Path) -> str:
        username = self.username(actor).strip()
        if not username:
            return ""
        if username in self.administrators:
            return "manage"
        resource = self.resolve(path)
        enabled = False
        effective: dict[str, str] = {}
        for directory in self._policy_directories(resource):
            policy = self.store._read_json(directory / POLICY_FILE, {})
            if policy.get("access_enabled") is not True:
                continue
            if not enabled or policy.get("inherit") is False:
                effective = {}
            enabled = True
            grants = policy.get("grants", [])
            if not isinstance(grants, list):
                continue
            for grant in grants:
                if not isinstance(grant, dict):
                    continue
                principal = str(grant.get("principal", grant.get("username", ""))).strip()
                role = str(grant.get("role", "")).strip()
                if principal and role in ROLES:
                    effective[principal] = role
        return effective.get(username, "") if enabled else "write"

    def allows(self, actor: str, path: str | Path, required: str = "read") -> bool:
        return ROLES.get(self.role(actor, path), 0) >= ROLES[required]

    def require(self, actor: str, path: str | Path, required: str = "read") -> Path:
        resource = self.resolve(path)
        if not self.allows(actor, resource, required):
            raise PermissionError(f"{required} access denied")
        return resource

    def entries(self, actor: str, path: str | Path = ".") -> list[VirtualEntry]:
        directory = self.require(actor, path, "read")
        if not directory.is_dir() or directory.is_symlink():
            raise NotADirectoryError(self.relative(directory))
        result = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if (
                child.name in {CONTROL_DIR, HISTORY_DIR, POLICY_FILE}
                or child.name.startswith(f"{POLICY_FILE}.")
                or child.name == Path(POLICY_FILE).with_suffix(".lock").name
                or child.is_symlink()
            ):
                continue
            try:
                child = resolve_under(self.root, child.relative_to(self.root), strict=True)
            except (OSError, ValueError):
                continue
            if not self.allows(actor, child, "read"):
                continue
            stat_result = child.stat(follow_symlinks=False)
            result.append(VirtualEntry(
                self.relative(child), child.name, child.is_dir(),
                0 if child.is_dir() else stat_result.st_size, stat_result.st_mtime_ns,
            ))
        return result

    def read_bytes(self, actor: str, path: str | Path) -> bytes:
        resource = self.require(actor, path, "read")
        if not resource.is_file() or resource.is_symlink():
            raise FileNotFoundError(self.relative(resource))
        resource = resolve_under(self.root, resource.relative_to(self.root), strict=True)
        return resource.read_bytes()

    def write_bytes(
        self,
        actor: str,
        path: str | Path,
        content: bytes,
        *,
        expected_sha256: str = "",
        max_bytes: int = 512 * 1024 * 1024,
    ) -> dict[str, Any]:
        resource = self.resolve(path)
        if resource.exists():
            self.require(actor, resource, "write")
            resource = resolve_under(self.root, resource.relative_to(self.root), strict=True)
            document = self.store.get_document(resource)
            return self.store.replace_content(
                document["document_id"], content, actor,
                expected_sha256=expected_sha256 or str(document.get("sha256", "")),
                max_bytes=max_bytes,
            )
        self.require(actor, resource.parent, "write")
        resource = resolve_for_write_under(self.root, resource.relative_to(self.root))
        return self.store.create_document_at(
            self.relative(resource), content, actor, max_bytes=max_bytes,
        )

    def mkdir(self, actor: str, path: str | Path) -> Path:
        resource = self.resolve(path)
        self.require(actor, resource.parent, "write")
        resource = resolve_for_write_under(self.root, resource.relative_to(self.root))
        return self.store.create_collection(self.relative(resource), actor)

    def remove(self, actor: str, path: str | Path, *, expected_sha256: str = "") -> None:
        resource = self.require(actor, path, "write")
        self.require(actor, resource.parent, "write")
        resource = resolve_under(self.root, resource.relative_to(self.root), strict=True)
        if resource.is_dir():
            self.store.delete_empty_collection(self.relative(resource), actor)
        else:
            document = self.store.get_document(resource)
            self.store.soft_delete_document(
                document["document_id"], actor, expected_sha256=expected_sha256,
            )

    def rename(
        self, actor: str, source: str | Path, destination: str | Path,
        *, replace: bool = False,
    ) -> None:
        source_path = self.require(actor, source, "write")
        destination_path = self.resolve(destination)
        self.require(actor, source_path.parent, "write")
        self.require(actor, destination_path.parent, "write")
        source_path = resolve_under(self.root, source_path.relative_to(self.root), strict=True)
        destination_path = resolve_for_write_under(
            self.root, destination_path.relative_to(self.root)
        )
        if destination_path.exists():
            if not replace:
                raise FileExistsError(self.relative(destination_path))
            self.require(actor, destination_path, "write")
            destination_path = resolve_under(
                self.root, destination_path.relative_to(self.root), strict=True
            )
            if source_path.is_dir() or destination_path.is_dir():
                raise ValueError("POSIX replacement is limited to regular files")
            source_document = self.store.get_document(source_path)
            destination_document = self.store.get_document(destination_path)
            self.store.replace_document_via_move(
                source_document["document_id"], destination_document["document_id"], actor,
                expected_source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                expected_destination_sha256=hashlib.sha256(destination_path.read_bytes()).hexdigest(),
            )
            return
        if source_path.is_dir():
            self.store.move_collection(self.relative(source_path), self.relative(destination_path), actor)
        else:
            document = self.store.get_document(source_path)
            self.store.move_document(
                document["document_id"], self.relative(destination_path.parent), actor,
                destination_name=destination_path.name,
            )

    def set_times(
        self, actor: str, path: str | Path, *, atime: float | None = None,
        mtime: float | None = None,
    ) -> None:
        resource = self.require(actor, path, "write")
        resource = resolve_under(self.root, resource.relative_to(self.root), strict=True)
        current = resource.stat(follow_symlinks=False)
        accessed = current.st_atime if atime is None else float(atime)
        modified = current.st_mtime if mtime is None else float(mtime)
        if not 0 <= accessed <= 4102444800 or not 0 <= modified <= 4102444800:
            raise ValueError("timestamp is outside the supported range")
        os.utime(resource, (accessed, modified), follow_symlinks=False)
        details = {
            "path": self.relative(resource), "atime": accessed, "mtime": modified,
            "actor": self.username(actor), "updated_at": utc_now(),
        }
        self.store._event("document_times_updated", details)
        self.store.history.record(
            "document_times_updated", self.username(actor), "documents",
            hashlib.sha256(details["path"].encode()).hexdigest(), details,
        )

    def set_grants(
        self,
        folder: str | Path,
        grants: dict[str, str],
        actor: str,
        *,
        inherit: bool = True,
    ) -> dict[str, Any]:
        target = self.resolve(folder, allow_missing=False)
        if not target.is_dir() or target.is_symlink():
            raise ValueError("folder does not exist")
        if self.username(actor) not in self.administrators and not self.allows(actor, target, "manage"):
            raise PermissionError("manage access denied")
        normalized = [
            {"principal": username.strip(), "role": role}
            for username, role in sorted(grants.items())
            if username.strip() and role in ROLES
        ]
        target = resolve_under(self.root, target.relative_to(self.root), strict=True)
        policy_path = self.store.ensure_folder_policy(target, actor)
        with exclusive_file_lock(policy_path.with_suffix(".lock")):
            policy = self.store._read_json(policy_path, {})
            policy.update({
                "version": max(3, int(policy.get("version", 0) or 0)),
                "access_enabled": True,
                "inherit": bool(inherit),
                "grants": normalized,
                "access_updated_at": utc_now(),
                "access_updated_by": self.username(actor),
            })
            atomic_json_write(policy_path, policy)
        details = {
            "folder": self.relative(target), "folder_id": policy["folder_id"],
            "inherit": bool(inherit), "grants": normalized,
            "actor": self.username(actor), "updated_at": policy["access_updated_at"],
        }
        self.store._event("folder_access_updated", details)
        self.store.history.record(
            "folder_access_updated", self.username(actor), "policies",
            hashlib.sha256(str(policy["folder_id"]).encode()).hexdigest(), details,
        )
        return details

    def access_policy(self, folder: str | Path) -> dict[str, Any]:
        target = self.resolve(folder, allow_missing=False)
        target = resolve_under(self.root, target.relative_to(self.root), strict=True)
        policy = self.store._read_json(target / POLICY_FILE, {})
        grants = policy.get("grants", []) if policy.get("access_enabled") is True else []
        return {
            "folder": self.relative(target),
            "enabled": policy.get("access_enabled") is True,
            "inherit": policy.get("inherit") is not False,
            "grants": {
                str(item.get("principal", item.get("username", ""))): str(item.get("role", ""))
                for item in grants if isinstance(item, dict) and str(item.get("role", "")) in ROLES
            },
        }
