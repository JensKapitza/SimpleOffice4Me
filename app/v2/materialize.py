"""Temporary verified materialization for consumers that require a filesystem path.

V2 content remains authoritative in StoragePort.  This helper creates a private,
short-lived plaintext file only after the complete object has been streamed and
verified.  The temporary file is never placed in the managed document tree and
is removed when the context exits.
"""
from __future__ import annotations

import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .contracts import LogicalObjectId
from .storage_runtime import result_or_raise, storage_for


_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9._-]{1,24}$")


def _validated_suffix(value: str) -> str:
    suffix = str(value or "").strip()
    if not suffix:
        return ""
    if not _SAFE_SUFFIX.fullmatch(suffix):
        raise ValueError("invalid materialized file suffix")
    return suffix


@contextmanager
def materialize_verified_object(
    root: str | Path,
    actor: str,
    object_id: str | LogicalObjectId,
    *,
    suffix: str = "",
) -> Iterator[Path]:
    """Yield a private temporary path containing verified StoragePort bytes."""

    logical = object_id if isinstance(object_id, LogicalObjectId) else LogicalObjectId(str(object_id))
    extension = _validated_suffix(suffix)
    with tempfile.TemporaryDirectory(prefix="simpleoffice-v2-materialized-") as temp:
        directory = Path(temp)
        if os.name == "posix":
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        target = directory / f"object{extension}"
        with target.open("xb") as handle:
            result_or_raise(
                storage_for(root, actor).copy_verified_to(logical, handle)
            )
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        yield target
