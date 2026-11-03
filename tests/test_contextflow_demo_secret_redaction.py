"""Regression: demo banner and failures must never render secrets.

Covers CodeQL "clear-text logging of sensitive information" for
``tools/contextflow_demo.py``: the fixed demo password and any provided
``MODEL_API_KEY`` must not appear on stdout, stderr, or in ``SystemExit``
messages, while useful non-sensitive diagnostics remain.
"""

from __future__ import annotations

import io
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
        with self.assertRaises(SystemExit) as invalid_provider:
            vision_config_from_env(
                {
                    "CONTEXTFLOW_VISION_PROVIDER": "bogus-provider",
                    "MODEL_API_KEY": SENTINEL_API_KEY,
                }
            )
        self.assertNotIn(SENTINEL_API_KEY, str(invalid_provider.exception))
        self.assertNotIn(DEMO_PASSWORD, str(invalid_provider.exception))
        self.assertIn("must be local, meta or mock", str(invalid_provider.exception))

        with self.assertRaises(SystemExit) as missing_key:
            vision_config_from_env({"CONTEXTFLOW_VISION_PROVIDER": "meta"})
        self.assertNotIn(DEMO_PASSWORD, str(missing_key.exception))
        self.assertIn("MODEL_API_KEY is missing", str(missing_key.exception))


if __name__ == "__main__":
    unittest.main()
