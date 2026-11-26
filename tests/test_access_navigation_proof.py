"""Normal HTTP login and fallback only; no production test adapter can arm this page."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import app, kids_hand_assets
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

    def test_translated_layout_preserves_skip_target_and_native_sign_out(self):
        switch = self.client.post("/documents/settings/language", data={"language": "de"},
                                  headers=self._csrf_headers(self.client, PROOF))
        self.assertEqual(switch.status_code, 302)
        page = self.client.get(PROOF)
        german = BeautifulSoup(page.text, "html.parser")
        self.assertEqual(german.html["lang"], "de")
        self.assertEqual(german.select_one("main nav")["aria-label"], "Kontonavigation")
        self.assertIn("Blicksteuerung aus", german.select_one("[data-access-status]").get_text())
        switch = self.client.post("/documents/settings/language", data={"language": "en"},
                                  headers=self._csrf_headers(self.client, PROOF))
        self.assertEqual(switch.status_code, 302)
        page = self.client.get(PROOF)
        self.assertEqual(page.status_code, 200)
        english = BeautifulSoup(page.text, "html.parser")
        self.assertEqual(english.html["lang"], "en")
        self.assertEqual(len(english.select("main#main-content")), 1)
        self.assertIsNotNone(english.select_one("a.skip-link[href='#main-content']"))
        self.assertEqual(english.select_one("main nav")["aria-label"], "Account navigation")
        self.assertIn("Gaze control off", english.select_one("[data-access-status]").get_text())
        self.assertEqual(len(english.select("script[src='/static/js/i18n.js']")), 1)
        logout = english.select_one("main form[action='/auth/logout']")
        self.assertEqual(logout.select_one("button").get_text(strip=True), "Sign out")
        token = logout.select_one("input[name='_csrf_token']")["value"]
        self.assertEqual(self.client.post(logout["action"], data={"_csrf_token": token}).status_code, 302)
        self.assertEqual(self.client.get(PROOF).status_code, 302)

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
        with patch.dict(app.config, {"HAND_NAV_STAGING": True, "KIDS_HAND_GAME_ASSET_DIR": "/nonexistent"}):
            dashboard = self.client.get("/documents/dashboard")
            self.assertIn(f'href="{PROOF}"', dashboard.get_data(as_text=True))
            self.assertIn("camera=()", dashboard.headers["Permissions-Policy"])
            page = self.client.get(PROOF)
            self.assertIn('data-ready="no"', page.get_data(as_text=True))
            self.assertNotIn("access_hand_staging.mjs", page.get_data(as_text=True))
            self.assertIn("camera=()", page.headers["Permissions-Policy"])

        with patch.dict(app.config, {"HAND_NAV_STAGING": True}), patch("app.kids_hand_assets.ready", return_value=True), patch(
            "app.kids_hand_assets.simd_ready", return_value=True,
        ):
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

    def test_staged_simd_requires_both_verified_files_without_changing_base_readiness(self):
        files = {
            "vision_wasm_internal.js": ("application/javascript", b"fixture loader"),
            "vision_wasm_internal.wasm": ("application/wasm", b"fixture WASM"),
        }
        pins = {name: (mime, len(data), sha256(data).hexdigest()) for name, (mime, data) in files.items()}
        assets = "/kids/lernen/handzeichen/assets/"
        with TemporaryDirectory() as folder, patch.dict(app.config, {
            "HAND_NAV_STAGING": True, "KIDS_HAND_GAME_ASSET_DIR": folder,
        }), patch.object(kids_hand_assets, "ready", return_value=True), patch.object(kids_hand_assets, "SIMD_ASSETS", pins):
            def page_flags():
                region = BeautifulSoup(self.client.get(PROOF).text, "html.parser").select_one("[data-hand-nav]")
                return region["data-ready"], region["data-simd"]

            self.assertEqual(("no", "no"), page_flags())
            not_ready = self.client.get(PROOF)
            self.assertIn("camera=()", not_ready.headers["Permissions-Policy"])
            self.assertNotIn("access_hand_staging.mjs", not_ready.text)
            fallback = BeautifulSoup(not_ready.text, "html.parser")
            self.assertIsNotNone(fallback.select_one('a[href="/documents/"]'))
            self.assertIn("Finger", fallback.select_one("[data-hand-nav-status]").get_text())
            for count, (name, (_, data)) in enumerate(files.items(), 1):
                (Path(folder) / name).write_bytes(data)
                if count == 1:
                    self.assertEqual(("no", "no"), page_flags())
            self.assertEqual(("yes", "yes"), page_flags())
            for name, (mime, data) in files.items():
                self.assertEqual(302, app.test_client().get(assets + name).status_code)
                response = self.client.get(assets + name)
                self.assertEqual((200, mime, data), (response.status_code, response.mimetype, response.data))
                self.assertIn("camera=()", response.headers["Permissions-Policy"])
            binary = "vision_wasm_internal.wasm"
            (Path(folder) / binary).write_bytes(b"fixture XXXX")  # exact same size; changed digest
            self.assertEqual(("no", "no"), page_flags())
            self.assertEqual(404, self.client.get(assets + binary).status_code)
            with patch.dict(app.config, {"HAND_NAV_STAGING": False}):
                self.assertNotIn("data-hand-nav", self.client.get(PROOF).text)
                self.assertNotIn(f'href="{PROOF}"', self.client.get("/documents/dashboard").text)
