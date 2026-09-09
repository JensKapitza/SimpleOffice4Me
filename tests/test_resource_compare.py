from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.resource_compare import SAMPLE_BYTES, compare_files
from app.resource_completeness import verify_complete
from app.resource_local import LocalResourceProvider


class ResourceCompareTests(unittest.TestCase):
    def setUp(self):
        self.left_temp = tempfile.TemporaryDirectory()
        self.right_temp = tempfile.TemporaryDirectory()
        self.left_root = Path(self.left_temp.name)
        self.right_root = Path(self.right_temp.name)
        self.left = LocalResourceProvider(self.left_root)
        self.right = LocalResourceProvider(self.right_root)

    def tearDown(self):
        self.left_temp.cleanup()
        self.right_temp.cleanup()

    def test_metadata_mode_reads_no_file_data(self):
        payload = b"A" * (SAMPLE_BYTES + 4096)
        (self.left_root / "large.bin").write_bytes(payload)
        (self.right_root / "large.bin").write_bytes(payload)
        result = compare_files(self.left, "large.bin", self.right, "large.bin", mode="metadata")
        self.assertEqual("probably_identical", result.status)
        self.assertEqual("metadata", result.confidence)
        self.assertEqual(0, result.bytes_read_left)
        self.assertEqual(0, result.bytes_read_right)

    def test_fast_large_file_reads_only_first_sample(self):
        payload = b"A" * (SAMPLE_BYTES + 4096)
        (self.left_root / "large.bin").write_bytes(payload)
        (self.right_root / "large.bin").write_bytes(payload)
        result = compare_files(self.left, "large.bin", self.right, "large.bin", mode="fast")
        self.assertEqual("probably_identical", result.status)
        self.assertEqual(SAMPLE_BYTES, result.bytes_read_left)
        self.assertEqual(SAMPLE_BYTES, result.bytes_read_right)

    def test_full_comparison_detects_change_after_equal_first_megabyte(self):
        common = b"A" * SAMPLE_BYTES
        (self.left_root / "large.bin").write_bytes(common + b"LEFT")
        (self.right_root / "large.bin").write_bytes(common + b"RGHT")
        fast = compare_files(self.left, "large.bin", self.right, "large.bin", mode="fast")
        full = compare_files(self.left, "large.bin", self.right, "large.bin", mode="full")
        self.assertEqual("probably_identical", fast.status)
        self.assertEqual("different", full.status)

    def test_recursive_completeness_allows_extra_target_files(self):
        (self.left_root / "folder").mkdir()
        (self.right_root / "folder").mkdir()
        (self.left_root / "folder" / "required.txt").write_text("required", encoding="utf-8")
        (self.right_root / "folder" / "required.txt").write_text("required", encoding="utf-8")
        (self.right_root / "folder" / "extra.txt").write_text("wanted extra", encoding="utf-8")
        result = verify_complete(self.left, "", self.right, "")
        self.assertTrue(result["complete"])
        self.assertTrue(result["extra_target_allowed"])
        self.assertEqual(1, result["checked_files"])

    def test_recursive_completeness_detects_missing_nested_file(self):
        (self.left_root / "a" / "b").mkdir(parents=True)
        (self.right_root / "a" / "b").mkdir(parents=True)
        (self.left_root / "a" / "b" / "missing.pdf").write_bytes(b"pdf")
        result = verify_complete(self.left, "", self.right, "")
        self.assertFalse(result["complete"])
        self.assertEqual("a/b/missing.pdf", result["required_missing"][0]["path"])


if __name__ == "__main__":
    unittest.main()
