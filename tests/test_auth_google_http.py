import io
import unittest
from unittest.mock import patch

from app.auth import GOOGLE_TOKEN_URL, _google_json_request


class _Response:
    def __init__(self, body: bytes, headers=None):
        self._body = io.BytesIO(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self._body.read(size)


class GoogleOAuthHttpTests(unittest.TestCase):
    def test_exact_google_endpoint_is_allowed(self):
        with patch("app.auth._GOOGLE_OPENER.open", return_value=_Response(b'{"access_token":"token"}')) as opener:
            result = _google_json_request(GOOGLE_TOKEN_URL, data=b"grant_type=test")
        self.assertEqual("token", result["access_token"])
        opener.assert_called_once()

    def test_untrusted_google_lookalike_is_rejected_before_network(self):
        with patch("app.auth._GOOGLE_OPENER.open") as opener:
            for url in (
                "http://oauth2.googleapis.com/token",
                "https://oauth2.googleapis.com.evil.invalid/token",
                "https://user:secret@oauth2.googleapis.com/token",
                "https://oauth2.googleapis.com:444/token",
                "https://oauth2.googleapis.com/token?redirect=https://evil.invalid",
            ):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    _google_json_request(url)
            opener.assert_not_called()

    def test_invalid_declared_response_size_is_rejected(self):
        with patch(
            "app.auth._GOOGLE_OPENER.open",
            return_value=_Response(b"{}", {"Content-Length": "not-a-number"}),
        ):
            with self.assertRaisesRegex(ValueError, "Content-Length"):
                _google_json_request(GOOGLE_TOKEN_URL)


if __name__ == "__main__":
    unittest.main()
