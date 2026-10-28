"""Focused tests for the synthetic demo-root reset (launch-path follow-up).

Only temp dirs under the OS temp area are touched. Protected roots are
asserted refused without planting markers anywhere real. Run with the
project venv (``.venv``) and TEMP/TMP pointing to a short local path.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from app.contextflow.demo import (
    DEMO_MARKER,
    DemoRootError,
    is_verified_demo_root,
    reset_demo_root,
)

MARKER_PAYLOAD = {"synthetic": True, "tool": "contextflow_demo"}

try:
    with tempfile.TemporaryDirectory() as _probe:
        os.symlink("x", os.path.join(_probe, "y"))
    CAN_SYMLINK = True
except (OSError, NotImplementedError):
    CAN_SYMLINK = False


def _mark(root: Path) -> None:
    (root / DEMO_MARKER).write_text(
        json.dumps(MARKER_PAYLOAD) + "\n", encoding="utf-8"
    )


def _readonly(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


class DemoResetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "demo"
        self.root.mkdir()

    def tearDown(self) -> None:
        # Make our own leftovers deletable again (read-only flags, links).
        for dirpath, dirnames, filenames in os.walk(
            self.temp.name, topdown=False, followlinks=False
        ):
            for name in dirnames + filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), 0o700)
                except OSError:
                    pass
        self.temp.cleanup()

    def test_unmarked_nonempty_refused_and_untouched(self) -> None:
        victim = self.root / "keep.txt"
        victim.write_text("echt", encoding="utf-8")
        self.assertFalse(is_verified_demo_root(self.root))
        with self.assertRaises(DemoRootError):
            reset_demo_root(self.root)
        self.assertEqual(victim.read_text(encoding="utf-8"), "echt")

    def test_wrong_marker_content_refused(self) -> None:
        (self.root / DEMO_MARKER).write_text("{kein json", encoding="utf-8")
        (self.root / "data.txt").write_text("x", encoding="utf-8")
        self.assertFalse(is_verified_demo_root(self.root))
        with self.assertRaises(DemoRootError):
            reset_demo_root(self.root)
        (self.root / DEMO_MARKER).write_text(
            json.dumps({"synthetic": False, "tool": "contextflow_demo"}),
            encoding="utf-8",
        )
        self.assertFalse(is_verified_demo_root(self.root))
        with self.assertRaises(DemoRootError):
            reset_demo_root(self.root)
        self.assertTrue((self.root / "data.txt").exists())

    def test_verified_roundtrip(self) -> None:
        _mark(self.root)
        self.assertTrue(is_verified_demo_root(self.root))
        result = reset_demo_root(self.root)
        self.assertEqual(result, self.root)
        self.assertTrue(is_verified_demo_root(self.root))
        self.assertEqual(sorted(item.name for item in self.root.iterdir()), [DEMO_MARKER])

    def test_fresh_reset_clears_nested_readonly(self) -> None:
        _mark(self.root)
        # Mirrors the reported failure: read-only Git objects below a
        # history directory, plus a read-only top-level file.
        objects = self.root / ".simpleoffice-history" / ".git" / "objects" / "ab"
        objects.mkdir(parents=True)
        blob = objects / "cdef1234"
        blob.write_text("git-object", encoding="utf-8")
        _readonly(blob)
        top = self.root / "demo-users.sqlite"
        top.write_text("daten", encoding="utf-8")
        _readonly(top)
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "inner.txt").write_text("tief", encoding="utf-8")
        reset_demo_root(self.root)
        self.assertTrue(is_verified_demo_root(self.root))
        self.assertEqual(sorted(item.name for item in self.root.iterdir()), [DEMO_MARKER])

    @unittest.skipUnless(CAN_SYMLINK, "OS/user cannot create symlinks")
    def test_symlinks_removed_never_followed(self) -> None:
        _mark(self.root)
        with tempfile.TemporaryDirectory() as outside:
            target_file = Path(outside) / "echt.txt"
            target_file.write_text("nicht loeschen", encoding="utf-8")
            target_dir = Path(outside) / "echt-dir"
            target_dir.mkdir()
            (target_dir / "innen.txt").write_text("innen", encoding="utf-8")
            os.symlink(str(target_file), self.root / "link-datei")
            os.symlink(str(target_dir), self.root / "link-verzeichnis",
                       target_is_directory=True)
            reset_demo_root(self.root)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "nicht loeschen")
            self.assertTrue((target_dir / "innen.txt").exists())
        self.assertTrue(is_verified_demo_root(self.root))
        self.assertEqual(sorted(item.name for item in self.root.iterdir()), [DEMO_MARKER])

    def test_protected_roots_refused(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        with self.assertRaises(DemoRootError):
            reset_demo_root(repo_root)
        with self.assertRaises(DemoRootError):
            reset_demo_root(Path(os.path.abspath(os.sep)))
        self.assertTrue((repo_root / "pyproject.toml").exists())


if __name__ == "__main__":
    unittest.main()
