"""LAN receive readiness through the actual administrator page and forms."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app import app
from app.federation_lan_receive_state import LanReceiveState, MAX_RECEIVE_WINDOW_SECONDS
from app.federation_local_profile import local_profile
from tests.federation_admin_ui_fixture import FederationAdminUiFixture

DISCOVERY = "/admin/federation/peer-discovery"
START = DISCOVERY + "/receive/start"
STOP = DISCOVERY + "/receive/stop"
START_TIME = 1795560000
DEADLINE = "2026-11-24T22:55:00+00:00"
RAW_DEADLINE = START_TIME + MAX_RECEIVE_WINDOW_SECONDS


class FederationLanReceiveUiTests(FederationAdminUiFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        app.config["TEST_CSRF_PROTECTION"] = True
        self.now = START_TIME
        clock = patch("app.federation_lan_receive_state.time", SimpleNamespace(time=lambda: self.now))
        addresses = patch("app.federation_peer_admin._connect_addresses", return_value=[])
        clock.start()
        addresses.start()
        self.addCleanup(clock.stop)
        self.addCleanup(addresses.stop)

    def _page(self, response):
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.data, "html.parser")

    def _post_form(self, page, action):
        form = page.select_one(f'#lan-receive form[action="{action}"]')
        self.assertIsNotNone(form)
        token = form.select_one('input[name="_csrf_token"]')["value"]
        result = self.client.post(action, data={"_csrf_token": token})
        self.assertEqual(result.status_code, 302)
        location = urlsplit(result.location)
        self.assertEqual((location.path, location.fragment), (DISCOVERY, "lan-receive"))
        return self._page(self.client.get(location.path))

    def _assert_deadline(self, card):
        time_element = card.select_one("time")
        self.assertEqual(time_element["datetime"], DEADLINE)
        self.assertEqual(time_element.get_text(strip=True), "2026-11-24 22:55:00 UTC")
        self.assertNotIn(str(RAW_DEADLINE), card.get_text())

    def _profile_capabilities(self):
        with app.app_context():
            return local_profile(self.root, "https://office.example.test", prefer_fallback=True)["capabilities"]

    def test_start_reload_exact_expiry_and_restart_are_distinct(self):
        page = self._page(self.client.get(DISCOVERY))
        self.assertIsNone(page.select_one("#lan-receive time"))
        page = self._post_form(page, START)
        card = page.select_one("#lan-receive")
        self._assert_deadline(card)
        self.assertNotIn(str(RAW_DEADLINE), page.get_text())
        self.assertIn("öffnet keine Netzwerkports", card.get_text())
        self.assertTrue(self._profile_capabilities()["lan_receive_ready"])
        self.assertEqual(self._profile_capabilities()["lan_receive_expires_at"], RAW_DEADLINE)

        self.now = RAW_DEADLINE - 1
        reloaded = self.client.get(DISCOVERY)
        fresh = self._page(reloaded).select_one("#lan-receive")
        self.assertEqual(reloaded.headers.get("Cache-Control"), "private, no-store")
        self._assert_deadline(fresh)
        self.assertIsNotNone(fresh.select_one(f'form[action="{STOP}"]'))
        self.assertIn("Stand beim Laden der Seite", fresh.get_text())

        self.now += 1
        expired_page = self._page(self.client.get(DISCOVERY))
        expired = expired_page.select_one("#lan-receive")
        self._assert_deadline(expired)
        self.assertIn("Empfangshinweis abgelaufen", expired.get_text())
        self.assertIn("bleibt auch nach Ablauf des Hinweises auffindbar", expired.get_text())
        self.assertIn("auf dem sendenden Gerät die Verbindung in Richtung dieses Geräts", expired.get_text())
        self.assertIsNone(expired.select_one(f'form[action="{STOP}"]'))
        self.assertFalse(self._profile_capabilities()["lan_receive_ready"])
        self.assertEqual(self._profile_capabilities()["lan_receive_expires_at"], RAW_DEADLINE)

        self._post_form(expired_page, START)
        self.assertEqual(LanReceiveState(self.root).status()["expires_at"], RAW_DEADLINE + MAX_RECEIVE_WINDOW_SECONDS)

    def test_stop_returns_to_card_and_does_not_appear_expired(self):
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "en"
        active = self._post_form(self._page(self.client.get(DISCOVERY)), START)
        self.assertIn("Receive notice active", active.select_one("#lan-receive").get_text())
        self._assert_deadline(active.select_one("#lan-receive"))
        stopped = self._post_form(active, STOP).select_one("#lan-receive")
        self.assertIn("No receive notice is active", stopped.get_text())
        self.assertNotIn("expired", stopped.get_text())
        self.assertIsNone(stopped.select_one("time"))
        self.assertEqual(LanReceiveState(self.root).status(), {"active": False, "started_at": 0, "expires_at": 0})

    def test_member_and_request_without_csrf_do_not_change_state(self):
        member = app.test_client()
        app.config["TEST_CSRF_PROTECTION"] = False
        try:
            login = member.post("/auth/login", data={
                "username": "federation-ui-user", "password": "federation-user-password",
            })
            self.assertLess(login.status_code, 400)
        finally:
            app.config["TEST_CSRF_PROTECTION"] = True
        with member.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.assertEqual(member.get(DISCOVERY).status_code, 403)
        for action in (START, STOP):
            with self.subTest(action=action):
                self.assertEqual(member.post(action, data={"_csrf_token": "x" * 40}).status_code, 403)
                self.assertEqual(self.client.post(action).status_code, 403)
        self.assertFalse(LanReceiveState(self.root).path.exists())

    def test_numeric_but_unrenderable_deadline_does_not_break_dashboard(self):
        LanReceiveState(self.root).start(now=10**30)
        card = self._page(self.client.get(DISCOVERY)).select_one("#lan-receive")
        self.assertIsNone(card.select_one("time"))
        self.assertIn("Zeitpunkt nicht verfügbar", card.get_text())
        self.assertIsNotNone(card.select_one(f'form[action="{STOP}"]'))
