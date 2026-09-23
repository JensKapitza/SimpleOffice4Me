"""Versioned fragment/recovery contracts for V2 distributed storage."""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .contracts import LogicalObjectId, PhysicalBlobId


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FRAGMENT_SCHEMA = "simpleoffice-v2-fragments/v1"


class FragmentRecoveryError(ValueError):
    """Recovery metadata, fragments or codec output failed validation."""


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
        if self.schema != _FRAGMENT_SCHEMA:
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
        physical_ids = [item.physical_id.value for item in self.fragments]
        if len(set(physical_ids)) != len(physical_ids):
            raise ValueError("fragment physical ids must be unique")


@dataclass(frozen=True)
class FragmentAssessment:
    states: Mapping[int, FragmentState]
    valid_count: int
    corrupt_count: int
    missing_count: int
    recoverable: bool


@dataclass(frozen=True)
class EncodedRecoverySet:
    recovery_set: RecoverySet
    fragments: Mapping[str, bytes]


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


def _require_codec(plan: FragmentPlan, codec: ErasureCodec) -> None:
    if getattr(codec, "name", None) != plan.codec or getattr(codec, "version", None) != plan.codec_version:
        raise FragmentRecoveryError(
            f"codec mismatch: recovery set requires {plan.codec}/{plan.codec_version}"
        )


def recovery_set_to_dict(recovery_set: RecoverySet) -> dict[str, Any]:
    return {
        "schema": recovery_set.schema,
        "recovery_id": recovery_set.recovery_id,
        "object_id": recovery_set.object_id.value,
        "version_id": recovery_set.version_id,
        "plan": {
            "k": recovery_set.plan.k,
            "n": recovery_set.plan.n,
            "codec": recovery_set.plan.codec,
            "codec_version": recovery_set.plan.codec_version,
        },
        "original_size": recovery_set.original_size,
        "padding": recovery_set.padding,
        "content_sha256": recovery_set.content_sha256,
        "fragments": [
            {
                "index": item.index,
                "physical_id": item.physical_id.value,
                "size": item.size,
                "sha256": item.sha256,
            }
            for item in recovery_set.fragments
        ],
    }


def recovery_set_from_dict(value: Mapping[str, Any]) -> RecoverySet:
    if not isinstance(value, Mapping):
        raise ValueError("recovery set must be an object")
    try:
        plan_value = value["plan"]
        fragment_values = value["fragments"]
        if not isinstance(plan_value, Mapping):
            raise TypeError("plan")
        if not isinstance(fragment_values, Sequence) or isinstance(fragment_values, (str, bytes, bytearray)):
            raise TypeError("fragments")
        plan = FragmentPlan(
            k=int(plan_value["k"]),
            n=int(plan_value["n"]),
            codec=str(plan_value["codec"]),
            codec_version=str(plan_value["codec_version"]),
        )
        fragments = tuple(
            FragmentDescriptor(
                index=int(item["index"]),
                physical_id=PhysicalBlobId(str(item["physical_id"])),
                size=int(item["size"]),
                sha256=str(item["sha256"]),
            )
            for item in fragment_values
            if isinstance(item, Mapping)
        )
        if len(fragments) != len(fragment_values):
            raise TypeError("fragment descriptor")
        return RecoverySet(
            schema=str(value["schema"]),
            recovery_id=str(value["recovery_id"]),
            object_id=LogicalObjectId(str(value["object_id"])),
            version_id=str(value["version_id"]),
            plan=plan,
            original_size=int(value["original_size"]),
            padding=int(value["padding"]),
            content_sha256=str(value["content_sha256"]),
            fragments=fragments,
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            ("unsupported recovery-set", "fragment ", "invalid ", "recovery ")
        ):
            raise
        raise ValueError("invalid recovery-set document") from exc


def encode_recovery_set(
    payload: bytes,
    *,
    object_id: LogicalObjectId,
    version_id: str,
    plan: FragmentPlan,
    codec: ErasureCodec,
    recovery_id: str | None = None,
) -> EncodedRecoverySet:
    _require_codec(plan, codec)
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload must be bytes-like")
    content = bytes(payload)
    encoded = tuple(bytes(fragment) for fragment in codec.encode(content, plan))
    if len(encoded) != plan.n:
        raise FragmentRecoveryError("codec did not return exactly n fragments")
    sizes = {len(fragment) for fragment in encoded}
    if len(sizes) != 1:
        raise FragmentRecoveryError("codec returned unequal fragment sizes")
    fragment_size = next(iter(sizes), 0)
    padding = fragment_size * plan.k - len(content)
    if padding < 0:
        raise FragmentRecoveryError("codec fragments cannot represent original content size")

    descriptors: list[FragmentDescriptor] = []
    fragments: dict[str, bytes] = {}
    for index, fragment in enumerate(encoded):
        physical_id = PhysicalBlobId(uuid.uuid4().hex)
        descriptors.append(
            FragmentDescriptor(
                index=index,
                physical_id=physical_id,
                size=len(fragment),
                sha256=hashlib.sha256(fragment).hexdigest(),
            )
        )
        fragments[physical_id.value] = fragment

    recovery_set = RecoverySet(
        schema=_FRAGMENT_SCHEMA,
        recovery_id=recovery_id or uuid.uuid4().hex,
        object_id=object_id,
        version_id=str(version_id),
        plan=plan,
        original_size=len(content),
        padding=padding,
        content_sha256=hashlib.sha256(content).hexdigest(),
        fragments=tuple(descriptors),
    )
    return EncodedRecoverySet(recovery_set=recovery_set, fragments=fragments)


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


def recover_payload(
    recovery_set: RecoverySet,
    available: Mapping[str, bytes],
    codec: ErasureCodec,
) -> bytes:
    _require_codec(recovery_set.plan, codec)
    assessment = assess_fragments(recovery_set, available)
    if not assessment.recoverable:
        raise FragmentRecoveryError(
            f"recovery requires {recovery_set.plan.k} valid fragments; found {assessment.valid_count}"
        )
    valid_fragments = {
        descriptor.index: bytes(available[descriptor.physical_id.value])
        for descriptor in recovery_set.fragments
        if assessment.states[descriptor.index] is FragmentState.PRESENT_VALID
    }
    decoded = codec.decode(
        valid_fragments,
        recovery_set.plan,
        original_size=recovery_set.original_size,
        padding=recovery_set.padding,
    )
    if not isinstance(decoded, (bytes, bytearray, memoryview)):
        raise FragmentRecoveryError("codec returned a non-bytes payload")
    payload = bytes(decoded)
    if len(payload) != recovery_set.original_size:
        raise FragmentRecoveryError("reconstructed payload size mismatch")
    if hashlib.sha256(payload).hexdigest() != recovery_set.content_sha256:
        raise FragmentRecoveryError("reconstructed payload integrity mismatch")
    return payload
