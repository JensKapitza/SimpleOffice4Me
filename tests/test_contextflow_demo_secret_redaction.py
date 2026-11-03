"""Regression: demo banner and failures must never render secrets.

Covers CodeQL "clear-text logging of sensitive information" for
``tools/contextflow_demo.py``: the fixed demo password and any provided
``MODEL_API_KEY`` must not appear on stdout, stderr, or in ``SystemExit``
messages, while useful non-sensitive diagnostics remain.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tools.contextflow_demo import (
    DEMO_PASSWORD,
    DEMO_USERNAME,
    print_demo_banner,
    vision_config_from_env,
)

SENTINEL_API_KEY = "sentinel-meta-key-9f8e7d6c5b4a-do-not-log"
SENTINEL_ROOT = Path("/tmp/sentinel-demo-root")


def _summary() -> dict:
    return {
        "link_id": "L-123",
        "completed_on": "2026-11-03",
        "demo_documents": {
            "documents": [
                {
                    "name": "befund.pdf",
                    "document_id": "doc-1",
                    "preview": "pdf",
                    "converter": "local",
                }
            ]
        },
    }


def _captured_banner(vision: dict) -> tuple[str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        print_demo_banner(
            port=5088, root=SENTINEL_ROOT, summary=_summary(), vision=vision
        )
    return out.getvalue(), err.getvalue()


def _run_demo_cli(provider: str, api_key: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="contextflow-secret-output-") as root:
        env = os.environ.copy()
        env["CONTEXTFLOW_VISION_PROVIDER"] = provider
        # Set even an empty value so local .env loading cannot change the
        # missing-key path during this subprocess test.
        env["MODEL_API_KEY"] = api_key
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.contextflow_demo",
                "--port",
                "0",
                "--root",
                root,
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )


class DemoSecretRedactionTest(unittest.TestCase):
    def test_success_banner_hides_password_and_api_key(self) -> None:
        vision = vision_config_from_env(
            {
                "CONTEXTFLOW_VISION_PROVIDER": "meta",
                "MODEL_API_KEY": SENTINEL_API_KEY,
            }
        )
        stdout, stderr = _captured_banner(vision)

        self.assertNotIn(DEMO_PASSWORD, stdout)
        self.assertNotIn(SENTINEL_API_KEY, stdout)
        self.assertNotIn(DEMO_PASSWORD, stderr)
        self.assertNotIn(SENTINEL_API_KEY, stderr)

        # Useful diagnostics remain.
        self.assertIn(DEMO_USERNAME, stdout)
        self.assertIn("http://127.0.0.1:5088/contextflow/", stdout)
        self.assertIn(str(SENTINEL_ROOT), stdout)
        self.assertIn("L-123", stdout)
        self.assertIn("Meta Muse Spark", stdout)

    def test_mock_banner_hides_password(self) -> None:
        vision = vision_config_from_env({})
        stdout, stderr = _captured_banner(vision)

        self.assertNotIn(DEMO_PASSWORD, stdout)
        self.assertNotIn(DEMO_PASSWORD, stderr)
        self.assertIn(DEMO_USERNAME, stdout)
        self.assertIn("Mock", stdout)

    def test_failure_paths_do_not_render_provided_secret(self) -> None:
        cases = (
            ("bogus-provider", SENTINEL_API_KEY, "must be local, meta or mock"),
            ("meta", "", "MODEL_API_KEY is missing"),
        )
        for provider, key, expected_diagnostic in cases:
            with self.subTest(provider=provider):
                result = _run_demo_cli(provider, key)
                combined = result.stdout + result.stderr
                self.assertNotEqual(0, result.returncode)
                self.assertNotIn(SENTINEL_API_KEY, combined)
                self.assertNotIn(DEMO_PASSWORD, combined)
                self.assertIn(expected_diagnostic, combined)


if __name__ == "__main__":
    unittest.main()
