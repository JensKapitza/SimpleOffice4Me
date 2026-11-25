"""Authenticated HTML and direct-loopback-only hardware entry."""
from app import app
from tests.http_api_fixture import PublicHttpTestCase


PAGE = "/kids/reaktionsparcours/"


class KidsReactionPageTests(PublicHttpTestCase):
    def test_logged_in_kids_dashboard_links_real_virtual_play_page(self):
        home = self.client.get("/kids/")
        self.assertEqual(200, home.status_code)
        self.assertIn(f'href="{PAGE}"', home.get_data(as_text=True))
        response = self.client.get(PAGE)
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.assertEqual("no-referrer", response.headers["Referrer-Policy"])
        self.assertRegex(body, r'data-kids-reaction data-en="[01]" data-usb="1"')
        for script in ("game", "usb", "app"):
            self.assertIn(f"/static/js/kids_reaction_{script}.js", body)
            self.assertEqual(200, self.client.get(f"/static/js/kids_reaction_{script}.js").status_code)
        self.assertIn('data-keyboard aria-label=', body)
        self.assertIn("1, 2", body)
        self.assertIn('data-connect disabled', body)
        self.assertIn("serial=(self)", response.headers["Permissions-Policy"])

    def test_virtual_still_renders_without_serial_for_nonlocal_and_forwarded_requests(self):
        local = self.client.get_cookie(app.config["SESSION_COOKIE_NAME"])
        self.assertIsNotNone(local)
        self.client.set_cookie(local.key, local.value, domain="office.example.invalid")
        for options in (
            {"base_url": "http://office.example.invalid"},
            {"headers": {"X-Forwarded-For": "192.0.2.5"}},
            {"environ_overrides": {"REMOTE_ADDR": "192.0.2.8"}},
        ):
            with self.subTest(options=options):
                response = self.client.get(PAGE, **options)
                self.assertEqual(200, response.status_code)
                self.assertIn(b'data-usb="0"', response.data)
                self.assertIn(b'data-station="1"', response.data)
                self.assertIn("serial=()", response.headers["Permissions-Policy"])

    def test_anonymous_and_mutation_requests_have_no_game_hardware_endpoint(self):
        response = app.test_client().get(PAGE)
        self.assertEqual(302, response.status_code)
        self.assertIn("/auth/login", response.headers["Location"])
        response = self.client.post(PAGE, headers=self._csrf_headers(self.client))
        self.assertEqual(405, response.status_code)
