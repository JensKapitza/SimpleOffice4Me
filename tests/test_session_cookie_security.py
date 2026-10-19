"""Regression tests for HTTPS session-cookie policy."""
import os
import unittest
from http.cookies import SimpleCookie
from unittest.mock import patch

from flask import Flask, session
from werkzeug.middleware.proxy_fix import ProxyFix

from app import app as production_app
from app import configured_session_cookie_secure


class SessionCookieSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test-session-cookie-secret"
        self.app.config.update(
            SESSION_COOKIE_SECURE=False,
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
        )
        self.app.session_interface = production_app.session_interface

        @self.app.get("/")
        def touch_session():
            session["visited"] = True
            return "ok"

    def cookie(self, *, base_url="http://office.example.test", headers=None, proxy=False):
        if proxy:
            self.app.wsgi_app = ProxyFix(self.app.wsgi_app, x_proto=1)
        response = self.app.test_client().get("/", base_url=base_url, headers=headers or {})
        values = SimpleCookie()
        values.load(response.headers["Set-Cookie"])
        return values["session"], response

    def test_plain_http_does_not_require_secure_for_local_development(self):
        cookie, _ = self.cookie()
        self.assertEqual("", cookie["secure"])
        self.assertIs(cookie["httponly"], True)
        self.assertEqual("Lax", cookie["samesite"])

    def test_https_request_sets_secure_cookie(self):
        cookie, _ = self.cookie(base_url="https://office.example.test")
        self.assertIs(cookie["secure"], True)

    def test_trusted_forwarded_https_sets_secure_cookie(self):
        cookie, _ = self.cookie(headers={"X-Forwarded-Proto": "https"}, proxy=True)
        self.assertIs(cookie["secure"], True)

    def test_untrusted_forwarded_proto_is_ignored(self):
        cookie, _ = self.cookie(headers={"X-Forwarded-Proto": "https"})
        self.assertEqual("", cookie["secure"])

    def test_explicit_https_policy_protects_http_fallback(self):
        self.app.config["SESSION_COOKIE_SECURE"] = True
        cookie, _ = self.cookie()
        self.assertIs(cookie["secure"], True)

    def test_https_environment_setting_is_explicit_and_case_insensitive(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_HTTPS": " TrUe "}):
            self.assertTrue(configured_session_cookie_secure())
        with patch.dict(os.environ, {"SIMPLEOFFICE_HTTPS": "false"}):
            self.assertFalse(configured_session_cookie_secure())


if __name__ == "__main__":
    unittest.main()
