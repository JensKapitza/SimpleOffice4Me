"""Reusable SOFP federation and file-transfer primitives.

These helpers deliberately contain no network client.  They model manifests,
chunk scheduling, peer policy, resumable transfer state and safe third-party
transfer planning so HTTP workers can remain small and testable.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024
RESOURCE_TYPES = frozenset({"documents", "contacts", "calendars", "tasks"})
TRANSFER_OPERATIONS = frozenset({"COPY", "REPLICATE", "MOVE", "VERIFY", "REPAIR"})


def normalize_sha256(value: str) -> str:
    value = str(value or "").removeprefix("sha256:").casefold().strip()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("invalid sha256")
    return value


def sha256_bytes(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def chunk_count(size: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> int: return math.ceil(max(0, size) / chunk_size)
def chunk_offset(index: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> int: return index * chunk_size
def chunk_length(index: int, size: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> int: return max(0, min(chunk_size, size - chunk_offset(index, chunk_size)))
def chunk_range(index: int, size: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> tuple[int, int]:
    start = chunk_offset(index, chunk_size); return start, start + chunk_length(index, size, chunk_size) - 1

def iter_chunk_ranges(size: int, chunk_size: int = DEFAULT_CHUNK_SIZE):
    for index in range(chunk_count(size, chunk_size)): yield index, *chunk_range(index, size, chunk_size)

def verify_chunk(data: bytes, digest: str) -> bool: return hmac.compare_digest(sha256_bytes(data), normalize_sha256(digest))
def verify_file(path: Path, digest: str) -> bool:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""): h.update(block)
    return hmac.compare_digest(h.hexdigest(), normalize_sha256(digest))

def merkle_parent(left: str, right: str) -> str: return sha256_bytes(bytes.fromhex(normalize_sha256(left)) + bytes.fromhex(normalize_sha256(right)))
def merkle_root(hashes: list[str]) -> str:
    if not hashes: return sha256_bytes(b"")
    level = [normalize_sha256(x) for x in hashes]
    while len(level) > 1:
        if len(level) % 2: level.append(level[-1])
        level = [merkle_parent(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]

def build_manifest(path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict:
    size = path.stat().st_size; chunks = []; whole = hashlib.sha256()
    with path.open("rb") as source:
        index = 0; offset = 0
        while True:
            data = source.read(chunk_size)
            if not data: break
            whole.update(data); chunks.append({"index": index, "offset": offset, "length": len(data), "hash": sha256_bytes(data)})
            index += 1; offset += len(data)
    return {"blob_hash": whole.hexdigest(), "size": size, "chunk_size": chunk_size, "chunk_count": len(chunks), "merkle_root": merkle_root([c["hash"] for c in chunks]), "chunks": chunks}

def manifest_valid(manifest: dict) -> bool:
    try:
        return int(manifest["chunk_count"]) == len(manifest["chunks"]) and normalize_sha256(manifest["blob_hash"]) and normalize_sha256(manifest["merkle_root"]) and all(c["index"] == i and normalize_sha256(c["hash"]) for i, c in enumerate(manifest["chunks"]))
    except (KeyError, TypeError, ValueError): return False

def bitmap_encode(indices: set[int], total: int) -> str:
    data = bytearray((total + 7) // 8)
    for i in indices:
        if 0 <= i < total: data[i // 8] |= 1 << (i % 8)
    return data.hex()

def bitmap_decode(value: str, total: int) -> set[int]:
    raw = bytes.fromhex(value or ""); return {i for i in range(total) if i // 8 < len(raw) and raw[i // 8] & (1 << (i % 8))}
def missing_chunks(have: set[int], total: int) -> list[int]: return [i for i in range(total) if i not in have]
def completion_ratio(have: set[int], total: int) -> float: return 1.0 if total == 0 else len(have & set(range(total))) / total
def complete(have: set[int], total: int) -> bool: return len(have & set(range(total))) == total
def contiguous_prefix(have: set[int], total: int) -> int:
    i = 0
    while i < total and i in have: i += 1
    return i

def availability_counts(sources: dict[str, set[int]], total: int) -> dict[int, int]: return {i: sum(i in chunks for chunks in sources.values()) for i in range(total)}
def rarest_first(have: set[int], sources: dict[str, set[int]], total: int) -> list[int]:
    counts = availability_counts(sources, total); return sorted(missing_chunks(have, total), key=lambda i: (counts[i] == 0, counts[i], i))
def source_candidates(index: int, sources: dict[str, set[int]]) -> list[str]: return sorted(peer for peer, chunks in sources.items() if index in chunks)
def select_source(index: int, sources: dict[str, set[int]], scores: dict[str, float]) -> str | None:
    candidates = source_candidates(index, sources); return max(candidates, key=lambda p: scores.get(p, 0.0), default=None)
def source_score(throughput: float, rtt_ms: float, failures: int) -> float: return max(0.0, throughput) / max(1.0, rtt_ms) / (1 + max(0, failures))
def endgame_needed(have: set[int], total: int, threshold: int = 4) -> bool: return 0 < total - len(have) <= threshold
def read_ahead_indices(position: int, size: int, chunk_size: int, window_chunks: int = 3) -> list[int]:
    first = min(chunk_count(size, chunk_size), max(0, position // chunk_size)); return list(range(first, min(chunk_count(size, chunk_size), first + max(1, window_chunks))))
def byte_range_chunks(start: int, end: int, size: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[int]:
    if start < 0 or end < start or start >= size: return []
    return list(range(start // chunk_size, min(size - 1, end) // chunk_size + 1))
def coalesce_indices(indices: set[int]) -> list[tuple[int, int]]:
    values = sorted(indices); out = []
    for value in values:
        if not out or value > out[-1][1] + 1: out.append([value, value])
        else: out[-1][1] = value
    return [tuple(x) for x in out]
def expand_ranges(ranges: list[tuple[int, int]]) -> set[int]: return {i for start, end in ranges for i in range(start, end + 1)}

@dataclass
class PeerPolicy:
    resources: dict[str, dict[str, bool]] = field(default_factory=dict)
    orchestrate: bool = False; third_party_source: bool = False; third_party_sink: bool = False; relay: bool = False
    def allowed(self, resource: str, action: str) -> bool: return resource in RESOURCE_TYPES and bool(self.resources.get(resource, {}).get(action, False))
    def may_send(self, resource: str) -> bool: return self.allowed(resource, "send")
    def may_receive(self, resource: str) -> bool: return self.allowed(resource, "receive")
    def may_seed(self, resource: str) -> bool: return self.allowed(resource, "seed")
    def may_orchestrate(self) -> bool: return self.orchestrate
    def may_source_third_party(self) -> bool: return self.third_party_source
    def may_sink_third_party(self) -> bool: return self.third_party_sink
    def may_relay(self) -> bool: return self.relay

def default_deny_policy() -> PeerPolicy: return PeerPolicy()
def backup_policy() -> PeerPolicy: return PeerPolicy(resources={r: {"receive": True, "send": False} for r in RESOURCE_TYPES}, third_party_sink=True)
def collector_policy(resource: str) -> PeerPolicy:
    if resource not in RESOURCE_TYPES: raise ValueError("unsupported resource")
    return PeerPolicy(resources={resource: {"receive": True, "send": False}})
def effective_permission(local: bool, remote: bool) -> bool: return bool(local and remote)
def validate_operation(value: str) -> str:
    value = str(value).upper()
    if value not in TRANSFER_OPERATIONS: raise ValueError("unsupported operation")
    return value

def should_transfer_blob(local_hashes: set[str], digest: str) -> bool: return normalize_sha256(digest) not in {normalize_sha256(x) for x in local_hashes}
def should_transfer_revision(local_revision: int | None, incoming_revision: int) -> bool: return local_revision is None or incoming_revision > local_revision
def tombstone_action(policy: str) -> str:
    if policy not in {"ignore", "archive", "mirror"}: raise ValueError("invalid tombstone policy")
    return policy

def canonical_json(value: object) -> bytes: return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
def content_hash(value: object) -> str: return sha256_bytes(canonical_json(value))
def make_object_identity(origin: str, object_id: str) -> str: return f"{origin}:{object_id}"
def cursor_advance(current: int, entries: list[dict]) -> int: return max([current, *[int(e.get("sequence", current)) for e in entries]])
def page_changes(entries: list[dict], after: int, limit: int) -> list[dict]: return [e for e in entries if int(e.get("sequence", 0)) > after][:max(1, min(limit, 1000))]
def dedupe_changes(entries: list[dict]) -> list[dict]:
    latest = {}
    for e in entries:
        key = (e.get("origin_instance_id"), e.get("object_id"));
        if key not in latest or int(e.get("revision", 0)) >= int(latest[key].get("revision", 0)): latest[key] = e
    return sorted(latest.values(), key=lambda e: int(e.get("sequence", 0)))
def need_metadata(local_hash: str | None, remote_hash: str) -> bool: return not local_hash or not hmac.compare_digest(local_hash, remote_hash)
def need_blob(local_hashes: set[str], remote_hash: str) -> bool: return should_transfer_blob(local_hashes, remote_hash)
def make_need(metadata_ids: list[str], blob_hashes: list[str]) -> dict: return {"need_metadata": list(dict.fromkeys(metadata_ids)), "need_blob": list(dict.fromkeys(blob_hashes))}
def ack_payload(cursor: int, accepted: list[str], rejected: dict[str, str] | None = None) -> dict: return {"cursor": cursor, "accepted": accepted, "rejected": rejected or {}}

def transfer_id() -> str: return secrets.token_urlsafe(18)
def nonce() -> str: return secrets.token_urlsafe(18)
def capability_payload(transfer: str, source: str, target: str, blob_hash: str, expires_at: int, max_bytes: int, ranges=None) -> dict:
    return {"transfer_id": transfer, "source": source, "target": target, "blob_hash": normalize_sha256(blob_hash), "expires_at": int(expires_at), "max_bytes": int(max_bytes), "ranges": ranges or [], "nonce": nonce()}
def sign_capability(payload: dict, secret: bytes) -> str: return hmac.new(secret, canonical_json(payload), hashlib.sha256).hexdigest()
def verify_capability(payload: dict, signature: str, secret: bytes, now: int | None = None) -> bool:
    expected = sign_capability(payload, secret); return hmac.compare_digest(expected, signature) and int(payload.get("expires_at", 0)) >= int(now or time.time())
def capability_envelope(payload: dict, secret: bytes) -> dict: return {"payload": payload, "signature": sign_capability(payload, secret)}
def capability_allows_bytes(payload: dict, amount: int) -> bool: return 0 <= amount <= int(payload.get("max_bytes", -1))
def capability_allows_blob(payload: dict, digest: str) -> bool:
    try: return hmac.compare_digest(normalize_sha256(payload.get("blob_hash", "")), normalize_sha256(digest))
    except ValueError: return False

def safe_https_url(url: str, allow_http: bool = False) -> bool:
    try:
        parsed = urlparse(url); scheme_ok = parsed.scheme == "https" or (allow_http and parsed.scheme == "http")
        return scheme_ok and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError: return False
def public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value); return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved)
    except ValueError: return False
def same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b); return (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)
def redirect_allowed(original: str, redirected: str) -> bool: return safe_https_url(redirected) and same_origin(original, redirected)
def sanitize_peer_id(value: str) -> str:
    value = "".join(c for c in str(value) if c.isalnum() or c in "-_.")[:128]
    if not value: raise ValueError("invalid peer id")
    return value

def retry_delay(attempt: int, base: float = 0.5, maximum: float = 60.0) -> float: return min(maximum, base * (2 ** max(0, attempt)))
def retryable_status(status: int) -> bool: return status in {408, 425, 429, 500, 502, 503, 504}
def terminal_status(status: int) -> bool: return 400 <= status < 500 and not retryable_status(status)
def bounded_parallelism(requested: int, maximum: int = 16) -> int: return max(1, min(int(requested), maximum))
def bounded_page_size(requested: int, maximum: int = 1000) -> int: return max(1, min(int(requested), maximum))
def quota_allows(used: int, incoming: int, quota: int) -> bool: return quota <= 0 or used + incoming <= quota
def bandwidth_budget(rate_bytes_s: int, elapsed_s: float) -> int: return max(0, int(rate_bytes_s * max(0.0, elapsed_s)))
def fair_share(total_slots: int, active_transfers: int) -> int: return max(1, total_slots // max(1, active_transfers))
def replication_needed(current_copies: int, desired_copies: int) -> int: return max(0, desired_copies - current_copies)
def choose_replication_targets(candidates: list[str], existing: set[str], desired: int) -> list[str]: return [p for p in candidates if p not in existing][:replication_needed(len(existing), desired)]
def transfer_progress(received_bytes: int, total_bytes: int) -> float: return 1.0 if total_bytes <= 0 else min(1.0, max(0.0, received_bytes / total_bytes))
def transfer_eta(remaining_bytes: int, bytes_per_second: float) -> float | None: return None if bytes_per_second <= 0 else max(0, remaining_bytes) / bytes_per_second
def resumable_offset(path: Path) -> int: return path.stat().st_size if path.exists() else 0
def preallocate(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as target: target.truncate(size)
def write_chunk(path: Path, offset: int, data: bytes) -> int:
    with path.open("r+b") as target: target.seek(offset); return target.write(data)
def read_chunk(path: Path, offset: int, length: int) -> bytes:
    with path.open("rb") as source: source.seek(offset); return source.read(length)
def atomic_state_write(path: Path, state: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_bytes(canonical_json(state)); tmp.replace(path)
def state_read(path: Path) -> dict: return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
def transfer_state(blob_hash: str, total: int, have: set[int], status: str = "active") -> dict: return {"blob_hash": normalize_sha256(blob_hash), "total_chunks": total, "have": bitmap_encode(have, total), "status": status, "updated_at": int(time.time())}
def transfer_state_have(state: dict) -> set[int]: return bitmap_decode(state.get("have", ""), int(state.get("total_chunks", 0)))
def transfer_pause(state: dict) -> dict: state = dict(state); state["status"] = "paused"; return state
def transfer_resume(state: dict) -> dict: state = dict(state); state["status"] = "active"; return state
def transfer_cancel(state: dict) -> dict: state = dict(state); state["status"] = "cancelled"; return state
def transfer_finished(state: dict) -> bool: return state.get("status") == "complete" or complete(transfer_state_have(state), int(state.get("total_chunks", 0)))
def source_health(successes: int, failures: int) -> float: return successes / max(1, successes + failures)
def source_usable(health: float, minimum: float = 0.2) -> bool: return health >= minimum
def quarantine_reason(hash_ok: bool, policy_ok: bool, scan_ok: bool) -> str | None:
    if not hash_ok: return "hash_mismatch"
    if not policy_ok: return "policy_rejected"
    if not scan_ok: return "malware_scan_failed"
    return None
def audit_event(action: str, peer: str, object_id: str = "", detail: dict | None = None) -> dict: return {"timestamp": int(time.time()), "action": action, "peer": peer, "object_id": object_id, "detail": detail or {}}
def metadata_only(policy: dict) -> bool: return bool(policy.get("metadata_only", False))
def mime_allowed(mime: str, allowed: list[str] | None) -> bool: return not allowed or mime in allowed
def size_allowed(size: int, maximum: int | None) -> bool: return maximum is None or size <= maximum
def tags_allowed(tags: set[str], required: set[str]) -> bool: return not required or bool(tags & required)
def transfer_policy_allows(size: int, mime: str, tags: set[str], policy: dict) -> bool: return size_allowed(size, policy.get("max_size")) and mime_allowed(mime, policy.get("mime_types")) and tags_allowed(tags, set(policy.get("tags", [])))
def canonical_contact(value: dict) -> dict: return {k: value[k] for k in sorted(value) if k not in {"local_id", "sync_timestamp", "peer_metadata"}}
def canonical_calendar(value: dict) -> dict: return {k: value[k] for k in sorted(value) if k not in {"local_id", "sync_timestamp", "peer_metadata"}}
def canonical_task(value: dict) -> dict: return {k: value[k] for k in sorted(value) if k not in {"local_id", "sync_timestamp", "peer_metadata"}}
def domain_uid(value: dict) -> str: return str(value.get("uid") or value.get("UID") or "")
def conflict_required(local_origin: str, remote_origin: str, locally_modified: bool) -> bool: return locally_modified and local_origin != remote_origin
def conflict_record(local: dict, remote: dict) -> dict: return {"local": local, "remote": remote, "status": "unresolved"}
def restore_job(blob_hash: str, target: str) -> dict: return {"operation": "RESTORE", "blob_hash": normalize_sha256(blob_hash), "target": target, "explicit": True}
def copy_job(blob_hash: str, source: str, target: str) -> dict: return {"operation": "COPY", "blob_hash": normalize_sha256(blob_hash), "source": source, "target": target}
def replicate_job(blob_hash: str, source: str, targets: list[str]) -> dict: return {"operation": "REPLICATE", "blob_hash": normalize_sha256(blob_hash), "source": source, "targets": targets}
def verify_job(blob_hash: str, target: str) -> dict: return {"operation": "VERIFY", "blob_hash": normalize_sha256(blob_hash), "target": target}
def repair_job(blob_hash: str, sources: list[str], target: str) -> dict: return {"operation": "REPAIR", "blob_hash": normalize_sha256(blob_hash), "sources": sources, "target": target}
def relay_mode(value: str) -> str:
    if value not in {"stream", "cache", "store-and-forward"}: raise ValueError("invalid relay mode")
    return value
def chunk_request(blob_hash: str, index: int) -> dict: return {"blob_hash": normalize_sha256(blob_hash), "chunk": int(index)}
def chunk_receipt(blob_hash: str, index: int, digest: str, size: int) -> dict: return {"blob_hash": normalize_sha256(blob_hash), "chunk": int(index), "hash": normalize_sha256(digest), "size": int(size), "verified": True}
def source_advertisement(peer: str, blob_hash: str, chunks: set[int], total: int) -> dict: return {"peer": sanitize_peer_id(peer), "blob_hash": normalize_sha256(blob_hash), "availability": bitmap_encode(chunks, total), "total_chunks": total}
def parse_source_advertisement(value: dict) -> set[int]: return bitmap_decode(value["availability"], int(value["total_chunks"]))
def scheduler_plan(have: set[int], sources: dict[str, set[int]], scores: dict[str, float], total: int, slots: int) -> list[tuple[int, str]]:
    plan = []
    for index in rarest_first(have, sources, total):
        source = select_source(index, sources, scores)
        if source: plan.append((index, source))
        if len(plan) >= bounded_parallelism(slots): break
    return plan
def stream_plan(position: int, size: int, chunk_size: int, have: set[int], sources: dict[str, set[int]], scores: dict[str, float], window: int = 3) -> list[tuple[int, str]]:
    wanted = [i for i in read_ahead_indices(position, size, chunk_size, window) if i not in have]; return [(i, select_source(i, sources, scores)) for i in wanted if select_source(i, sources, scores)]
def idempotency_key(operation: str, blob_hash: str, target: str) -> str: return sha256_bytes(f"{operation}:{normalize_sha256(blob_hash)}:{target}".encode())
def request_fingerprint(method: str, path: str, body: bytes) -> str: return sha256_bytes(method.upper().encode() + b"\0" + path.encode() + b"\0" + body)
def freshness_ok(timestamp: int, now: int | None = None, window: int = 300) -> bool: return abs(int(now or time.time()) - int(timestamp)) <= window
def nonce_unused(value: str, seen: set[str]) -> bool: return bool(value) and value not in seen
def mark_nonce(value: str, seen: set[str]) -> None: seen.add(value)
def max_bytes_for_ranges(ranges: list[tuple[int, int]]) -> int: return sum(max(0, end - start + 1) for start, end in ranges)
def chunk_hash_map(manifest: dict) -> dict[int, str]: return {int(c["index"]): normalize_sha256(c["hash"]) for c in manifest.get("chunks", [])}
def manifest_chunk(manifest: dict, index: int) -> dict | None: return next((c for c in manifest.get("chunks", []) if int(c.get("index", -1)) == index), None)
def compatible_version(local: list[int], remote: list[int]) -> int | None:
    common = sorted(set(local) & set(remote)); return common[-1] if common else None
def negotiate_chunk_size(local: int, remote: int, maximum: int = 16 * 1024 * 1024) -> int: return max(64 * 1024, min(local, remote, maximum))
def capability_summary() -> dict: return {"protocol": "sofp", "versions": [1], "hashes": ["sha256"], "merkle": True, "range": True, "multi_source": True, "third_party_transfer": True, "relay": True, "operations": sorted(TRANSFER_OPERATIONS), "preferred_chunk_bytes": DEFAULT_CHUNK_SIZE}
