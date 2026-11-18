"""Deterministic bounds for visibility-aware search sidecar reads."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import g

from app import app
from app.document_store import DocumentStore


class DocumentSearchCostTests(unittest.TestCase):
    page_size = 5
    document_count = 160

    def _make_store(self, hidden_ratio):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / "documents"
        root.mkdir(parents=True)
        store = DocumentStore(root)
        store.initialize()
        visible = []
        for index in range(self.document_count):
            name = f"record-{index:03}.txt"
            path = root / name
            path.write_text("searchcostpaginationtoken", encoding="utf-8")
            store._scan_file(path, force_hash=True)
            is_hidden = self._is_hidden(index, hidden_ratio)
            if not is_hidden:
                visible.append(name)
            if is_hidden:
                document = store.get_document(path)
                store.update_metadata(
                    document["document_id"],
                    attributes={"chat_attachment": {
                        "visibility": "chat",
                        "allowed_local_users": ["another-user"],
                    }},
                    author="test-setup",
                )
        return store, visible

    @staticmethod
    def _is_hidden(index, ratio):
        # Each pattern repeats at a fixed interval, so expected cursor work is
        # independently computable without relying on elapsed time.
        if ratio == "10%":
            return index % 10 == 0
        if ratio == "50%":
            return index % 2 == 0
        if ratio == "90%":
            return index % 10 != 0
        raise AssertionError(ratio)

    @staticmethod
    def _expected_reads(visible_names, visible_target):
        target_name = visible_names[visible_target - 1]
        return int(target_name.removeprefix("record-").removesuffix(".txt")) + 1

    def _search_and_count_reads(self, store, page):
        json_sidecars = []
        original_read = store._read_json

        def record_read(path, default):
            candidate = Path(path)
            if candidate.parent == store.documents and candidate.suffix == ".json":
                json_sidecars.append(candidate)
            return original_read(path, default)

        with patch.object(store, "_read_json", side_effect=record_read):
            with app.test_request_context("/documents/search"):
                g.user = {"username": "search-viewer", "is_admin": False}
                result = store.search_page("searchcostpaginationtoken", page=page, page_size=self.page_size)
        return result, json_sidecars

    def test_benchmark_cli_counts_only_visible_matches_in_isolated_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            configured_root = Path(directory) / "existing-archive"
            run = subprocess.run(
                [sys.executable, "-m", "tools.benchmark_document_search", "--documents", "4",
                 "--hidden-fractions", "0.5,1", "--page-size", "1", "--pages", "1,2",
                 "--repeats", "2"],
                env={**os.environ, "SIMPLEOFFICE_DOCUMENT_ROOT": str(configured_root)},
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(0, run.returncode, run.stderr)
            self.assertFalse(configured_root.exists())
        report = json.loads(run.stdout)
        self.assertEqual(2, report["repeats"])
        results = [(r["visible_results"], r["has_next"], r["metadata_reads_per_search"]["max"])
                   for r in report["cases"]]
        self.assertEqual([(1, True, 4), (1, False, 4), (0, False, 4), (0, False, 4)], results)

    def test_first_and_deep_pages_read_only_through_one_extra_visible_match(self):
        # These ratios exercise low, medium, and high hidden-row fractions.
        # The first page needs 6 visible matches; page 3 needs 16.
        for ratio in ("10%", "50%", "90%"):
            with self.subTest(hidden_fraction=ratio):
                store, visible_names = self._make_store(ratio)
                self.assertGreaterEqual(len(visible_names), 16)
                for page, visible_start in ((1, 0), (3, 10)):
                    with self.subTest(page=page):
                        result, reads = self._search_and_count_reads(store, page)
                        expected_paths = visible_names[visible_start:visible_start + self.page_size]
                        self.assertEqual(expected_paths, [row["path"] for row in result["results"]])
                        self.assertTrue(result["has_next"])
                        expected_count = self._expected_reads(visible_names, visible_start + self.page_size + 1)
                        self.assertEqual(expected_count, len(reads))
                        self.assertEqual(len(reads), len({path.name for path in reads}))


if __name__ == "__main__":
    unittest.main()
