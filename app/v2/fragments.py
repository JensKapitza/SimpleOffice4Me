"""Versioned fragment/recovery contracts for V2 distributed storage."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol, Sequence, runtime_checkable

from .contracts import LogicalObjectId, PhysicalBlobId


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FragmentState(str, Enum):
    PRESENT_VALID = "present_valid"
    PRESENT_CORRUPT = "present_corrupt"
    MISSING = "missing"


@dataclass(frozen=True)
class FragmentPlan:
    """Erasure policy without binding the core to a specific codec package."""

    k: int
    n: int
    codec: str
    codec_version: str

    def __post_init__(self) -> None:
        if not 1 <= int(self.k) <= int(self.n) <= 256:
            raise ValueError("fragment plan requires 1 <= k <= n <= 256")
        if not str(self.codec or "").strip() or not str(self.codec_version or "").strip():
            raise ValueError("fragment codec and version are required")


@dataclass(frozen=True)
class FragmentDescriptor:
    index: int
    physical_id: PhysicalBlobId
    size: int
    sha256: str

    def __post_init__(self) -> None:
        if self.index < 0 or self.size < 0:
            raise ValueError("invalid fragment index or size")
        if not _SHA256.fullmatch(str(self.sha256 or "")):
            raise ValueError("invalid fragment integrity digest")


@dataclass(frozen=True)
class RecoverySet:
    schema: str
    recovery_id: str
    object_id: LogicalObjectId
    version_id: str
    plan: FragmentPlan
    original_size: int
    padding: int
    content_sha256: str
    fragments: tuple[FragmentDescriptor, ...]

    def __post_init__(self) -> None:
        if self.schema != "simpleoffice-v2-fragments/v1":
            raise ValueError("unsupported recovery-set schema")
        if not self.recovery_id or not self.version_id:
            raise ValueError("recovery and version identities are required")
        if self.original_size < 0 or self.padding < 0:
            raise ValueError("invalid recovery-set size")
        if not _SHA256.fullmatch(str(self.content_sha256 or "")):
            raise ValueError("invalid recovery-set content digest")
        if len(self.fragments) != self.plan.n:
            raise ValueError("recovery set must describe exactly n fragments")
        indices = [item.index for item in self.fragments]
        if indices != list(range(self.plan.n)):
            raise ValueError("fragment descriptors must have contiguous indexes")


@dataclass(frozen=True)
class FragmentAssessment:
    states: Mapping[int, FragmentState]
    valid_count: int
    corrupt_count: int
    missing_count: int
    recoverable: bool


@runtime_checkable
class ErasureCodec(Protocol):
    """Pluggable k-of-n codec boundary.

    Implementations must be externally reviewed/tested codecs; the V2 core does
    not implement its own erasure mathematics.
    """

    name: str
    version: str

    def encode(self, payload: bytes, plan: FragmentPlan) -> Sequence[bytes]:
        ...

    def decode(
        self,
        fragments: Mapping[int, bytes],
        plan: FragmentPlan,
        *,
        original_size: int,
        padding: int,
    ) -> bytes:
        ...


def assess_fragments(recovery_set: RecoverySet, available: Mapping[str, bytes]) -> FragmentAssessment:
    states: dict[int, FragmentState] = {}
    valid = corrupt = missing = 0
    for descriptor in recovery_set.fragments:
        payload = available.get(descriptor.physical_id.value)
        if payload is None:
            states[descriptor.index] = FragmentState.MISSING
            missing += 1
            continue
        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != descriptor.size or digest != descriptor.sha256:
            states[descriptor.index] = FragmentState.PRESENT_CORRUPT
            corrupt += 1
            continue
        states[descriptor.index] = FragmentState.PRESENT_VALID
        valid += 1
    return FragmentAssessment(
        states=states,
        valid_count=valid,
        corrupt_count=corrupt,
        missing_count=missing,
        recoverable=valid >= recovery_set.plan.k,
    )
