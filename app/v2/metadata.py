"""V2 metadata contracts with explicit provenance and filesystem alias rules."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .contracts import LogicalObjectId


class MetadataSource(str, Enum):
    ORIGINAL = "original"
    OBSERVED = "observed"
    IMPORTED = "imported"
    FEDERATED = "federated"


class TrustLevel(str, Enum):
    UNKNOWN = "unknown"
    UNTRUSTED = "untrusted"
    LOCAL = "local"
    TRUSTED = "trusted"


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONFLICT = "conflict"
    INVALID = "invalid"


class UnicodeNormalization(str, Enum):
    NONE = "none"
    NFC = "NFC"
    NFD = "NFD"


@dataclass(frozen=True)
class Provenance:
    source: MetadataSource
    source_ref: str
    observed_at: str
    actor: str = ""

    def __post_init__(self) -> None:
        if not self.source_ref or not self.observed_at:
            raise ValueError("metadata provenance source_ref and observed_at are required")


@dataclass(frozen=True)
class MetadataValue:
    name: str
    value: Any
    provenance: Provenance
    trust: TrustLevel = TrustLevel.UNKNOWN
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    immutable: bool = False

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        if not name or len(name) > 200:
            raise ValueError("invalid metadata field name")
        object.__setattr__(self, "name", name)
        if self.provenance.source is MetadataSource.ORIGINAL and not self.immutable:
            raise ValueError("original metadata must be immutable")
        if self.provenance.source is not MetadataSource.ORIGINAL and self.immutable:
            raise ValueError("only original metadata may be immutable")


@dataclass(frozen=True)
class NamespaceRules:
    case_sensitive: bool = True
    unicode_normalization: UnicodeNormalization = UnicodeNormalization.NFC
    max_name_bytes: int = 255

    def __post_init__(self) -> None:
        if self.max_name_bytes < 1 or self.max_name_bytes > 4096:
            raise ValueError("invalid maximum filename length")

    def normalize(self, name: str) -> str:
        value = str(name or "")
        if not value or value in {".", ".."}:
            raise ValueError("invalid filename alias")
        if any(ch in value for ch in ("/", "\\", "\x00")):
            raise ValueError("filename alias contains a path separator or NUL")
        if self.unicode_normalization is not UnicodeNormalization.NONE:
            value = unicodedata.normalize(self.unicode_normalization.value, value)
        if len(value.encode("utf-8")) > self.max_name_bytes:
            raise ValueError("filename alias exceeds namespace limit")
        return value

    def collision_key(self, name: str) -> str:
        normalized = self.normalize(name)
        return normalized if self.case_sensitive else normalized.casefold()


@dataclass(frozen=True)
class FilenameAlias:
    name: str
    provenance: Provenance
    namespace: NamespaceRules = field(default_factory=NamespaceRules)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.namespace.normalize(self.name))

    @property
    def collision_key(self) -> str:
        return self.namespace.collision_key(self.name)


@dataclass(frozen=True)
class MetadataEnvelope:
    object_id: LogicalObjectId
    original: tuple[MetadataValue, ...] = ()
    observed: tuple[MetadataValue, ...] = ()
    imported: tuple[MetadataValue, ...] = ()
    federated: tuple[MetadataValue, ...] = ()
    aliases: tuple[FilenameAlias, ...] = ()
    sidecar: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        buckets = (
            (MetadataSource.ORIGINAL, self.original),
            (MetadataSource.OBSERVED, self.observed),
            (MetadataSource.IMPORTED, self.imported),
            (MetadataSource.FEDERATED, self.federated),
        )
        for expected, values in buckets:
            for item in values:
                if item.provenance.source is not expected:
                    raise ValueError(f"metadata value belongs in {item.provenance.source.value}, not {expected.value}")
        original_names = [item.name for item in self.original]
        if len(original_names) != len(set(original_names)):
            raise ValueError("original metadata field names must be unique")
        for key in self.sidecar:
            if not str(key).strip():
                raise ValueError("sidecar metadata keys must not be empty")

    def values_for(self, name: str) -> tuple[MetadataValue, ...]:
        field_name = str(name or "").strip()
        return tuple(
            item
            for bucket in (self.original, self.observed, self.imported, self.federated)
            for item in bucket
            if item.name == field_name
        )

    def alias_collisions(self) -> tuple[tuple[FilenameAlias, FilenameAlias], ...]:
        collisions: list[tuple[FilenameAlias, FilenameAlias]] = []
        for index, left in enumerate(self.aliases):
            for right in self.aliases[index + 1 :]:
                if left.namespace == right.namespace and left.collision_key == right.collision_key:
                    collisions.append((left, right))
        return tuple(collisions)


def original_value(name: str, value: Any, provenance: Provenance,
                   *, verification: VerificationStatus = VerificationStatus.UNVERIFIED) -> MetadataValue:
    if provenance.source is not MetadataSource.ORIGINAL:
        raise ValueError("original metadata requires original provenance")
    return MetadataValue(
        name=name,
        value=value,
        provenance=provenance,
        trust=TrustLevel.LOCAL,
        verification=verification,
        immutable=True,
    )
