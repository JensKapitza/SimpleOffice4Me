import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_audio_http import bp as audio_bp
from app.federation_blocks_http import bp as blocks_bp
from app.federation_peer_auth import headers as peer_headers
from app.federation_store import FederationStore


class FederationPairInviteAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "receiver"
        app = Flask(__name__)
        app.config.update(TESTING=False, DOCUMENT_ROOT=str(self.root), SECRET_KEY="peer-test")
        app.register_blueprint(blocks_bp)
        app.register_blueprint(audio_bp)
        self.client = app.test_client()
        self.context = app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        self.token = "p" * 43
        FederationStore(self.root).save_peer(
            "peer-b", "Peer B", "https://peer-b.example.test", self.token,
            policy={}, enabled=True,
        )
        self.shared_patch = patch("app.federation_blocks_http._identity.shared_token", return_value="")
        self.shared_patch.start()
        self.addCleanup(self.shared_patch.stop)
        self.audio_shared_patch = patch("app.federation_audio_http._identity.shared_token", return_value="")
        self.audio_shared_patch.start()
        self.addCleanup(self.audio_shared_patch.stop)

    def _headers(self, path, body):
        return {
            "Authorization": f"Bearer {self.token}",
            **peer_headers("peer-b", self.token, "POST", path, body),
        }

    def test_pair_token_authenticates_only_the_small_block_echo(self):
        body = b"one-scan-pair-check"
        response = self.client.post(
            "/federation/v1/blocks/test", data=body,
            headers=self._headers("/federation/v1/blocks/test", body),
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["ok"])

        body = b"{}"
        denied = self.client.post(
            "/federation/v1/blocks/availability", data=body,
            content_type="application/json",
            headers=self._headers("/federation/v1/blocks/availability", body),
        )
        self.assertEqual(401, denied.status_code)

    def test_pair_token_authenticates_audio_diagnostic(self):
        body = b"{}"
        with patch("app.federation_audio_http.render_preset", return_value=Path(self.temp.name) / "tone.wav"), \
             patch("app.federation_audio_http.play_file"):
            response = self.client.post(
                "/federation/v1/audio/test-tone", data=body,
                content_type="application/json",
                headers=self._headers("/federation/v1/audio/test-tone", body),
            )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["ok"])

    def test_missing_peer_proof_is_rejected_without_global_token(self):
        response = self.client.post("/federation/v1/blocks/test", data=b"data")
        self.assertEqual(401, response.status_code)


if __name__ == "__main__":
    unittest.main()
