"""Normal HTTP login and fallback only; no production test adapter can arm this page."""
from bs4 import BeautifulSoup

from app import app
from http_api_fixture import PublicHttpTestCase

PROOF = "/documents/bedienhilfe"


class AccessNavigationProofTests(PublicHttpTestCase):
    def test_authenticated_fallback_only_and_native_app_links(self):
        normal = self.client.get("/", follow_redirects=True)
        self.assertEqual(normal.status_code, 200)
        self.assertEqual(normal.request.path, "/documents/dashboard")
        page = self.client.get(PROOF)
        self.assertEqual(page.status_code, 200)
        self.assertIn("camera=(), microphone=()", page.headers["Permissions-Policy"])
        self.assertIn("no-store", page.headers["Cache-Control"])
        self.assertNotIn("'wasm-unsafe-eval'", page.headers["Content-Security-Policy"])
        soup = BeautifulSoup(page.get_data(as_text=True), "html.parser")
        root = soup.select_one("[data-access-proof]")
        self.assertIsNotNone(root)
        self.assertTrue(root.select_one("[data-access-start]").has_attr("disabled"))
        self.assertTrue(root.select_one("[data-access-marker]").has_attr("hidden"))
        self.assertNotIn("Hokuspokus", root.get_text())
        self.assertNotIn("Hocus-pocus", root.get_text())
        self.assertNotIn("access_navigation_session", page.get_data(as_text=True))
        self.assertEqual(
            {a["data-access-target"]: a["href"] for a in root.select("a[data-access-target]")},
            {"dashboard": "/documents/dashboard", "documents": "/documents/", "calendar": "/documents/calendar"},
        )
        for anchor in root.select("a[data-access-target]"):
            self.assertEqual(self.client.get(anchor["href"]).status_code, 200)

    def test_no_anonymous_camera_and_no_mutation(self):
        anonymous = app.test_client().get(PROOF)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("camera=(), microphone=()", anonymous.headers["Permissions-Policy"])
        # The application's generic two-part page handler receives unknown POSTs.
        post = self.client.post(PROOF, headers=self._csrf_headers(self.client, PROOF))
        self.assertEqual(post.status_code, 404)
        self.assertIn("camera=(), microphone=()", post.headers["Permissions-Policy"])


class HandShortcutStagingTests(PublicHttpTestCase):
    def test_opt_in_asset_verification_and_proof_only_permission(self):
        from unittest.mock import patch

        with patch.dict(app.config, {"HAND_NAV_STAGING": True, "KIDS_HAND_GAME_ASSET_DIR": "/nonexistent"}):
            dashboard = self.client.get("/documents/dashboard")
            self.assertNotIn(f'href="{PROOF}"', dashboard.get_data(as_text=True))
            self.assertIn("camera=()", dashboard.headers["Permissions-Policy"])
            page = self.client.get(PROOF)
            self.assertIn('data-ready="no"', page.get_data(as_text=True))
            self.assertNotIn("access_hand_staging.mjs", page.get_data(as_text=True))
            self.assertIn("camera=()", page.headers["Permissions-Policy"])

        with patch.dict(app.config, {"HAND_NAV_STAGING": True}), patch("app.kids_hand_assets.ready", return_value=True):
            dashboard = self.client.get("/documents/dashboard")
            self.assertIn(f'href="{PROOF}"', dashboard.get_data(as_text=True))
            self.assertIn("camera=()", dashboard.headers["Permissions-Policy"])
            page = self.client.get(PROOF)
            body = page.get_data(as_text=True)
            self.assertIn('data-ready="yes"', body)
            self.assertIn("access_hand_staging.mjs", body)
            self.assertNotIn("access_navigation_session.mjs", body)
            self.assertIn("camera=(self), microphone=()", page.headers["Permissions-Policy"])
            self.assertIn("frame-ancestors 'none'", page.headers["Content-Security-Policy"])
            self.assertIn("connect-src 'self'", page.headers["Content-Security-Policy"])
            self.assertIn("'wasm-unsafe-eval'", page.headers["Content-Security-Policy"])
            self.assertNotIn("'unsafe-eval'", page.headers["Content-Security-Policy"])
            soup = BeautifulSoup(body, "html.parser")
            self.assertTrue(soup.select_one("[data-hand-nav-start]").has_attr("disabled"))
            self.assertTrue(soup.select_one("[data-hand-nav-arm]").has_attr("disabled"))
            self.assertEqual(
                [item["value"] for item in soup.select("[data-hand-nav-destination] option")],
                ["dashboard", "documents"],
            )
            self.assertIn("camera=()", app.test_client().get(PROOF).headers["Permissions-Policy"])
