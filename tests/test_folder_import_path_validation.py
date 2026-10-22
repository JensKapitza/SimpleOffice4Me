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

    def test_accepts_single_file_without_folder_metadata(self):
        files = [SimpleNamespace(filename="photo.jpg")]
        self.assertIsNone(validate_upload_batch(files, [], folder_import=False))


if __name__ == "__main__":
    unittest.main()
