"""Free model IDs and real HTTP compatible transport, with synthetic test data."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from app.contextflow.openai_provider import OpenAIVisionProvider, check_openai_model
from app.contextflow.provider_transport import request_json, validate_endpoint
from app.contextflow.vision import VisionError, _localhost_only, select_provider


class CompatibleProviderTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.response_status = 200
        self.response = {"choices": [{"message": {"content": '{"answer":"synthetic"}'}}]}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                self.do_POST()

            def do_POST(self):
                size = int(self.headers.get("Content-Length", "0"))
                payload = self.rfile.read(size)
                owner.requests.append((self.path, self.headers.get("Authorization"), json.loads(payload) if payload else None))
                self.send_response(owner.response_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Location", "http://example.invalid/must-not-follow")
                self.end_headers()
                self.wfile.write(json.dumps(owner.response).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.settings = {"provider": "compatible", "openai_endpoint": self.endpoint,
                         "openai_model": "Vendor/MiMo-v2.6", "openai_api_key": ""}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_local_model_is_used_verbatim_in_real_http_post_without_cloud_key(self):
        provider = select_provider(self.settings, False, None)
        self.assertEqual({"answer": "synthetic"}, provider.complete_text("synthetic prompt"))
        path, key, body = self.requests[0]
        self.assertEqual("/v1/chat/completions", path)
        self.assertIsNone(key)
        self.assertEqual("Vendor/MiMo-v2.6", body["model"])
        self.assertNotIn("response_format", body)

    def test_local_compatible_image_payload_reaches_selected_adapter(self):
        provider = select_provider(self.settings, False, None)
        self.assertEqual({"answer": "synthetic"}, provider.suggest(b"synthetic", "image/png", {}))
        image = self.requests[0][2]["messages"][0]["content"][1]
        self.assertEqual("image_url", image["type"])
        self.assertTrue(image["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_generic_text_helper_dispatches_to_compatible_provider(self):
        from app.contextflow.doc_tags import complete_text
        self.assertEqual({"answer": "synthetic"}, complete_text(select_provider(self.settings, False, None), "synthetic"))

    def test_exact_model_is_checked_not_any_vendor_model(self):
        self.response = {"data": [{"id": "different"}]}
        self.assertFalse(check_openai_model("", endpoint=self.endpoint, model="Vendor/MiMo-v2.6")[0])
        self.response = {"data": [{"id": "Vendor/MiMo-v2.6"}]}
        self.assertTrue(check_openai_model("", endpoint=self.endpoint, model="Vendor/MiMo-v2.6")[0])

    def test_status_probes_local_compatible_model_without_cloud_consent_or_key(self):
        from app.contextflow.vision import ml_status
        self.response = {"data": [{"id": "Vendor/MiMo-v2.6"}]}
        status = ml_status(self.settings)
        self.assertTrue(status["openai"]["local"])
        self.assertFalse(status["openai"]["key_required"])
        self.assertTrue(status["openai"]["model_listed"])
        self.assertEqual("/v1/models", self.requests[0][0])
        self.assertIsNone(self.requests[0][1])

    def test_redirects_and_auth_rate_errors_are_terminal(self):
        for status in [302, 401, 403, 429, 500]:
            self.response_status = status
            before = len(self.requests)
            with self.assertRaisesRegex(VisionError, str(status)):
                request_json(self.endpoint, "synthetic-test-key", "/chat/completions", {"model": "mimo-v2.6"})
            self.assertEqual(before + 1, len(self.requests))

    def test_large_response_is_bounded(self):
        self.response = {"text": "x" * (256 * 1024)}
        with self.assertRaisesRegex(VisionError, "groß"):
            request_json(self.endpoint, "", "/models", None)

    def test_external_requires_server_and_request_consent_then_key(self):
        settings = {**self.settings, "openai_endpoint": "https://example.com/api/v1", "openai_api_key": "synthetic-test-key"}
        for consent, flag in [(False, True), (True, False)]:
            with self.assertRaises(PermissionError):
                select_provider({**settings, "openai_consent": flag}, consent, "/ordinary-data-root")
        provider = select_provider({**settings, "openai_consent": True}, True, "/ordinary-data-root")
        self.assertIsInstance(provider, OpenAIVisionProvider)
        with self.assertRaises(PermissionError):
            select_provider({**settings, "openai_consent": True, "openai_api_key": ""}, True, "/ordinary-data-root")
        self.assertEqual([], self.requests)

    def test_endpoint_validation_blocks_userinfo_query_and_nonlocal_http(self):
        for url in ["http://example.com/v1", "https://key@example.com/v1", "https://example.com/v1?key=x", "https://example.com/#x", "https://example.com/../private", "https://example.com/%2e%2e/private"]:
            with self.subTest(url=url), self.assertRaises(VisionError):
                validate_endpoint(url)
        for url in ["http://key@127.0.0.1:11434", "http://127.0.0.1:11434/?key=x", "http://127.0.0.1:11434/#x"]:
            with self.assertRaises(VisionError):
                _localhost_only(url)

    def test_status_redacts_credentials_in_invalid_local_override(self):
        from app.contextflow.vision import ml_status
        with mock.patch("app.contextflow.vision.local_urlopen") as transport:
            status = ml_status({"provider": "local", "ollama_url": "http://user:synthetic-secret@127.0.0.1:11434",
                                "ollama_model": "mimo-v2.6"})
        transport.assert_not_called()
        self.assertNotIn("synthetic-secret", json.dumps(status))
        self.assertFalse(status["ollama"]["reachable"])

    def test_external_dns_failure_is_redacted(self):
        with mock.patch("app.contextflow.provider_transport.addresses", side_effect=OSError("synthetic-secret")):
            with self.assertRaises(VisionError) as raised:
                request_json("https://example.com/v1", "synthetic-secret", "/models", None)
        self.assertNotIn("synthetic-secret", str(raised.exception))
