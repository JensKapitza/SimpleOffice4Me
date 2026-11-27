"""Component executables prefer the host and reject unsafe managed paths."""
from __future__ import annotations

import os
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.component_runtime import resolve_command
from app.document_store_core import CONTROL_DIR


class ComponentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "documents"
        self.bin_dir = self.root / CONTROL_DIR / "components" / "bin"
        self.bin_dir.mkdir(parents=True)

    def managed_ffmpeg(self) -> Path:
        executable = self.bin_dir / "ffmpeg"
        contents = b"#!/bin/sh\nprintf 'managed fixture\\n'\n"
        executable.write_bytes(contents)
        executable.chmod(0o700)
        (self.bin_dir.parent / "installed.json").write_text(json.dumps({
            "schema": 1,
            "components": {"ffmpeg": {
                "filename": executable.name,
                "sha256": hashlib.sha256(contents).hexdigest(),
                "version": "fixture-1",
            }},
        }), encoding="utf-8")
        return executable

    def test_system_installation_wins_without_touching_managed_install(self):
        managed = self.managed_ffmpeg()
        with patch("app.component_runtime.shutil.which", return_value="/usr/bin/ffmpeg"):
            self.assertEqual("/usr/bin/ffmpeg", resolve_command("ffmpeg", self.root))
        self.assertTrue(managed.is_file())

    def test_private_managed_install_is_resolved_when_host_tool_is_missing(self):
        managed = self.managed_ffmpeg()
        with patch("app.component_runtime.shutil.which", return_value=None):
            self.assertEqual(str(managed), resolve_command("ffmpeg", self.root))

    def test_symlink_and_path_escape_are_never_used_as_managed_executables(self):
        outside = Path(self.temporary.name) / "outside"
        outside.write_text("not a trusted executable", encoding="utf-8")
        outside.chmod(0o700)
        (self.bin_dir / "ffmpeg").symlink_to(outside)
        with patch("app.component_runtime.shutil.which", return_value=None):
            self.assertIsNone(resolve_command("ffmpeg", self.root))

    def test_modified_managed_executable_fails_its_install_manifest(self):
        managed = self.managed_ffmpeg()
        managed.write_text("#!/bin/sh\nprintf 'changed after verification'\n", encoding="utf-8")
        managed.chmod(0o700)
        with patch("app.component_runtime.shutil.which", return_value=None):
            self.assertIsNone(resolve_command("ffmpeg", self.root))

    def test_symlinked_component_directory_is_rejected(self):
        external_bin = Path(self.temporary.name) / "external-bin"
        external_bin.mkdir()
        executable = external_bin / "ffmpeg"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
        self.bin_dir.rmdir()
        self.bin_dir.symlink_to(external_bin, target_is_directory=True)
        with patch("app.component_runtime.shutil.which", return_value=None):
            self.assertIsNone(resolve_command("ffmpeg", self.root))

    def test_unknown_component_names_are_not_accepted(self):
        with self.assertRaises(ValueError):
            resolve_command("arbitrary-command", self.root)

    @unittest.skipIf(os.name == "nt", "POSIX executable permission is not used on Windows")
    def test_non_executable_regular_file_is_not_used(self):
        executable = self.bin_dir / "ffmpeg"
        executable.write_text("not executable", encoding="utf-8")
        executable.chmod(0o600)
        with patch("app.component_runtime.shutil.which", return_value=None):
            self.assertIsNone(resolve_command("ffmpeg", self.root))


if __name__ == "__main__":
    unittest.main()
