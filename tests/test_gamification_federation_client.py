import io
import unittest
from unittest.mock import patch

from app.gamification_federation_client import fetch_next, submit_answer


class FakeResponse:
    def __init__(self, body=b"{}"):
        self.body = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body.read(size)


class GamificationFederationClientTests(unittest.TestCase):
    def test_fetch_next_ignores_peer_supplied_preview_url_and_uses_local_identity(self):
        peer = {"enabled": True, "policy": {"gamification": {"receive_challenges": True, "providers": ["images"]}}}
        body = b'{"challenge_id":"c1","provider":"images","kind":"tags","answer_type":"tags","prompt":"Bild?","payload":{"preview":true},"preview_endpoint":"https://evil.invalid/steal"}'
        with patch("app.gamification_federation_client._peer", return_value=(peer, "https://peer.invalid", "secret")), \
             patch("app.gamification_federation_client._local_peer_id", return_value="local-a"), \
             patch("app.gamification_federation_client._request", return_value=FakeResponse(body)) as request_mock:
            result = fetch_next("/tmp/root", "remote-b", "session 1")
        self.assertNotIn("preview_endpoint", result)
        called_url = request_mock.call_args.args[0]
        headers = request_mock.call_args.kwargs["headers"]
        self.assertEqual(called_url, "https://peer.invalid/federation/v1/gamification/sessions/session%201/next")
        self.assertEqual(headers["X-SimpleOffice-Game-Peer"], "local-a")
        self.assertEqual(result["peer_id"], "remote-b")
        self.assertNotIn("evil.invalid", called_url)

    def test_fetch_next_rejects_provider_outside_policy(self):
        peer = {"enabled": True, "policy": {"gamification": {"receive_challenges": True, "providers": ["images"]}}}
        body = b'{"challenge_id":"c1","provider":"contacts","kind":"city","answer_type":"text","prompt":"Ort?","payload":{}}'
        with patch("app.gamification_federation_client._peer", return_value=(peer, "https://peer.invalid", "secret")), \
             patch("app.gamification_federation_client._local_peer_id", return_value="local-a"), \
             patch("app.gamification_federation_client._request", return_value=FakeResponse(body)):
            with self.assertRaises(ValueError):
                fetch_next("/tmp/root", "friend", "session")

    def test_submit_answer_bounds_input_before_network(self):
        with patch("app.gamification_federation_client._peer", return_value=({}, "https://peer.invalid", "secret")), \
             patch("app.gamification_federation_client._request") as request_mock:
            with self.assertRaises(ValueError):
                submit_answer("/tmp/root", "friend", "c1", answer="x" * 501)
        request_mock.assert_not_called()

    def test_submit_unknown_contains_no_answer_value(self):
        captured = {}
        def fake_request(url, **kwargs):
            captured.update(kwargs)
            return FakeResponse(b'{"status":"unknown"}')
        with patch("app.gamification_federation_client._peer", return_value=({}, "https://peer.invalid", "secret")), \
             patch("app.gamification_federation_client._local_peer_id", return_value="local-a"), \
             patch("app.gamification_federation_client._request", side_effect=fake_request):
            result = submit_answer("/tmp/root", "friend", "c1", answer="must-not-leak", action="unknown")
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn(b"must-not-leak", captured["body"])
        self.assertEqual(captured["headers"]["X-SimpleOffice-Game-Peer"], "local-a")


if __name__ == "__main__":
    unittest.main()
