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
    "EncryptedBlobIntegrityError",
    "EncryptedBlobStore",
    "EncryptedBlobVersion",
    "CryptoService",
    "ChunkCryptoSession",
    "EncryptedChunk",
    "EncryptedPayload",
    "ProtectedMasterKey",
    "WrappedKey",
    "MasterKeyProfileStore",
    "RecoveryMaterial",
    "encode_recovery_key",
    "decode_recovery_key",
    "recover_master_key_from_bundle",
    "EncodedRecoverySet",
    "ErasureCodec",
    "FragmentAssessment",
    "FragmentDescriptor",
    "FragmentPlan",
    "FragmentRecoveryError",
    "FragmentState",
    "RecoverySet",
    "assess_fragments",
    "encode_recovery_set",
    "recover_payload",
    "recovery_set_from_dict",
    "recovery_set_to_dict",
    "FederationTransferIntent",
    "PersistentJobStore",
    "FederationJobService",
    "AuthorizationStore",
    "CapabilityGrant",
    "GrantRight",
    "FilenameAlias",
    "MetadataEnvelope",
    "MetadataSource",
    "MetadataValue",
    "NamespaceRules",
    "Provenance",
    "TrustLevel",
    "UnicodeNormalization",
    "VerificationStatus",
    "original_value",
    "OverlayImportJournal",
    "OverlayImportRecord",
    "OverlayImportState",
    "CatalogEntry",
    "CatalogState",
    "ObjectCatalog",
]

from .blob_store import BlobIntegrityError, BlobStore, BlobVersion
from .encrypted_blob_store import EncryptedBlobIntegrityError, EncryptedBlobStore, EncryptedBlobVersion

from .crypto import ChunkCryptoSession, CryptoService, EncryptedChunk, EncryptedPayload, ProtectedMasterKey, WrappedKey
from .master_keys import MasterKeyProfileStore, RecoveryMaterial, decode_recovery_key, encode_recovery_key, recover_master_key_from_bundle

from .fragments import (
    EncodedRecoverySet,
    ErasureCodec,
    FragmentAssessment,
    FragmentDescriptor,
    FragmentPlan,
    FragmentRecoveryError,
    FragmentState,
    RecoverySet,
    assess_fragments,
    encode_recovery_set,
    recover_payload,
    recovery_set_from_dict,
    recovery_set_to_dict,
)

from .jobs import FederationTransferIntent, PersistentJobStore, FederationJobService

from .authorization import AuthorizationStore, CapabilityGrant, GrantRight

from .metadata import FilenameAlias, MetadataEnvelope, MetadataSource, MetadataValue, NamespaceRules, Provenance, TrustLevel, UnicodeNormalization, VerificationStatus, original_value

from .overlay import OverlayImportJournal, OverlayImportRecord, OverlayImportState

from .catalog import CatalogEntry, CatalogState, ObjectCatalog
