import tempfile
import unittest
from pathlib import Path

from app.safe_paths import relative_under, resolve_under, safe_filename


class SafePathsTest(unittest.TestCase):
    def test_nested_path_inside_root_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nested = root / "customers" / "42"
            nested.mkdir(parents=True)
            self.assertEqual(nested, resolve_under(root, "customers/42", strict=True))
            self.assertEqual(Path("customers/42"), relative_under(root, "customers/42", require_name=True))

    def test_parent_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                resolve_under(root, "../../etc/passwd")
            with self.assertRaises(ValueError):
                resolve_under(root, r"..\..\etc\passwd")

    def test_absolute_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                resolve_under(root, Path(temp).parent / "outside.txt")
            with self.assertRaises(ValueError):
                resolve_under(root, r"C:\Windows\win.ini")
            with self.assertRaises(ValueError):
                resolve_under(root, r"\\server\share\secret.txt")

    def test_windows_drive_relative_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                resolve_under(Path(temp), r"C:relative\secret.txt")

    def test_nul_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                resolve_under(Path(temp), "safe\x00outside")

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            link = root / "escape"
            try:
                link.symlink_to(Path(outside), target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")
            with self.assertRaises(ValueError):
                resolve_under(root, "escape/secret.txt")

    def test_filename_is_reduced_to_one_safe_component(self):
        self.assertEqual("passwd", safe_filename("../../etc/passwd"))
        self.assertEqual("evil.txt", safe_filename(r"..\..\evil.txt"))
        self.assertNotIn("/", safe_filename("customer/report 2026.pdf"))
        self.assertNotIn("\\", safe_filename("customer/report 2026.pdf"))


if __name__ == "__main__":
    unittest.main()
