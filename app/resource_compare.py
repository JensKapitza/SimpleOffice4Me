"""Provider-independent staged comparison for Resource Commander."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from .resource_provider import ProviderError, ResourceEntry

SAMPLE_BYTES = 1024 * 1024
AUTO_FULL_HASH_BYTES = 32 * 1024 * 1024
FULL_HASH_CHUNK = 1024 * 1024
MAX_DIRECTORY_ENTRIES = 5000


@dataclass(frozen=True)
class CompareEvidence:
    method: str
    result: str
    detail: str = ""
    bytes_read_left: int = 0
    bytes_read_right: int = 0


@dataclass(frozen=True)
class CompareResult:
    status: str
    confidence: str
    left: dict[str, Any]
    right: dict[str, Any]
    evidence: list[dict[str, Any]]
    bytes_read_left: int = 0
    bytes_read_right: int = 0


def _known_digest(entry: ResourceEntry) -> tuple[str, str] | None:
    meta = entry.metadata or {}
    for algorithm, length in (("sha512", 128), ("sha256", 64), ("blake2b", 128)):
        value = str(meta.get(algorithm) or meta.get(f"checksum_{algorithm}") or "").strip().lower()
        if len(value) == length and all(c in "0123456789abcdef" for c in value):
            return algorithm, value
    return None


def read_range(provider, resource_id: str, offset: int, length: int) -> bytes:
    offset = max(0, int(offset))
    length = max(0, min(int(length), SAMPLE_BYTES))
    native = getattr(provider, "read_range", None)
    if callable(native):
        data = native(resource_id, offset, length)
        if not isinstance(data, (bytes, bytearray)):
            raise ProviderError("Provider lieferte ungueltige Bereichsdaten")
        return bytes(data[:length])
    with provider.open(resource_id) as stream:
        try:
            stream.seek(offset)
        except (AttributeError, OSError):
            remaining = offset
            while remaining:
                block = stream.read(min(remaining, SAMPLE_BYTES))
                if not block:
                    return b""
                remaining -= len(block)
        return stream.read(length)


def _sample_offsets(size: int) -> list[int]:
    if size <= SAMPLE_BYTES:
        return [0]
    return list(dict.fromkeys((0, max(0, (size - SAMPLE_BYTES) // 2), max(0, size - SAMPLE_BYTES))))


def _full_digest(provider, resource_id: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with provider.open(resource_id) as stream:
        while True:
            block = stream.read(FULL_HASH_CHUNK)
            if not block:
                break
            digest.update(block)
            total += len(block)
    return digest.hexdigest(), total


def _result(status: str, confidence: str, left: ResourceEntry, right: ResourceEntry,
            evidence: list[CompareEvidence], read_left: int, read_right: int) -> CompareResult:
    return CompareResult(status, confidence, left.to_dict(), right.to_dict(),
                         [asdict(item) for item in evidence], read_left, read_right)


def compare_files(left_provider, left_id: str, right_provider, right_id: str, *, mode: str = "normal") -> CompareResult:
    mode = str(mode or "normal").lower()
    if mode not in {"fast", "normal", "full"}:
        raise ProviderError("Vergleichsmodus muss fast, normal oder full sein")
    left = left_provider.stat(left_id)
    right = right_provider.stat(right_id)
    if left.kind != "file" or right.kind != "file":
        raise ProviderError("Dateivergleich benoetigt zwei Dateien")
    evidence: list[CompareEvidence] = []
    read_left = read_right = 0
    evidence.append(CompareEvidence("name", "match" if left.name == right.name else "different",
                                    "Dateiname identisch" if left.name == right.name else "Dateiname unterschiedlich"))
    if int(left.size) != int(right.size):
        evidence.append(CompareEvidence("size", "different", f"{left.size} != {right.size}"))
        return _result("different", "certain", left, right, evidence, 0, 0)
    evidence.append(CompareEvidence("size", "match", f"{left.size} Byte"))

    a_digest = _known_digest(left)
    b_digest = _known_digest(right)
    if a_digest and b_digest and a_digest[0] == b_digest[0]:
        if a_digest[1] != b_digest[1]:
            evidence.append(CompareEvidence(a_digest[0], "different", "Gespeicherte Vollpruefsummen unterscheiden sich"))
            return _result("different", "certain", left, right, evidence, 0, 0)
        evidence.append(CompareEvidence(a_digest[0], "match", "Gespeicherte Vollpruefsumme identisch"))
        return _result("identical", "cryptographic", left, right, evidence, 0, 0)

    if int(left.size) == 0:
        return _result("identical", "certain", left, right,
                       evidence + [CompareEvidence("empty", "match", "Beide Dateien sind leer")], 0, 0)

    offsets = _sample_offsets(int(left.size))
    if mode == "fast":
        offsets = offsets[:1]
    for offset in offsets:
        length = min(SAMPLE_BYTES, int(left.size) - offset)
        a = read_range(left_provider, left.resource_id, offset, length)
        b = read_range(right_provider, right.resource_id, offset, length)
        read_left += len(a)
        read_right += len(b)
        if len(a) != len(b) or hashlib.sha256(a).digest() != hashlib.sha256(b).digest():
            evidence.append(CompareEvidence("sample-sha256", "different", f"1-MiB-Block bei Offset {offset} unterschiedlich", len(a), len(b)))
            return _result("different", "certain", left, right, evidence, read_left, read_right)
        evidence.append(CompareEvidence("sample-sha256", "match", f"Block bei Offset {offset} stimmt", len(a), len(b)))

    should_full = mode == "full" or (mode == "normal" and int(left.size) <= AUTO_FULL_HASH_BYTES)
    if should_full:
        da, ca = _full_digest(left_provider, left.resource_id)
        db, cb = _full_digest(right_provider, right.resource_id)
        read_left += ca
        read_right += cb
        if da != db:
            evidence.append(CompareEvidence("full-sha256", "different", "Vollpruefsummen unterscheiden sich", ca, cb))
            return _result("different", "certain", left, right, evidence, read_left, read_right)
        evidence.append(CompareEvidence("full-sha256", "match", "Vollstaendige SHA-256-Pruefsumme identisch", ca, cb))
        return _result("identical", "cryptographic", left, right, evidence, read_left, read_right)

    evidence.append(CompareEvidence("sampling", "candidate", "Teilpruefungen stimmen; Vollvergleich bewusst ausgelassen"))
    return _result("probably_identical", "sampled", left, right, evidence, read_left, read_right)


def compare_directories(left_provider, left_path: str, right_provider, right_path: str, *, mode: str = "fast") -> dict[str, Any]:
    left_items = list(left_provider.list(left_path))[:MAX_DIRECTORY_ENTRIES]
    right_items = list(right_provider.list(right_path))[:MAX_DIRECTORY_ENTRIES]
    left_by_name = {item.name.casefold(): item for item in left_items}
    right_by_name = {item.name.casefold(): item for item in right_items}
    rows = []
    totals: dict[str, int] = {k: 0 for k in ("identical", "probably_identical", "different", "left_only", "right_only", "folders")}
    for key in sorted(set(left_by_name) | set(right_by_name)):
        a = left_by_name.get(key)
        b = right_by_name.get(key)
        if a is None:
            status, row = "right_only", {"name": b.name, "status": "right_only", "right": b.to_dict()}
        elif b is None:
            status, row = "left_only", {"name": a.name, "status": "left_only", "left": a.to_dict()}
        elif a.kind == "folder" and b.kind == "folder":
            status, row = "folders", {"name": a.name, "status": "folders", "left": a.to_dict(), "right": b.to_dict()}
        elif a.kind != b.kind:
            status, row = "different", {"name": a.name, "status": "different", "reason": "Typ unterschiedlich", "left": a.to_dict(), "right": b.to_dict()}
        else:
            compared = compare_files(left_provider, a.resource_id, right_provider, b.resource_id, mode=mode)
            status, row = compared.status, {"name": a.name, **asdict(compared)}
        totals[status] = totals.get(status, 0) + 1
        rows.append(row)
    return {"mode": mode, "left_path": left_path, "right_path": right_path, "totals": totals, "rows": rows}


def completeness_check(source_provider, source_path: str, target_provider, target_path: str, *, full: bool = False) -> dict[str, Any]:
    """Verify that every source file exists on target; extra target entries are allowed."""
    mode = "full" if full else "fast"
    compared = compare_directories(source_provider, source_path, target_provider, target_path, mode=mode)
    required_missing = []
    required_uncertain = []
    for row in compared["rows"]:
        status = row["status"]
        if status in {"left_only", "different"}:
            required_missing.append(row)
        elif status == "probably_identical":
            required_uncertain.append(row)
    return {
        **compared,
        "complete": not required_missing and (not full or not required_uncertain),
        "required_missing": required_missing,
        "required_uncertain": required_uncertain,
        "extra_target_allowed": True,
    }
