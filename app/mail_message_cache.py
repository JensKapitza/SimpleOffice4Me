"""Volatile, per-user LRU cache for fetched IMAP message bytes (Issue #1136).

The cache lives only in process memory: a restart empties it by
construction and nothing is ever written to disk. Entries are isolated
by (actor, account) namespaces. Only raw message bytes are cached;
flags are always refreshed with a lightweight FLAGS fetch so the cache
can never trigger server-side changes.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

DEFAULT_MAX_BYTES = 256 * 1024 * 1024
MIN_MAX_BYTES = 200 * 1024 * 1024
MAX_MAX_BYTES = 300 * 1024 * 1024


def cache_limit_bytes(value: Any) -> int:
    """Clamp the configured cache size to the 200-300 MiB range."""
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_BYTES
    return max(MIN_MAX_BYTES, min(limit, MAX_MAX_BYTES))


class VolatileMailCache:
    """Thread-safe byte-accounted LRU cache for raw message content."""

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES):
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str, str, str], bytes] = OrderedDict()
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._max_bytes = cache_limit_bytes(max_bytes)

    @staticmethod
    def _key(actor: str, account_id: str, folder: str, uid: str) -> tuple[str, str, str, str]:
        return (str(actor or ""), str(account_id or ""), str(folder or ""), str(uid or ""))

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def configure(self, max_bytes: Any) -> int:
        with self._lock:
            self._max_bytes = cache_limit_bytes(max_bytes)
            self._evict_locked()
            return self._max_bytes

    def get(self, actor: str, account_id: str, folder: str, uid: str) -> bytes | None:
        key = self._key(actor, account_id, folder, uid)
        with self._lock:
            if key not in self._entries:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return bytes(self._entries[key])

    def put(self, actor: str, account_id: str, folder: str, uid: str, raw: bytes) -> bool:
        data = bytes(raw or b"")
        if not data or len(data) > self._max_bytes:
            return False
        key = self._key(actor, account_id, folder, uid)
        with self._lock:
            if key in self._entries:
                self._bytes -= len(self._entries.pop(key))
            self._entries[key] = data
            self._entries.move_to_end(key)
            self._bytes += len(data)
            self._evict_locked()
            return key in self._entries

    def invalidate_account(self, actor: str, account_id: str) -> int:
        prefix = (str(actor or ""), str(account_id or ""))
        with self._lock:
            doomed = [key for key in self._entries if key[:2] == prefix]
            for key in doomed:
                self._bytes -= len(self._entries.pop(key))
            return len(doomed)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "max_bytes": self._max_bytes,
                "hits": self._hits,
                "misses": self._misses,
            }

    def _evict_locked(self) -> None:
        while self._entries and self._bytes > self._max_bytes:
            _, data = self._entries.popitem(last=False)
            self._bytes -= len(data)


_cache: VolatileMailCache | None = None
_cache_lock = threading.Lock()


def message_cache() -> VolatileMailCache:
    """Return the process-global volatile cache (restart empties it)."""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = VolatileMailCache()
    return _cache
