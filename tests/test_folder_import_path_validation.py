"""Regression tests for document folder-import path validation."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.folder_import_validation import validate_upload_batch


class FolderImportPathValidationTests(unittest.TestCase):
    def test_rejects_unsafe_paths(self):
        invalid = (
            "../secret.txt",
            "root/../secret.txt",
            "/absolute/secret.txt",
            "root//secret.txt",
            "root/CON/secret.txt",
            "root/CON.txt/secret.txt",
            "root/LPT1.log/secret.txt",
            "root/folder./secret.txt",
            "root/folder /secret.txt",
            "root/folder:bad/secret.txt",
        )
        files = [SimpleNamespace(filename="secret.txt")]
        for path in invalid:
            with self.subTest(path=path):
                self.assertIsNotNone(
                    validate_upload_batch(files, [path], folder_import=True)
                )

    def test_preserves_same_basename_in_different_directories(self):
        files = [SimpleNamespace(filename="photo.jpg")] * 2
        self.assertIsNone(
            validate_upload_batch(
                files,
                ["root/first/photo.jpg", "root/second/photo.jpg"],
                folder_import=True,
            )
        )

    def test_rejects_case_insensitive_duplicate_destination(self):
        files = [SimpleNamespace(filename="photo.jpg")] * 2
        self.assertIsNotNone(
            validate_upload_batch(
                files,
                ["root/Photos/photo.jpg", "root/photos/photo.jpg"],
                folder_import=True,
            )
        )

    def test_rejects_missing_relative_paths(self):
        files = [SimpleNamespace(filename="photo.jpg")]
        self.assertIsNotNone(validate_upload_batch(files, [], folder_import=True))

    def test_rejects_more_than_one_thousand_files(self):
        files = [SimpleNamespace(filename=f"file-{i}.txt") for i in range(1001)]
        self.assertIsNotNone(validate_upload_batch(files, [], folder_import=False))

    def test_accepts_one_thousand_distinct_files(self):
        files = [SimpleNamespace(filename=f"file-{i}.txt") for i in range(1000)]
        paths = [f"root/file-{i}.txt" for i in range(1000)]
        self.assertIsNone(validate_upload_batch(files, paths, folder_import=True))

    def test_rejects_mismatched_file_and_relative_path(self):
        files = [SimpleNamespace(filename="actual.txt")]
        self.assertIsNotNone(
            validate_upload_batch(files, ["root/other.txt"], folder_import=True)
        )

    def test_rejects_non_normalized_unicode_path(self):
        files = [SimpleNamespace(filename="file.txt")]
        self.assertIsNotNone(
            validate_upload_batch(files, ["root/cafe" + chr(0x0301) + "/file.txt"], folder_import=True)
        )

    def test_rejects_overlong_path_segment(self):
        files = [SimpleNamespace(filename="file.txt")]
        self.assertIsNotNone(
            validate_upload_batch(files, ["root/" + "a" * 256 + "/file.txt"], folder_import=True)
        )

    def test_rejects_overlong_relative_path(self):
        files = [SimpleNamespace(filename="file.txt")]
        path = "root/" + "/".join(["segment"] * 600) + "/file.txt"
        self.assertIsNotNone(validate_upload_batch(files, [path], folder_import=True))

    def test_accepts_single_file_without_folder_metadata(self):
        files = [SimpleNamespace(filename="photo.jpg")]
        self.assertIsNone(validate_upload_batch(files, [], folder_import=False))


if __name__ == "__main__":
    unittest.main()
