"""Unit: volatile per-user LRU message cache (Issue #1136, slice 2)."""
from __future__ import annotations

import unittest

from app.mail_message_cache import (
    DEFAULT_MAX_BYTES,
    MAX_MAX_BYTES,
    MIN_MAX_BYTES,
    VolatileMailCache,
    cache_limit_bytes,
)


class MailMessageCacheTests(unittest.TestCase):
    def test_limit_is_clamped_to_200_to_300_mib(self):
        self.assertEqual(MIN_MAX_BYTES, cache_limit_bytes(1))
        self.assertEqual(MAX_MAX_BYTES, cache_limit_bytes(10 ** 12))
        self.assertEqual(DEFAULT_MAX_BYTES, cache_limit_bytes(None))
        self.assertEqual(DEFAULT_MAX_BYTES, cache_limit_bytes("nope"))
        self.assertEqual(220 * 1024 * 1024, cache_limit_bytes(220 * 1024 * 1024))

    def test_put_get_roundtrip_and_stats(self):
        cache = VolatileMailCache(max_bytes=1024)
        self.assertIsNone(cache.get("a", "acc", "INBOX", "7"))
        self.assertTrue(cache.put("a", "acc", "INBOX", "7", b"raw-bytes"))
        self.assertEqual(b"raw-bytes", cache.get("a", "acc", "INBOX", "7"))
        stats = cache.stats()
        self.assertEqual(1, stats["entries"])
        self.assertEqual(9, stats["bytes"])
        self.assertEqual(1, stats["hits"])
        self.assertEqual(1, stats["misses"])

    def test_namespaces_are_isolated(self):
        cache = VolatileMailCache(max_bytes=1024)
        cache.put("alice", "acc", "INBOX", "7", b"alice-mail")
        self.assertIsNone(cache.get("bob", "acc", "INBOX", "7"))
        self.assertIsNone(cache.get("alice", "other", "INBOX", "7"))
        self.assertIsNone(cache.get("alice", "acc", "Archiv", "7"))
        self.assertEqual(b"alice-mail", cache.get("alice", "acc", "INBOX", "7"))

    def test_lru_evicts_least_recently_used_first(self):
        cache = VolatileMailCache()
        cache._max_bytes = 10  # white-box shrink for eviction testing only
        cache.put("a", "acc", "INBOX", "1", b"12345")
        cache.put("a", "acc", "INBOX", "2", b"12345")
        self.assertEqual(b"12345", cache.get("a", "acc", "INBOX", "1"))
        cache.put("a", "acc", "INBOX", "3", b"12345")
        self.assertEqual(b"12345", cache.get("a", "acc", "INBOX", "1"))
        self.assertIsNone(cache.get("a", "acc", "INBOX", "2"))
        self.assertEqual(b"12345", cache.get("a", "acc", "INBOX", "3"))

    def test_oversize_entries_are_rejected_and_invalidate_scopes(self):
        cache = VolatileMailCache()
        cache._max_bytes = 1024  # white-box shrink for rejection testing only
        self.assertFalse(cache.put("a", "acc", "INBOX", "9", b"x" * 2048))
        cache.put("a", "acc", "INBOX", "1", b"one")
        cache.put("a", "acc", "INBOX", "2", b"two")
        cache.put("b", "acc", "INBOX", "1", b"other-user")
        self.assertEqual(2, cache.invalidate_account("a", "acc"))
        self.assertIsNone(cache.get("a", "acc", "INBOX", "1"))
        self.assertEqual(b"other-user", cache.get("b", "acc", "INBOX", "1"))
