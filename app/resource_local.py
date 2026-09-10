"""Local managed-storage provider for Resource Commander."""
from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path
from typing import BinaryIO, Iterable

from .resource_provider import ProviderCapabilities, ProviderError, ResourceEntry
from .safe_paths import (
    normalize_path,
    resolve_directory_under,
    resolve_file_under,
    resolve_for_write_under,
    resolve_under,
    safe_filename,
)


class LocalResourceProvider:
    provider_id = "self"
    label = "Self"
    capabilities = ProviderCapabilities(
        read=True, write=True, delete=True, move=True, copy=True, folders=True,
        search=True, metadata=True, streaming=True, smart_view=True,
        server_side_copy=True,
    )

    def __init__(self, root: str | Path):
        self.root = normalize_path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = normalize_path(self.root, strict=True)

    def _resolve(self, value: str, *, require_exists: bool = False) -> Path:
        try:
            candidate = resolve_under(self.root, str(value or ""), strict=require_exists)
        except (OSError, ValueError) as exc:
            raise ProviderError("Pfad liegt ausserhalb des verwalteten Bereichs") from exc
        if candidate == self.root and str(value or "").strip().strip("/\\"):
            raise ProviderError("Ungueltiger Pfad")
        return candidate

    def _entry(self, path: Path) -> ResourceEntry:
        try:
            path = resolve_under(self.root, path.relative_to(self.root), strict=True)
        except (OSError, ValueError) as exc:
            raise ProviderError("Ressource liegt ausserhalb des verwalteten Bereichs") from exc
        rel = path.relative_to(self.root).as_posix()
        stat = path.stat(follow_symlinks=False)
        is_dir = path.is_dir() and not path.is_symlink()
        return ResourceEntry(
            resource_id=rel, name=path.name or "Self", kind="folder" if is_dir else "file",
            path=rel, size=0 if is_dir else int(stat.st_size),
            modified=str(int(stat.st_mtime)),
            mime_type="inode/directory" if is_dir else (mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
            provider=self.provider_id,
        )

    def list(self, path: str = "") -> Iterable[ResourceEntry]:
        try:
            folder = resolve_directory_under(self.root, path or ".")
        except (OSError, ValueError) as exc:
            raise ProviderError("Kein sicheres Verzeichnis") from exc
        for item in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
            if item.name == ".simpleoffice-meta" or item.is_symlink():
                continue
            try:
                normalized = resolve_under(self.root, item.relative_to(self.root), strict=True)
            except (OSError, ValueError):
                continue
            yield self._entry(normalized)

    def stat(self, resource_id: str) -> ResourceEntry:
        return self._entry(self._resolve(resource_id, require_exists=True))

    def open(self, resource_id: str) -> BinaryIO:
        try:
            path = resolve_file_under(self.root, resource_id)
        except (OSError, ValueError) as exc:
            raise ProviderError("Ressource ist keine sichere regulaere Datei") from exc
        return path.open("rb")

    def read_range(self, resource_id: str, offset: int, length: int) -> bytes:
        try:
            path = resolve_file_under(self.root, resource_id)
        except (OSError, ValueError) as exc:
            raise ProviderError("Ressource ist keine sichere regulaere Datei") from exc
        with path.open("rb") as source:
            source.seek(max(0, int(offset)))
            return source.read(max(0, min(int(length), 1024 * 1024)))

    def upload(self, path: str, source: BinaryIO, *, name: str, metadata=None) -> ResourceEntry:
        try:
            folder = resolve_directory_under(self.root, path or ".")
        except (OSError, ValueError) as exc:
            raise ProviderError("Ziel ist kein sicheres Verzeichnis") from exc
        safe_name = safe_filename(name, fallback="upload.bin")
        try:
            target = resolve_for_write_under(
                self.root, (folder.relative_to(self.root) / safe_name).as_posix()
            )
        except (OSError, ValueError) as exc:
            raise ProviderError("Ungueltiger Zielpfad") from exc
        if target.exists() and target.is_symlink():
            raise ProviderError("Symlinks werden nicht verarbeitet")
        with target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        return self._entry(target)

    def mkdir(self, path: str, name: str) -> ResourceEntry:
        try:
            folder = resolve_directory_under(self.root, path or ".")
            safe_name = safe_filename(name, fallback="folder")
            target = resolve_for_write_under(
                self.root, (folder.relative_to(self.root) / safe_name).as_posix()
            )
        except (OSError, ValueError) as exc:
            raise ProviderError("Ungueltiger Zielpfad") from exc
        target.mkdir(exist_ok=False)
        return self._entry(target)

    def delete(self, resource_id: str) -> None:
        target = self._resolve(resource_id, require_exists=True)
        if target == self.root or target.is_symlink():
            raise ProviderError("Ressource darf nicht entfernt werden")
        target = resolve_under(self.root, target.relative_to(self.root), strict=True)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    def move(self, resource_id: str, target_path: str, *, name: str | None = None) -> ResourceEntry:
        source = self._resolve(resource_id, require_exists=True)
        if source == self.root or source.is_symlink():
            raise ProviderError("Ressource darf nicht verschoben werden")
        try:
            folder = resolve_directory_under(self.root, target_path or ".")
            destination_name = safe_filename(name, fallback=source.name) if name else source.name
            target = resolve_for_write_under(
                self.root, (folder.relative_to(self.root) / destination_name).as_posix()
            )
        except (OSError, ValueError) as exc:
            raise ProviderError("Ungueltiger Zielpfad") from exc
        source.replace(target)
        return self._entry(target)

    def search(self, query: str, path: str = "") -> Iterable[ResourceEntry]:
        needle = str(query or "").casefold().strip()
        if not needle:
            return []
        try:
            base = resolve_directory_under(self.root, path or ".")
        except (OSError, ValueError) as exc:
            raise ProviderError("Ungueltiger Suchpfad") from exc
        result = []
        for item in base.rglob("*"):
            if item.is_symlink() or ".simpleoffice-meta" in item.parts:
                continue
            try:
                normalized = resolve_under(self.root, item.relative_to(self.root), strict=True)
            except (OSError, ValueError):
                continue
            if needle in normalized.name.casefold():
                result.append(self._entry(normalized))
                if len(result) >= 500:
                    break
        return result
