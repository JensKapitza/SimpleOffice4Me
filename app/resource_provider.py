"""Common resource-provider model for the two-pane Resource Commander.

Providers deliberately expose capabilities instead of pretending every backend
is a POSIX filesystem.  This lets mail, federation and managed storage share
one UI without enabling operations a backend cannot safely perform.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, BinaryIO, Iterable, Protocol


@dataclass(frozen=True)
class ProviderCapabilities:
    read: bool = True
    write: bool = False
    delete: bool = False
    move: bool = False
    copy: bool = True
    folders: bool = True
    search: bool = False
    metadata: bool = True
    streaming: bool = True
    smart_view: bool = False
    server_side_copy: bool = False

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceEntry:
    resource_id: str
    name: str
    kind: str = "file"
    path: str = ""
    size: int = 0
    modified: str = ""
    mime_type: str = "application/octet-stream"
    provider: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResourceProvider(Protocol):
    provider_id: str
    label: str
    capabilities: ProviderCapabilities

    def list(self, path: str = "") -> Iterable[ResourceEntry]: ...
    def stat(self, resource_id: str) -> ResourceEntry: ...
    def open(self, resource_id: str) -> BinaryIO: ...
    def upload(self, path: str, source: BinaryIO, *, name: str, metadata: dict[str, Any] | None = None) -> ResourceEntry: ...
    def mkdir(self, path: str, name: str) -> ResourceEntry: ...
    def delete(self, resource_id: str) -> None: ...
    def move(self, resource_id: str, target_path: str, *, name: str | None = None) -> ResourceEntry: ...
    def search(self, query: str, path: str = "") -> Iterable[ResourceEntry]: ...


class ProviderError(RuntimeError):
    pass


class UnsupportedOperation(ProviderError):
    pass
