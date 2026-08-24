"""Permission-aware virtual namespace shared by WebDAV and SFTP.

The ordinary filesystem remains the durable source of truth.  This module is
the mandatory authorization boundary for remote filesystem protocols: callers
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
        self.root = self.store.root
        self.administrators = {value.strip() for value in administrators if value.strip()}

    @staticmethod
    def username(actor: str) -> str:
        return actor.split(":", 1)[1] if actor.startswith(("webdav:", "sftp:", "rsync:")) else actor

    @classmethod
    def from_environment(cls, root: str | Path) -> "VirtualFileSystem":
        admins = os.environ.get("SIMPLEOFFICE_DOCUMENT_ADMINS", "")
        return cls(root, admins.split(","))

    def resolve(self, value: str | Path, *, allow_missing: bool = True) -> Path:
        supplied = Path(value)
        if supplied.is_absolute():
            try:
                text = supplied.relative_to(self.root).as_posix()
            except ValueError:
                # WebDAV/SFTP clients address their virtual root as "/".  An
                # absolute client path is therefore interpreted inside this
                # namespace, never as an operating-system path.
                text = supplied.as_posix().lstrip("/")
        else:
            text = str(value).replace("\\", "/").strip("/")
        relative = Path(text or ".")
        if relative.is_absolute() or ".." in relative.parts or "\x00" in text:
            raise ValueError("path must remain inside the virtual filesystem")
        if any(part in {CONTROL_DIR, HISTORY_DIR, POLICY_FILE, ""} for part in relative.parts):
            raise ValueError("path contains a reserved segment")
        candidate = self.root if relative == Path(".") else self.root / relative
        current = self.root
        for part in (() if relative == Path(".") else relative.parts):
            current = current / part
            if current.is_symlink():
                raise ValueError("symbolic links are not available")
        # The root itself has a parent outside the virtual namespace.  For all
        # descendants, resolving the parent catches symlink escapes while still
        # allowing the conventional "." spelling for the root collection.
        boundary = candidate if candidate == self.root else candidate.parent
        try:
            boundary.resolve().relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path must remain inside the virtual filesystem") from exc
        if not allow_missing and not candidate.exists():
            raise FileNotFoundError(text)
        return candidate

    def relative(self, path: str | Path) -> str:
        resolved = self.resolve(path) if not isinstance(path, Path) or not path.is_absolute() else path
        return "." if resolved == self.root else resolved.relative_to(self.root).as_posix()

    def _policy_directories(self, path: Path) -> list[Path]:
        target = path if path.is_dir() else path.parent
        if not target.exists():
            target = target.parent if target != self.root else target
        try:
            relative = target.resolve().relative_to(self.root)
        except ValueError as exc:
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
        # Compatibility boundary: an installation has the historical writable
        # namespace until an administrator explicitly enables an ACL.
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
            if not self.allows(actor, child, "read"):
                continue
            stat = child.stat(follow_symlinks=False)
            result.append(VirtualEntry(
                self.relative(child), child.name, child.is_dir(),
                0 if child.is_dir() else stat.st_size, stat.st_mtime_ns,
            ))
        return result

    def read_bytes(self, actor: str, path: str | Path) -> bytes:
        resource = self.require(actor, path, "read")
        if not resource.is_file() or resource.is_symlink():
            raise FileNotFoundError(self.relative(resource))
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
            document = self.store.get_document(resource)
            return self.store.replace_content(
                document["document_id"], content, actor,
                expected_sha256=expected_sha256 or str(document.get("sha256", "")),
                max_bytes=max_bytes,
            )
        self.require(actor, resource.parent, "write")
        return self.store.create_document_at(
            self.relative(resource), content, actor, max_bytes=max_bytes,
        )

    def mkdir(self, actor: str, path: str | Path) -> Path:
        resource = self.resolve(path)
        self.require(actor, resource.parent, "write")
        return self.store.create_collection(self.relative(resource), actor)

    def remove(self, actor: str, path: str | Path, *, expected_sha256: str = "") -> None:
        resource = self.require(actor, path, "write")
        self.require(actor, resource.parent, "write")
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
        if destination_path.exists():
            if not replace:
                raise FileExistsError(self.relative(destination_path))
            self.require(actor, destination_path, "write")
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
        """Set portable timestamps without exposing ownership or mode changes."""
        resource = self.require(actor, path, "write")
        current = resource.stat(follow_symlinks=False)
        accessed = current.st_atime if atime is None else float(atime)
        modified = current.st_mtime if mtime is None else float(mtime)
        # Keep platform-dependent timestamp conversion away from extreme input.
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
