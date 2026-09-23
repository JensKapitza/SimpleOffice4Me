"""Runtime unlock provider for the V2 encrypted blob backend.

The service password is never stored in the SimpleOffice data root or accepted
on a command line. Operators point SIMPLEOFFICE_V2_STORAGE_PASSWORD_FILE at a
protected external secret file. The derived master key is cached only in this
process and invalidated when either the password file or key-profile generation
changes.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from .master_keys import MasterKeyProfileStore, load_master_password_file


STORAGE_PROFILE_ID = "v2-local-storage"
PASSWORD_FILE_ENV = "SIMPLEOFFICE_V2_STORAGE_PASSWORD_FILE"


@dataclass(frozen=True)
class _CachedMasterKey:
    password_path: str
    password_stat: tuple[int, int, int, int, int, int]
    profile_generation: int
    master_key: bytes


class RuntimeStorageKeyProvider:
    """Process-local cache for the already-unlocked storage master key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, _CachedMasterKey] = {}

    @staticmethod
    def _password_path(root: Path) -> Path:
        configured = str(os.environ.get(PASSWORD_FILE_ENV) or "").strip()
        if not configured:
            raise RuntimeError(
                f"encrypted V2 storage requires {PASSWORD_FILE_ENV} to point at a protected password file"
            )
        supplied = Path(configured).expanduser()
        if supplied.is_symlink():
            raise RuntimeError("encrypted V2 storage password file must not be a symlink")
        try:
            resolved = supplied.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("encrypted V2 storage password file is unavailable") from exc
        if resolved == root or root in resolved.parents:
            raise RuntimeError("encrypted V2 storage password file must be outside the SimpleOffice data root")
        return resolved

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int, int, int, int, int]:
        try:
            metadata = path.stat()
        except OSError as exc:
            raise RuntimeError("encrypted V2 storage password file is unavailable") from exc
        return (
            int(getattr(metadata, "st_dev", 0)),
            int(getattr(metadata, "st_ino", 0)),
            int(metadata.st_mtime_ns),
            int(getattr(metadata, "st_ctime_ns", 0)),
            int(metadata.st_size),
            int(metadata.st_mode),
        )

    def master_key(self, root: str | Path) -> bytes:
        data_root = Path(root).expanduser().resolve()
        password_path = self._password_path(data_root)
        fingerprint = self._fingerprint(password_path)
        profile = MasterKeyProfileStore(data_root, "v2-storage-runtime")
        try:
            status = profile.status(STORAGE_PROFILE_ID)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RuntimeError("encrypted V2 storage master-key profile is unavailable") from exc
        generation = int(status["generation"])
        cache_key = str(data_root)

        with self._lock:
            cached = self._cache.get(cache_key)
            if (
                cached is not None
                and cached.password_path == str(password_path)
                and cached.password_stat == fingerprint
                and cached.profile_generation == generation
            ):
                return bytes(cached.master_key)

            try:
                password = load_master_password_file(
                    password_path,
                    forbidden_root=data_root,
                )
                master_key = profile.unlock_with_password(STORAGE_PROFILE_ID, password)
            except (OSError, RuntimeError, ValueError) as exc:
                raise RuntimeError("encrypted V2 storage could not unlock its master key") from exc
            self._cache[cache_key] = _CachedMasterKey(
                password_path=str(password_path),
                password_stat=fingerprint,
                profile_generation=generation,
                master_key=bytes(master_key),
            )
            return bytes(master_key)

    def clear(self, root: str | Path | None = None) -> None:
        """Drop cached plaintext master-key material from this process."""

        with self._lock:
            if root is None:
                self._cache.clear()
                return
            self._cache.pop(str(Path(root).expanduser().resolve()), None)


_PROVIDER = RuntimeStorageKeyProvider()


def runtime_storage_master_key(root: str | Path) -> bytes:
    return _PROVIDER.master_key(root)


def clear_runtime_storage_master_key(root: str | Path | None = None) -> None:
    _PROVIDER.clear(root)
