from __future__ import annotations

import time
import unittest
from pathlib import Path

from app.v3_search import SearchContext, SearchHit, SearchRegistry


class SearchRegistryTests(unittest.TestCase):
    def setUp(self):
        self.context = SearchContext(
            Path("/tmp"),
            "alice",
            False,
            frozenset({"documents"}),
        )

    def test_feature_scoped_provider_is_not_called_when_denied(self):
        calls = []
        registry = SearchRegistry()
        registry.register(
            "contacts",
            lambda q, c, l: calls.append(q) or [],
            feature="contacts",
        )
        hits, errors = registry.search("alice", self.context)
        self.assertEqual([], hits)
        self.assertEqual([], errors)
        self.assertEqual([], calls)

    def test_provider_failure_does_not_block_other_results(self):
        registry = SearchRegistry()
        registry.register(
            "broken",
            lambda q, c, l: (_ for _ in ()).throw(RuntimeError("private")),
        )
        registry.register(
            "ok",
            lambda q, c, l: [
                SearchHit(
                    "ok",
                    "Dokument",
                    "document",
                    "1",
                    "Titel",
                    "",
                    "/x",
                    10,
                )
            ],
        )
        hits, errors = registry.search("x", self.context)
        self.assertEqual(["Titel"], [row.title for row in hits])
        self.assertEqual(["broken"], errors)

    def test_slow_provider_is_bounded_by_timeout(self):
        registry = SearchRegistry()

        def slow(q, c, l):
            time.sleep(0.2)
            return []

        registry.register("slow", slow)
        started = time.monotonic()
        hits, errors = registry.search(
            "x",
            self.context,
            timeout_seconds=0.03,
        )
        self.assertLess(time.monotonic() - started, 0.15)
        self.assertEqual([], hits)
        self.assertEqual(["slow"], errors)

    def test_results_are_bounded(self):
        registry = SearchRegistry()
        registry.register(
            "many",
            lambda q, c, l: [
                SearchHit(
                    "many",
                    "x",
                    "x",
                    str(i),
                    str(i),
                    "",
                    "/x",
                    i,
                )
                for i in range(100)
            ],
        )
        hits, _ = registry.search("x", self.context, limit=7)
        self.assertEqual(7, len(hits))


if __name__ == "__main__":
    unittest.main()
