"""Login page: QR/address section is a collapsed native disclosure.

The login form stays the primary focus (first, autofocus on username);
the QR/address carousel keeps working unchanged once expanded.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import app
from app.db import ensure_auth_database

TARGETS = [
    {
        "qr": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        "url": "http://192.168.1.10:5000",
        "label": "LAN-Adresse",
    },
    {"qr": "", "url": "http://127.0.0.1:5000", "label": "Lokal"},
]


class LoginQrDisclosureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {
            key: app.config.get(key) for key in ("DATABASE", "TESTING")
        }
        app.config.update(
            DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
            TESTING=True,
        )
        with app.app_context():
            ensure_auth_database()
        self.client = app.test_client()

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def _get(self, targets):
        with mock.patch("app.auth.login_qr_targets", return_value=targets):
            return self.client.get("/auth/login")

    def test_qr_section_collapsed_by_default(self) -> None:
        response = self._get(TARGETS)
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn(b"<details", body)
        self.assertNotIn(b"<details open", body)
        self.assertIn(b"<summary", body)
        heading = "Auf einem anderen Gerät öffnen".encode("utf-8")
        self.assertIn(heading, body)
        self.assertLess(body.index(b"<summary"), body.index(heading))

    def test_login_stays_primary(self) -> None:
        body = self._get(TARGETS).data
        self.assertLess(body.index(b"<form"), body.index(b"<details"))
        self.assertIn(b'id="username"', body)
        self.assertIn(b"autofocus", body)

    def test_carousel_intact_inside_disclosure(self) -> None:
        body = self._get(TARGETS).data
        self.assertIn(b"login-qr-carousel", body)
        self.assertIn(b"http://192.168.1.10:5000", body)
        self.assertIn(b"http://127.0.0.1:5000", body)
        self.assertIn(b"data-qr-prev", body)
        self.assertIn(b"data-qr-next", body)
        self.assertIn(b"data-qr-number", body)
        slides = [
            match.start()
            for match in re.finditer(rb'<div class="login-qr-slide"', body)
        ]
        self.assertEqual(len(slides), 2)
        first = body[slides[0]:body.index(b">", slides[0])]
        second = body[slides[1]:body.index(b">", slides[1])]
        self.assertNotIn(b"hidden", first)
        self.assertIn(b"hidden", second)

    def test_no_disclosure_without_targets(self) -> None:
        response = self._get([])
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"<details", response.data)
        self.assertNotIn(b"login-qr-carousel", response.data)
        self.assertNotIn(b"auth-main-attached", response.data)
        self.assertNotIn(b"auth-qr-attached", response.data)
        self.assertIn(b"autofocus", response.data)

    def test_qr_panel_attached_to_login_card(self) -> None:
        body = self._get(TARGETS).data
        self.assertIn(b"auth-stack", body)
        self.assertIn(b"auth-main-attached", body)
        self.assertIn(b"auth-qr-attached", body)
        stack = body.index(b"auth-stack")
        section = body.index(b"auth-main-attached")
        panel = body.index(b"auth-qr-attached")
        self.assertLess(stack, section)
        self.assertLess(section, panel)


if __name__ == "__main__":
    unittest.main()
