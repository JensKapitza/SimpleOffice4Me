import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.release_updater import _extract, apply_archive


def _archive(path: Path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, content in files.items():
            package.writestr(f"SimpleOffice4Me-test/{name}", content)


class ReleaseUpdaterTest(unittest.TestCase):
    def test_update_replaces_program_files_and_preserves_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "install"
            root.mkdir()
            (root / "app").mkdir()
            (root / "app" / "old.py").write_text("old", encoding="utf-8")
            (root / "instance").mkdir()
            (root / "instance" / "simpleoffice.json").write_text("private", encoding="utf-8")
            (root / ".venv").mkdir()
            (root / ".venv" / "marker").write_text("keep", encoding="utf-8")
            archive = Path(temp) / "update.zip"
            _archive(
                archive,
                {
                    "pyproject.toml": '[project]\nname="simpleoffice4me"\nversion="2.0.0"\n',
                    "app/new.py": "new",
                    "update.sh": "#!/bin/sh\n",
                },
            )

            release = apply_archive(root, archive, "a" * 40, "main", 1234)

            self.assertFalse((root / "app" / "old.py").exists())
            self.assertEqual("new", (root / "app" / "new.py").read_text(encoding="utf-8"))
            self.assertEqual("private", (root / "instance" / "simpleoffice.json").read_text(encoding="utf-8"))
            self.assertEqual("keep", (root / ".venv" / "marker").read_text(encoding="utf-8"))
            self.assertEqual("2.0.0", release["version"])
            saved = json.loads((root / ".simpleoffice-release.json").read_text(encoding="utf-8"))
            self.assertEqual("https-source-archive", saved["update_mode"])
            self.assertEqual("a" * 40, saved["revision"])

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / "bad.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("SimpleOffice4Me-test/pyproject.toml", "[project]\nversion='1.0.0'\n")
                archive.writestr("SimpleOffice4Me-test/app/x.py", "x")
                archive.writestr("SimpleOffice4Me-test/../escape.txt", "bad")
            destination = Path(temp) / "out"
            with self.assertRaisesRegex(ValueError, "unsicheren Pfad"):
                _extract(archive_path, destination)
            self.assertFalse((Path(temp) / "escape.txt").exists())

    def test_copy_failure_rolls_back_existing_program_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "install"
            root.mkdir()
            (root / "app").mkdir()
            (root / "app" / "stable.py").write_text("stable", encoding="utf-8")
            (root / "pyproject.toml").write_text('[project]\nversion="1.0.0"\n', encoding="utf-8")
            archive = Path(temp) / "update.zip"
            _archive(
                archive,
                {
                    "pyproject.toml": '[project]\nversion="2.0.0"\n',
                    "app/new.py": "new",
                },
            )
            with patch("tools.release_updater.shutil.copytree", side_effect=OSError("copy failed")):
                with self.assertRaises(OSError):
                    apply_archive(root, archive, "b" * 40, "main", 1234)
            self.assertEqual("stable", (root / "app" / "stable.py").read_text(encoding="utf-8"))
            self.assertFalse((root / "app" / "new.py").exists())


if __name__ == "__main__":
    unittest.main()
