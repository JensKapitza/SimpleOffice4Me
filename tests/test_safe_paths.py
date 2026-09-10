import tempfile
import unittest
from pathlib import Path

from app.safe_paths import (
    normalize_path,
    relative_under,
    resolve_directory_under,
    resolve_file_under,
    resolve_for_write_under,
    resolve_under,
    safe_filename,
)


class SafePathsTest(unittest.TestCase):
    def test_nested_path_inside_root_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested = root / "customers" / "42"
            nested.mkdir(parents=True)
            self.assertEqual(nested.resolve(), resolve_under(root, "customers/42", strict=True))
            self.assertEqual(Path("customers/42"), relative_under(root, "customers/42", require_name=True))

    def test_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for value in ("../../etc/passwd", "a/../../outside.txt", r"..\..\etc\passwd"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    resolve_under(root, value)

    def test_normalization_collapses_safe_dot_segments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "a" / "b"
            folder.mkdir(parents=True)
            self.assertEqual(folder.resolve(), resolve_under(root, "a/./x/../b", strict=True))

    def test_absolute_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for value in (Path(temp).parent / "outside.txt", r"C:\Windows\win.ini", r"\\server\share\secret.txt"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    resolve_under(root, value)

    def test_windows_drive_relative_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                resolve_under(Path(temp), r"C:relative\secret.txt")

    def test_sibling_prefix_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            root = parent / "root"
            sibling = parent / "root-other"
            root.mkdir()
            sibling.mkdir()
            with self.assertRaises(ValueError):
                resolve_under(root, "../root-other/secret.txt")

    def test_relative_path_requires_resource_name_when_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for value in ("", "."):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    relative_under(root, value, require_name=True)

    def test_nul_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                resolve_under(Path(temp), "safe\x00outside")

    def test_symlink_escape_is_rejected_for_read_and_write(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            link = root / "escape"
            try:
                link.symlink_to(Path(outside), target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")
            with self.assertRaises(ValueError):
                resolve_under(root, "escape/secret.txt")
            with self.assertRaises(ValueError):
                resolve_for_write_under(root, "escape/new.txt")

    def test_file_and_directory_helpers_validate_type(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "folder"
            folder.mkdir()
            file_path = folder / "file.txt"
            file_path.write_text("ok", encoding="utf-8")
            self.assertEqual(file_path.resolve(), resolve_file_under(root, "folder/file.txt"))
            self.assertEqual(folder.resolve(), resolve_directory_under(root, "folder"))
            with self.assertRaises(ValueError):
                resolve_file_under(root, "folder")
            with self.assertRaises(ValueError):
                resolve_directory_under(root, "folder/file.txt")

    def test_write_target_may_be_missing_but_parent_must_exist_inside_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "folder"
            folder.mkdir()
            target = resolve_for_write_under(root, "folder/new.txt")
            self.assertEqual((folder / "new.txt").resolve(strict=False), target)

    def test_trusted_path_normalizer_uses_absolute_realpath(self):
        with tempfile.TemporaryDirectory() as temp:
            normalized = normalize_path(Path(temp) / ".")
            self.assertTrue(normalized.is_absolute())
            self.assertEqual(Path(temp).resolve(), normalized)

    def test_filename_is_reduced_to_one_safe_component(self):
        self.assertEqual("passwd", safe_filename("../../etc/passwd"))
        self.assertEqual("evil.txt", safe_filename(r"..\..\evil.txt"))
        self.assertNotIn("/", safe_filename("customer/report 2026.pdf"))
        self.assertNotIn("\\", safe_filename("customer/report 2026.pdf"))


if __name__ == "__main__":
    unittest.main()
