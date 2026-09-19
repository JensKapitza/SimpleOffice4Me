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
    StorageLocation,
    StoredObject,
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
    "StorageLocation",
    "StoredObject",
    "StoragePort",
    "BlobIntegrityError",
    "BlobStore",
    "BlobVersion",
    "CryptoService",
    "EncryptedPayload",
    "ProtectedMasterKey",
    "WrappedKey",
    "ErasureCodec",
    "FragmentAssessment",
    "FragmentDescriptor",
    "FragmentPlan",
    "FragmentState",
    "RecoverySet",
    "assess_fragments",
    "FederationTransferIntent",
    "PersistentJobStore",
    "FederationJobService",
    "AuthorizationStore",
    "CapabilityGrant",
    "GrantRight",
]

from .blob_store import BlobIntegrityError, BlobStore, BlobVersion

from .crypto import CryptoService, EncryptedPayload, ProtectedMasterKey, WrappedKey

from .fragments import ErasureCodec, FragmentAssessment, FragmentDescriptor, FragmentPlan, FragmentState, RecoverySet, assess_fragments

from .jobs import FederationTransferIntent, PersistentJobStore, FederationJobService

from .authorization import AuthorizationStore, CapabilityGrant, GrantRight
