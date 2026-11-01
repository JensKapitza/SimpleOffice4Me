"""Parent .env loader: allowlisted keys, never override, never print."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.local_env import load_parent_env_file


class LocalEnvTests(unittest.TestCase):
    def write_env(self, directory: Path, text: str) -> Path:
        path = directory / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_is_silent_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual((), load_parent_env_file(Path(temp) / ".env"))

    def test_loads_allowlisted_key_and_strips_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_env(
                Path(temp),
                '# synthetic fixture\nMODEL_API_KEY="synthetic-key-1"\n',
            )
            with mock.patch.dict("os.environ", {}, clear=False):
                os.environ.pop("MODEL_API_KEY", None)
                loaded = load_parent_env_file(path)
                self.assertEqual(("MODEL_API_KEY",), loaded)
                self.assertEqual("synthetic-key-1", os.environ["MODEL_API_KEY"])
                os.environ.pop("MODEL_API_KEY", None)

    def test_existing_environment_always_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_env(Path(temp), "MODEL_API_KEY=file-value\n")
            with mock.patch.dict("os.environ", {"MODEL_API_KEY": "env-value"}):
                self.assertEqual((), load_parent_env_file(path))
                self.assertEqual("env-value", os.environ["MODEL_API_KEY"])

    def test_ignores_noise_empty_and_foreign_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_env(
                Path(temp),
                "\n# comment\nlowercase=x\nNOEQUALS\n"
                "MODEL_API_KEY=\nPYTHONPATH=/tmp/evil\n"
                "MODEL_API_KEY = ' spaced-value '\n",
            )
            with mock.patch.dict("os.environ", {}, clear=False):
                os.environ.pop("MODEL_API_KEY", None)
                os.environ.pop("PYTHONPATH", None)
                loaded = load_parent_env_file(path)
                self.assertEqual(("MODEL_API_KEY",), loaded)
                self.assertEqual(" spaced-value ", os.environ["MODEL_API_KEY"])
                self.assertNotIn("PYTHONPATH", os.environ)
                self.assertNotIn("lowercase", os.environ)
                os.environ.pop("MODEL_API_KEY", None)

    def test_emits_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_env(Path(temp), "MODEL_API_KEY=s3cr3t\n")
            buffer = io.StringIO()
            with mock.patch.dict("os.environ", {}, clear=False):
                os.environ.pop("MODEL_API_KEY", None)
                with contextlib.redirect_stdout(buffer):
                    load_parent_env_file(path)
                os.environ.pop("MODEL_API_KEY", None)
            self.assertEqual("", buffer.getvalue())

    def test_oversize_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_env(Path(temp), "MODEL_API_KEY=x\n" + "A" * 70_000)
            with mock.patch.dict("os.environ", {}, clear=False):
                os.environ.pop("MODEL_API_KEY", None)
                self.assertEqual((), load_parent_env_file(path))
                self.assertNotIn("MODEL_API_KEY", os.environ)


if __name__ == "__main__":
    unittest.main()
