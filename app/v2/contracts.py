"""Implementation-neutral contracts for the incremental V2 migration.

These types intentionally contain no Flask, HTTP, filesystem, database,
cryptography or federation implementation details.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, BinaryIO, Generic, Mapping, Protocol, TypeVar, runtime_checkable


T = TypeVar("T")


class ErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    FORBIDDEN = "forbidden"
    INTEGRITY_ERROR = "integrity_error"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    RETRYABLE = "retryable"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class OperationError:
    code: ErrorCode
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationResult(Generic[T]):
    value: T | None = None
    error: OperationError | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.error is None):
            raise ValueError("exactly one of value or error is required")

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def success(cls, value: T) -> "OperationResult[T]":
        return cls(value=value)

    @classmethod
    def failure(
        cls,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> "OperationResult[T]":
        return cls(
            error=OperationError(
                code=code,
                message=str(message),
                retryable=bool(retryable),
                details=dict(details or {}),
            )
        )


@dataclass(frozen=True, order=True)
class LogicalObjectId:
    """Stable domain identity. It must never be derived from a physical path."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 200:
            raise ValueError("invalid logical object id")


@dataclass(frozen=True, order=True)
class PhysicalBlobId:
    """Opaque identity of one physical content representation."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 300:
            raise ValueError("invalid physical blob id")


@dataclass(frozen=True)
class StorageLocation:
    """Presentation/storage namespace location; never a logical identity."""

    relative_path: str

    def __post_init__(self) -> None:
        value = str(self.relative_path or "").strip().replace("\\", "/")
        if not value or value.startswith("/") or value in {".", ".."} or ".." in value.split("/"):
            raise ValueError("invalid storage location")
        object.__setattr__(self, "relative_path", value)


@dataclass(frozen=True)
class StoredObject:
    object_id: LogicalObjectId
    version: str
    size: int
    location: StorageLocation

    def __post_init__(self) -> None:
        if not self.version or self.size < 0:
            raise ValueError("invalid stored object")


@dataclass(frozen=True)
class PersistentFormat:
    family: str
    version: int

    def __post_init__(self) -> None:
        if not self.family or self.version < 1:
            raise ValueError("invalid persistent format version")


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    kind: str
    state: JobState
    idempotency_key: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    attempt: int = 0

    def __post_init__(self) -> None:
        if not self.job_id or not self.kind or not self.idempotency_key:
            raise ValueError("job identity fields are required")
        if self.attempt < 0:
            raise ValueError("job attempt must not be negative")


@dataclass(frozen=True)
class AuditEvent:
    actor: str
    operation: str
    object_id: str
    occurred_at: str
    source: str = ""
    correlation_id: str = ""
    changes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.actor or not self.operation or not self.object_id or not self.occurred_at:
            raise ValueError("audit identity fields are required")


@runtime_checkable
class StoragePort(Protocol):
    """Logical-object storage boundary.

    Implementations may use the current DocumentStore, a V2 blob store or a
    remote backend. Callers must not depend on concrete paths. Moving or
    renaming an object changes only its StorageLocation, never its LogicalObjectId.
    Copying creates a new LogicalObjectId.
    """

    def read_bytes(self, object_id: LogicalObjectId) -> OperationResult[bytes]:
        ...

    def copy_verified_to(
        self,
        object_id: LogicalObjectId,
        target: BinaryIO,
    ) -> OperationResult[StoredObject]:
        """Stream verified content to target.

        Implementations may write provisional bytes before the final integrity
        check completes. Callers must publish or otherwise trust the target only
        after a successful result.
        """
        ...

    def copy_verified_range_to(
        self,
        object_id: LogicalObjectId,
        target: BinaryIO,
        *,
        start: int,
        length: int | None = None,
    ) -> OperationResult[StoredObject]:
        """Verify the complete object while copying only one byte range.

        Implementations may write provisional range bytes before final whole-
        object verification completes. Callers must publish the target only
        after a successful result.
        """
        ...

    def create_bytes(self, location: StorageLocation, content: bytes) -> OperationResult[StoredObject]:
        ...

    def import_stream(
        self,
        stream: BinaryIO,
        filename: str,
        *,
        archive: bool = False,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        ...

    def replace_bytes(
        self,
        object_id: LogicalObjectId,
        content: bytes,
        *,
        expected_version: str | None = None,
        source: str = "v2-storage",
        restored_from_version: str = "",
    ) -> OperationResult[StoredObject]:
        ...

    def copy(self, object_id: LogicalObjectId, destination: StorageLocation) -> OperationResult[StoredObject]:
        ...

    def copy_replace(
        self,
        source_id: LogicalObjectId,
        destination_id: LogicalObjectId,
        *,
        expected_source_version: str,
        expected_destination_version: str,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        ...

    def move_replace(
        self,
        source_id: LogicalObjectId,
        destination_id: LogicalObjectId,
        *,
        expected_source_version: str,
        expected_destination_version: str,
        max_bytes: int = 512 * 1024 * 1024,
    ) -> OperationResult[StoredObject]:
        ...

    def delete(self, object_id: LogicalObjectId, *, expected_version: str | None = None) -> OperationResult[str]:
        ...

    def move(self, object_id: LogicalObjectId, destination: StorageLocation) -> OperationResult[StoredObject]:
        ...

    def restore(
        self,
        object_id: LogicalObjectId,
        destination: StorageLocation,
        *,
        expected_version: str | None = None,
    ) -> OperationResult[StoredObject]:
        ...


@runtime_checkable
class JobStorePort(Protocol):
    def put(self, job: JobRecord) -> OperationResult[JobRecord]:
        ...

    def get(self, job_id: str) -> OperationResult[JobRecord]:
        ...


@runtime_checkable
class AuditPort(Protocol):
    def append(self, event: AuditEvent) -> OperationResult[str]:
        ...
