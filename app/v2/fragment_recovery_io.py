"""Portable fragment inputs and atomic recovery export for the V2 CLI."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Sequence

from .fragments import FragmentAssessment, RecoverySet, recovery_set_from_dict


_MAX_DESCRIPTOR_BYTES = 1024 * 1024


def load_recovery_descriptor(path: str | Path) -> RecoverySet:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("fragment recovery descriptor does not exist")
    if source.stat().st_size > _MAX_DESCRIPTOR_BYTES:
        raise ValueError("fragment recovery descriptor exceeds the size limit")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid fragment recovery descriptor") from exc
    if not isinstance(document, dict):
        raise ValueError("fragment recovery descriptor must contain a JSON object")
    return recovery_set_from_dict(document)


def load_fragment_specs(recovery_set: RecoverySet, specs: Sequence[str]) -> dict[str, bytes]:
    descriptors = {item.index: item for item in recovery_set.fragments}
    available: dict[str, bytes] = {}
    seen: set[int] = set()

    for spec in specs:
        index_text, separator, path_text = str(spec).partition("=")
        if not separator or not path_text.strip():
            raise ValueError("fragment must use INDEX=PATH syntax")
        try:
            index = int(index_text)
        except ValueError as exc:
            raise ValueError("fragment index must be an integer") from exc
        if index in seen:
            raise ValueError(f"fragment index {index} was supplied more than once")
        descriptor = descriptors.get(index)
        if descriptor is None:
            raise ValueError(f"fragment index {index} is outside the recovery set")
        seen.add(index)

        source = Path(path_text).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"fragment {index} does not exist")
        with source.open("rb") as handle:
            payload = handle.read(descriptor.size + 1)
        available[descriptor.physical_id.value] = payload
    return available


def assessment_to_dict(assessment: FragmentAssessment) -> dict[str, object]:
    return {
        "states": {str(index): state.value for index, state in sorted(assessment.states.items())},
        "valid_count": assessment.valid_count,
        "corrupt_count": assessment.corrupt_count,
        "missing_count": assessment.missing_count,
        "recoverable": assessment.recoverable,
    }


def export_recovered_payload(
    payload: bytes,
    output: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    target = Path(output).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        raise FileExistsError("fragment recovery output already exists")

    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
