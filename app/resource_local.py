"""Local managed-storage provider for Resource Commander."""
from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path
from typing import BinaryIO, Iterable

from .resource_provider import ProviderCapabilities, ProviderError, ResourceEntry


class LocalResourceProvider:
    provider_id = "self"
    label = "Self"
    capabilities = ProviderCapabilities(
        read=True, write=True, delete=True, move=True, copy=True, folders=True,
        search=True, metadata=True, streaming=True, smart_view=True,
        server_side_copy=True,
    )

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, value: str, *, require_exists: bool = False) -> Path:
        raw = str(value or "").replace("\\", "/").lstrip("/")
        candidate = (self.root / raw).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ProviderError("Pfad liegt ausserhalb des verwalteten Bereichs") from exc
        if candidate == self.root and raw:
            raise ProviderError("Ungueltiger Pfad")
        if require_exists and not candidate.exists():
            raise ProviderError("Ressource nicht gefunden")
        return candidate

    def _entry(self, path: Path) -> ResourceEntry:
        rel = path.relative_to(self.root).as_posix()
        stat = path.stat()
        is_dir = path.is_dir()
        return ResourceEntry(
            resource_id=rel, name=path.name or "Self", kind="folder" if is_dir else "file",
            path=rel, size=0 if is_dir else int(stat.st_size),
            modified=str(int(stat.st_mtime)),
            mime_type="inode/directory" if is_dir else (mimetypes.guess_type(path.name)[0] or "application/octet-stream"),
            provider=self.provider_id,
        )

    def list(self, path: str = "") -> Iterable[ResourceEntry]:
        folder = self._resolve(path, require_exists=True) if path else self.root
        if not folder.is_dir() or folder.is_symlink():
            raise ProviderError("Kein Verzeichnis")
        for item in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
            if item.name == ".simpleoffice-meta" or item.is_symlink():
                continue
            yield self._entry(item)

    def stat(self, resource_id: str) -> ResourceEntry:
        return self._entry(self._resolve(resource_id, require_exists=True))

    def open(self, resource_id: str) -> BinaryIO:
        path = self._resolve(resource_id, require_exists=True)
        if not path.is_file() or path.is_symlink():
            raise ProviderError("Ressource ist keine regulaere Datei")
        return path.open("rb")

    def read_range(self, resource_id: str, offset: int, length: int) -> bytes:
        path = self._resolve(resource_id, require_exists=True)
        if not path.is_file() or path.is_symlink():
            raise ProviderError("Ressource ist keine regulaere Datei")
        with path.open("rb") as source:
            source.seek(max(0, int(offset)))
            return source.read(max(0, min(int(length), 1024 * 1024)))

    def upload(self, path: str, source: BinaryIO, *, name: str, metadata=None) -> ResourceEntry:
        folder = self._resolve(path, require_exists=True) if path else self.root
        if not folder.is_dir() or folder.is_symlink():
            raise ProviderError("Ziel ist kein Verzeichnis")
        safe_name = Path(str(name or "")).name
        if not safe_name or safe_name in {".", ".."}:
            raise ProviderError("Ungueltiger Dateiname")
        target = self._resolve((folder.relative_to(self.root) / safe_name).as_posix())
        with target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        return self._entry(target)

    def mkdir(self, path: str, name: str) -> ResourceEntry:
        folder = self._resolve(path, require_exists=True) if path else self.root
        safe_name = Path(str(name or "")).name
        target = self._resolve((folder.relative_to(self.root) / safe_name).as_posix())
        target.mkdir(exist_ok=False)
        return self._entry(target)

    def delete(self, resource_id: str) -> None:
        target = self._resolve(resource_id, require_exists=True)
        if target.is_symlink():
            raise ProviderError("Symlinks werden nicht verarbeitet")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    def move(self, resource_id: str, target_path: str, *, name: str | None = None) -> ResourceEntry:
        source = self._resolve(resource_id, require_exists=True)
        folder = self._resolve(target_path, require_exists=True) if target_path else self.root
        target = self._resolve((folder.relative_to(self.root) / (Path(name).name if name else source.name)).as_posix())
        source.replace(target)
        return self._entry(target)

    def search(self, query: str, path: str = "") -> Iterable[ResourceEntry]:
        needle = str(query or "").casefold().strip()
        if not needle:
            return []
        base = self._resolve(path, require_exists=True) if path else self.root
        result = []
        for item in base.rglob("*"):
            if item.is_symlink() or ".simpleoffice-meta" in item.parts:
                continue
            if needle in item.name.casefold():
                result.append(self._entry(item))
                if len(result) >= 500:
                    break
        return result
