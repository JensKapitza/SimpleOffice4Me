"""ContextFlow ML status badge: honest backend state, no secrets."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from app import app
from app.contextflow.demo import DEMO_MARKER, DEMO_TOOL_TAG
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class FakeTagsResponse:
    """Minimal context manager mimicking the /api/tags answer."""

    def __init__(self, payload: dict) -> None:
        self._stream = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class MlStatusTests(unittest.TestCase):
    password = "contextflow-ml-status-password"
    keys = (
        "DATABASE",
        "DOCUMENT_ROOT",
        "TESTING",
        "TEST_CSRF_PROTECTION",
        "CONTEXTFLOW_ENABLED",
        "CONTEXTFLOW_VISION_PROVIDER",
        "CONTEXTFLOW_OLLAMA_URL",
        "CONTEXTFLOW_OLLAMA_MODEL",
        "CONTEXTFLOW_META_CONSENT",
    )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in self.keys}
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"),
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            CONTEXTFLOW_ENABLED=True,
            CONTEXTFLOW_VISION_PROVIDER="local",
            CONTEXTFLOW_OLLAMA_URL="http://127.0.0.1:11434",
            CONTEXTFLOW_OLLAMA_MODEL="llama3.2-vision:11b",
            CONTEXTFLOW_META_CONSENT=False,
        )
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,"
                "is_disabled,auth_version,created_at,updated_at)"
                " VALUES (?,?,?, ?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("admin", hash_password(self.password), "Admin", 1),
            )
            db.commit()
        self.client = self.login("admin")

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, username: str):
        import re

        client = app.test_client()
        page = client.get("/auth/login")
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
        self.assertIsNotNone(match)
        assert match is not None
        client.post(
            "/auth/login",
            data={
                "username": username,
                "password": self.password,
                "_csrf_token": match.group(1).decode(),
            },
        )
        return client

    def test_local_reachable_reports_model_seen(self) -> None:
        tags = {"models": [{"name": "llama3.2-vision:11b"}]}
        with mock.patch(
            "app.contextflow.vision.local_urlopen",
            return_value=FakeTagsResponse(tags),
        ):
            response = self.client.get("/contextflow/ml-status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["provider"], "local")
        self.assertTrue(payload["ollama"]["reachable"])
        self.assertTrue(payload["ollama"]["model_seen"])
        self.assertTrue(payload["ollama"]["model_allowed"])
        self.assertEqual(payload["ollama"]["model"], "llama3.2-vision:11b")
        self.assertIn("rapidocr", payload["ocr"])
        self.assertIn("tesseract", payload["ocr"])

    def test_local_unreachable_reports_plain_error(self) -> None:
        with mock.patch(
            "app.contextflow.vision.local_urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            response = self.client.get("/contextflow/ml-status")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["ollama"]["reachable"])
        self.assertFalse(payload["ollama"]["model_seen"])
        self.assertTrue(payload["ollama"]["model_allowed"])
        self.assertIn("nicht erreichbar", payload["ollama"]["error"])
        self.assertNotIn("refused", response.get_data(as_text=True))

    def test_unapproved_model_is_never_probed(self) -> None:
        for bad_model in ("qwen3-vl:2b", "my-llama-alias"):
            with self.subTest(model=bad_model):
                app.config.update(CONTEXTFLOW_OLLAMA_MODEL=bad_model)
                with mock.patch(
                    "app.contextflow.vision.local_urlopen"
                ) as urlopen:
                    response = self.client.get("/contextflow/ml-status")
                urlopen.assert_not_called()
                payload = response.get_json()
                self.assertEqual(response.status_code, 200)
                self.assertFalse(payload["ollama"]["model_allowed"])
                self.assertFalse(payload["ollama"]["reachable"])
                self.assertFalse(payload["ollama"]["model_seen"])
                self.assertEqual(bad_model, payload["ollama"]["model"])
                self.assertIn("nicht freigegeben", payload["ollama"]["error"])

    def test_non_loopback_url_is_never_probed(self) -> None:
        app.config.update(CONTEXTFLOW_OLLAMA_URL="http://ollama.lan:11434")
        with mock.patch(
            "app.contextflow.vision.local_urlopen"
        ) as urlopen:
            response = self.client.get("/contextflow/ml-status")
        urlopen.assert_not_called()
        payload = response.get_json()
        self.assertFalse(payload["ollama"]["reachable"])
        self.assertIn("Loopback", payload["ollama"]["error"])

    def test_missing_model_skips_probe(self) -> None:
        app.config.update(CONTEXTFLOW_OLLAMA_MODEL="")
        with mock.patch(
            "app.contextflow.vision.local_urlopen"
        ) as urlopen:
            response = self.client.get("/contextflow/ml-status")
        urlopen.assert_not_called()
        self.assertIn(
            "kein Modell", response.get_json()["ollama"]["error"]
        )
        self.assertFalse(response.get_json()["ollama"]["model_allowed"])

    def test_mock_provider_reports_placeholder(self) -> None:
        app.config.update(CONTEXTFLOW_VISION_PROVIDER="mock")
        with mock.patch(
            "app.contextflow.vision.local_urlopen"
        ) as urlopen:
            response = self.client.get("/contextflow/ml-status")
        urlopen.assert_not_called()
        self.assertEqual(response.get_json()["provider"], "mock")

    def test_meta_key_presence_never_leaks_value(self) -> None:
        secret = "test-only-dummy-meta-key-never-real-02"
        with mock.patch.dict("os.environ", {"MODEL_API_KEY": secret}):
            response = self.client.get("/contextflow/ml-status")
        payload = response.get_json()
        self.assertTrue(payload["meta"]["key_present"])
        self.assertEqual(payload["meta"]["model"], "muse-spark-1.3-contributor")
        self.assertNotIn(secret, response.get_data(as_text=True))
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("MODEL_API_KEY", None)
            response = self.client.get("/contextflow/ml-status")
        self.assertFalse(response.get_json()["meta"]["key_present"])

    def test_meta_probe_needs_server_consent(self) -> None:
        app.config.update(CONTEXTFLOW_VISION_PROVIDER="meta")
        with (
            mock.patch.dict("os.environ", {"MODEL_API_KEY": "k"}),
            mock.patch(
                "app.contextflow.vision.check_meta_model"
            ) as check,
        ):
            response = self.client.get("/contextflow/ml-status")
        check.assert_not_called()
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["meta"]["key_present"])
        self.assertIsNone(payload["meta"]["model_listed"])
        self.assertIn("nicht freigegeben", payload["meta"]["models_error"])

    def test_meta_probe_needs_demo_root(self) -> None:
        app.config.update(
            CONTEXTFLOW_VISION_PROVIDER="meta",
            CONTEXTFLOW_META_CONSENT=True,
        )
        with (
            mock.patch.dict("os.environ", {"MODEL_API_KEY": "k"}),
            mock.patch(
                "app.contextflow.vision.check_meta_model"
            ) as check,
        ):
            response = self.client.get("/contextflow/ml-status")
        check.assert_not_called()
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(payload["meta"]["model_listed"])
        self.assertIn("nicht freigegeben", payload["meta"]["models_error"])

    def test_meta_probe_runs_on_consented_demo_root(self) -> None:
        root = Path(app.config["DOCUMENT_ROOT"])
        root.mkdir(parents=True, exist_ok=True)
        (root / DEMO_MARKER).write_text(
            json.dumps({"synthetic": True, "tool": DEMO_TOOL_TAG}) + "\n",
            encoding="utf-8",
        )
        app.config.update(
            CONTEXTFLOW_VISION_PROVIDER="meta",
            CONTEXTFLOW_META_CONSENT=True,
        )
        with (
            mock.patch.dict("os.environ", {"MODEL_API_KEY": "k"}),
            mock.patch(
                "app.contextflow.vision.check_meta_model",
                return_value=(True, ""),
            ) as check,
        ):
            response = self.client.get("/contextflow/ml-status")
        check.assert_called_once_with("k")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["meta"]["model_listed"])
        self.assertEqual(payload["meta"]["models_error"], "")

    def test_flag_off_404_and_login_required(self) -> None:
        app.config.update(CONTEXTFLOW_ENABLED=False)
        try:
            self.assertEqual(
                self.client.get("/contextflow/ml-status").status_code, 404
            )
        finally:
            app.config.update(CONTEXTFLOW_ENABLED=True)
        response = app.test_client().get("/contextflow/ml-status")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers.get("Location", ""))

    def test_contacts_page_carries_status_badge(self) -> None:
        response = self.client.get("/documents/contacts")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('id="ml-status-badge"', body)
        self.assertIn("/contextflow/ml-status", body)


class MlBadgeRenderingTests(unittest.TestCase):
    """Header badge honors model_allowed fail-closed (static wiring)."""

    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.script = (root / "static/js/contact-import.js").read_text(encoding="utf-8")

    def test_unapproved_guard_precedes_active_label(self) -> None:
        guard = "ollama.model_allowed !== true"
        self.assertIn(guard, self.script)
        self.assertLess(
            self.script.index(guard), self.script.index("ML aktiv")
        )
        self.assertIn("ML: Modell nicht freigegeben · ${ollama.model}", self.script)

    def test_allowed_local_model_shows_provider_and_id(self) -> None:
        self.assertIn(
            "ML aktiv · Meta-Modell lokal via Ollama · ${ollama.model}",
            self.script,
        )

    def test_meta_branch_shows_honest_key_listing_and_exact_id(self) -> None:
        self.assertIn("kein API-Schlüssel", self.script)
        self.assertIn("Modell nicht bestätigt", self.script)
        self.assertIn("Modell nicht geprüft", self.script)
        self.assertIn("mlBadge.title = `Modell: ${meta.model", self.script)


if __name__ == "__main__":
    unittest.main()
