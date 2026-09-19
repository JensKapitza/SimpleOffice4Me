"""Stable V2 architecture contracts.

The package deliberately has no Flask, filesystem or federation imports. Concrete
adapters live outside this package and implement the ports declared here.
"""

from .contracts import (
    AuditEvent,
    AuditPort,
    ErrorCode,
    JobRecord,
    JobState,
    JobStorePort,
    LogicalObjectId,
    OperationError,
    OperationResult,
    PersistentFormat,
    PhysicalBlobId,
    StoragePort,
)

__all__ = [
    "AuditEvent",
    "AuditPort",
    "ErrorCode",
    "JobRecord",
    "JobState",
    "JobStorePort",
    "LogicalObjectId",
    "OperationError",
    "OperationResult",
    "PersistentFormat",
    "PhysicalBlobId",
    "StoragePort",
]
