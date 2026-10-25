"""Regression tests for document folder-import path validation."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.folder_import_validation import (
    MAX_FOLDER_IMPORT_DEPTH,
    MAX_RELATIVE_PATH_MANIFEST_BYTES,
    decode_relative_path_manifest,
    decode_source_identity_manifest,
    stable_source_folder_id,
    validate_upload_batch,
    validate_source_identity_groups,
)


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

    def test_decodes_one_bounded_manifest_for_one_thousand_paths(self):
        paths = [f"root/file-{i}.txt" for i in range(1000)]
        decoded, error = decode_relative_path_manifest(json.dumps(paths), 1000)
        self.assertIsNone(error)
        self.assertEqual(paths, decoded)

    def test_decodes_optional_source_ids_aligned_with_files(self):
        source_id = "ab" * 32
        identities, error = decode_source_identity_manifest(
            json.dumps([source_id, ""]), 2,
        )
        self.assertIsNone(error)
        self.assertEqual([source_id, ""], identities)
        identities, error = decode_source_identity_manifest("", 2)
        self.assertIsNone(error)
        self.assertEqual(["", ""], identities)

    def test_rejects_malformed_or_unbounded_source_id_manifest(self):
        source_id = "cd" * 32
        for candidate, count in (
            (json.dumps([source_id]), 2),
            (json.dumps(["not-a-source-id"]), 1),
            (json.dumps(["CD" * 32]), 1),
            (" " * 100_001, 0),
            (json.dumps(["\ud800"]), 1),
        ):
            with self.subTest(candidate=candidate[:40], count=count):
                identities, error = decode_source_identity_manifest(candidate, count)
                self.assertIsNone(identities)
                self.assertIsNotNone(error)

    def test_source_identity_is_consistent_per_selected_root(self):
        source_a, source_b = "ab" * 32, "cd" * 32
        paths = ["Telegram/a.txt", "Telegram/nested/b.txt", "Photos/c.jpg"]
        self.assertIsNone(validate_source_identity_groups(paths, [source_a, source_a, source_b]))
        self.assertIn(
            "widersprüchliche",
            validate_source_identity_groups(paths, [source_a, source_b, source_b]),
        )
        self.assertIsNone(validate_source_identity_groups(paths, ["", "", ""]))

    def test_stable_source_folder_identity_is_per_user_and_requires_opaque_id(self):
        source_id = "ab" * 32
        first = stable_source_folder_id("user-a", source_id, "Telegram/2026")
        self.assertEqual(first, stable_source_folder_id("user-a", source_id, "Telegram/2026"))
        self.assertNotEqual(first, stable_source_folder_id("user-b", source_id, "Telegram/2026"))
        with self.assertRaises(ValueError):
            stable_source_folder_id("user-a", "invalid", "Telegram/2026")

    def test_rejects_manifest_over_memory_budget(self):
        decoded, error = decode_relative_path_manifest(
            " " * (MAX_RELATIVE_PATH_MANIFEST_BYTES + 1), 0
        )
        self.assertIsNone(decoded)
        self.assertIn("zu groß", error)

    def test_rejects_non_string_manifest_entries(self):
        decoded, error = decode_relative_path_manifest('["root/file.txt", 1]', 2)
        self.assertIsNone(decoded)
        self.assertIn("Dateipfade fehlen", error)

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

    def test_rejects_unpaired_surrogate_without_raising(self):
        files = [SimpleNamespace(filename="file.txt")]
        self.assertIsNotNone(
            validate_upload_batch(
                files, ["root/" + chr(0xD800) + "/file.txt"], folder_import=True
            )
        )

    def test_rejects_overlong_relative_path(self):
        files = [SimpleNamespace(filename="file.txt")]
        path = "root/" + "/".join(["segment"] * 600) + "/file.txt"
        self.assertIsNotNone(validate_upload_batch(files, [path], folder_import=True))

    def test_accepts_maximum_folder_depth(self):
        files = [SimpleNamespace(filename="file.txt")]
        path = "root/" + "/".join(["dir"] * MAX_FOLDER_IMPORT_DEPTH) + "/file.txt"
        self.assertIsNone(validate_upload_batch(files, [path], folder_import=True))

    def test_rejects_folder_depth_over_limit(self):
        files = [SimpleNamespace(filename="file.txt")]
        path = "root/" + "/".join(["dir"] * (MAX_FOLDER_IMPORT_DEPTH + 1)) + "/file.txt"
        self.assertIn(
            "zu tief",
            validate_upload_batch(files, [path], folder_import=True),
        )

    def test_rejects_oversized_path_component(self):
        files = [SimpleNamespace(filename="file.txt")]
        self.assertIsNotNone(
            validate_upload_batch(files, ["root/" + "x" * 256 + "/file.txt"], folder_import=True)
        )

    def test_rejects_oversized_full_path(self):
        files = [SimpleNamespace(filename="file.txt")]
        relative = "/".join(["segment" * 25] * 25) + "/file.txt"
        self.assertIsNotNone(validate_upload_batch(files, [relative], folder_import=True))

    def test_rejects_unpaired_unicode_surrogate_without_exception(self):
        files = [SimpleNamespace(filename="file.txt")]
        self.assertIsNotNone(
            validate_upload_batch(
                files, ["root/" + chr(0xD800) + "/file.txt"], folder_import=True
            )
        )

    def test_rejects_non_string_relative_path_without_exception(self):
        files = [SimpleNamespace(filename="file.txt")]
        self.assertIsNotNone(
            validate_upload_batch(files, [None], folder_import=True)
        )

    def test_accepts_single_file_without_folder_metadata(self):
        files = [SimpleNamespace(filename="photo.jpg")]
        self.assertIsNone(validate_upload_batch(files, [], folder_import=False))


if __name__ == "__main__":
    unittest.main()
