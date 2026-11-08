"""Regression tests for the macOS/Windows virtualenv launcher (stdlib only)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "cross_platform_start.py"
spec = importlib.util.spec_from_file_location("cross_platform_start", SCRIPT)
assert spec and spec.loader
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class CrossPlatformStartTests(unittest.TestCase):
    def test_missing_environment_never_counts_as_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(launcher.is_ready(Path(temp) / "does-not-exist"))

    def test_venv_layout_unix(self):
        with patch.object(launcher.os, "name", "posix"):
            self.assertEqual(
                launcher.venv_python(Path("/tmp/project/.venv")),
                Path("/tmp/project/.venv/bin/python"),
            )

    def test_venv_layout_windows(self):
        # On POSIX, Path semantics still permit checking the relative Windows layout.
        with patch.object(launcher.os, "name", "nt"):
            self.assertEqual(
                str(launcher.venv_python(Path("venv"))).replace("\\", "/"),
                "venv/Scripts/python.exe",
            )

    def test_choose_bootstrap_prefers_supported_candidate(self):
        class Result:
            def __init__(self, returncode):
                self.returncode = returncode

        def fake_run(command, **kwargs):
            return Result(0 if command[0] == "python3.14" else 1)

        with patch.dict(launcher.os.environ, {}, clear=True):
            with patch.object(launcher, "probe_candidates", return_value=[["python3.15"], ["python3.14"]]):
                with patch.object(launcher.subprocess, "run", side_effect=fake_run):
                    self.assertEqual(launcher.choose_bootstrap(), ["python3.14"])

    def test_rejects_unsupported_existing_interpreter(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "existing"
            python = launcher.venv_python(target)
            python.parent.mkdir(parents=True)
            python.touch()
            with patch.object(launcher, "supported_runtime", return_value=False):
                self.assertFalse(launcher.is_ready(python.parent.parent))


if __name__ == "__main__":
    unittest.main()
