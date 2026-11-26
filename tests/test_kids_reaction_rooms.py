"""Real signed-in Flask clients with independent sessions; no phone is simulated as hardware."""
from app import app
from app.kids_reaction_room import ReactionRooms
from tests.http_api_fixture import CsrfMetadata, PublicHttpTestCase


ORIGIN = "https://192.168.5.10:8443"
PAGE = "/kids/reaktionsparcours/rooms"
API = f"{PAGE}/api"


class ReactionRoomTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self._register(self.client, "station")
        self._register(self.client, "visitor")
        self.phone = app.test_client()
        self.visitor = app.test_client()
        self._login(self.phone, "station")
        self._login(self.visitor, "visitor")
        self.now = 100.0
        previous = app.extensions.pop("kids_reaction_rooms", None)
        self.addCleanup(self.restore_rooms, previous)
        app.extensions["kids_reaction_rooms"] = ReactionRooms(clock=lambda: self.now)
        self.csrf = {}
        for client in (self.client, self.phone, self.visitor):
            cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"])
            client.set_cookie(cookie.key, cookie.value, domain="192.168.5.10")
            response = client.get(PAGE, base_url=ORIGIN)
            self.assertEqual(200, response.status_code)
            parser = CsrfMetadata()
            parser.feed(response.get_data(as_text=True))
            self.assertTrue(parser.token)
            self.csrf[client] = parser.token

    @staticmethod
    def restore_rooms(prior):
        if prior is None:
            app.extensions.pop("kids_reaction_rooms", None)
        else:
            app.extensions["kids_reaction_rooms"] = prior

    def send(self, client, action, *, token="", status=200, **data):
        response = client.post(API, base_url=ORIGIN, json={"action": action, **data}, headers={
            "X-CSRF-Token": self.csrf[client], "Origin": ORIGIN, "X-Reaction-Station": token,
        })
        self.assertEqual(status, response.status_code, response.get_data(as_text=True)[:500])
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        return response.get_json()

    def enroll(self):
        a = self.send(self.client, "host", status=201)
        b = self.send(self.phone, "join", status=201, code=a["join_code"])
        self.assertEqual((1, 2), (a["station"], b["station"]))
        self.assertEqual(("host", "station"), (a["role"], b["role"]))
        self.assertIsNone(b["join_code"])
        return a, b

    def test_duel_keeps_device_order_both_turns_and_both_see_same_scores(self):
        a, b = self.enroll()
        state = self.send(self.client, "start", token=a["token"], mode="duel")
        lineup = state["sequence"]
        self.assertEqual([1, 2, 1, 2, 2, 1], lineup)
        self.assertIsNone(state["join_code"])
        self.assertEqual("unavailable", self.send(
            self.visitor, "join", status=404, code=a["join_code"])["error"])
        for turn in (1, 2):
            for station in lineup:
                actor, actor_data = (self.client, a) if station == 1 else (self.phone, b)
                current = self.send(actor, "state", token=actor_data["token"])
                self.assertEqual(turn, current["turn"])
                ticket = current["target"]["ticket"]
                self.assertEqual(station, current["target"]["station"])
                self.send(actor, "ack", token=actor_data["token"], ticket=ticket)
                self.now += turn / 5
                state = self.send(actor, "press", token=actor_data["token"], ticket=ticket, ms=turn * 190)
            if turn == 1:
                self.assertEqual("change", state["phase"])
                state = self.send(self.client, "next", token=a["token"])
                self.assertEqual(lineup, state["sequence"])
        self.assertEqual("complete", state["phase"])
        self.assertEqual([1140, 2280], [s["total"] for s in state["scores"]])
        self.assertEqual([316, 158], [s["rate"] for s in state["scores"]])
        self.assertEqual(1, state["winner"])
        phone_scores = self.send(self.phone, "state", token=b["token"])["scores"]
        self.assertEqual(state["scores"], phone_scores)
        new_round = self.send(self.client, "start", token=a["token"], mode="practice")
        self.assertEqual([], new_round["scores"])
        self.assertEqual([1, 2], new_round["stations"])
        self.assertEqual(13, new_round["target"]["ticket"])
        self.send(self.client, "ack", token=a["token"], ticket=1, status=409)
        self.send(self.visitor, "join", code=a["join_code"], status=404)

    def test_session_bound_host_commands_and_one_current_target(self):
        a, b = self.enroll()
        for command in ("stop", "next"):
            self.assertEqual("host_only", self.send(self.phone, command, token=b["token"], status=403)["error"])
        self.assertEqual("host_only", self.send(
            self.phone, "start", token=b["token"], mode="practice", status=403)["error"])
        self.assertEqual("room_gone", self.send(self.phone, "state", token=a["token"], status=410)["error"])
        self.assertEqual("room_gone", self.send(self.visitor, "state", token=b["token"], status=410)["error"])
        current = self.send(self.client, "start", token=a["token"], mode="practice")["target"]
        self.assertEqual(1, current["station"])
        ticket = current["ticket"]
        self.send(self.phone, "ack", token=b["token"], ticket=ticket, status=409)
        self.send(self.client, "press", token=a["token"], ticket=ticket, ms=0, status=409)
        self.send(self.client, "ack", token=a["token"], ticket=ticket)
        self.send(self.client, "ack", token=a["token"], ticket=ticket, status=409)
        self.send(self.client, "press", token=a["token"], ticket=ticket, ms=8, status=409)
        self.now += 0.2
        self.send(self.client, "press", token=a["token"], ticket=ticket, ms=150)
        self.send(self.client, "press", token=a["token"], ticket=ticket, ms=150, status=409)
        self.assertEqual({"phase": "closed"}, self.send(self.client, "stop", token=a["token"]))
        self.send(self.phone, "state", token=b["token"], status=410)

    def test_hidden_or_missing_station_invalidates_the_round_not_the_roster_order(self):
        a, b = self.enroll()
        self.send(self.client, "start", token=a["token"], mode="duel")
        self.send(self.phone, "leave", token=b["token"])
        self.send(self.client, "state", token=a["token"], status=410)
        self.send(self.phone, "join", code=a["join_code"], status=404)
        a, b = self.enroll()
        target = self.send(self.client, "start", token=a["token"], mode="duel")["target"]
        self.send(self.client, "ack", token=a["token"], ticket=target["ticket"])
        self.now += 3
        self.send(self.client, "state", token=a["token"])
        self.now += 3
        self.send(self.client, "state", token=a["token"], status=410)
        self.send(self.phone, "state", token=b["token"], status=410)

    def test_authenticated_lobby_csrf_origin_https_and_request_limits(self):
        with self.client.get(PAGE, base_url=ORIGIN) as page:
            self.assertIn(b"data-room-host", page.data)
            self.assertIn("no-referrer", page.headers["Referrer-Policy"])
        with self.client.get(PAGE, base_url="http://192.168.5.10") as page:
            self.assertNotIn(b"data-room-host", page.data)
        csrf = self.csrf[self.client]
        response = self.client.post(API, base_url=ORIGIN, json={"action": "host"})
        self.assertEqual(403, response.status_code)
        for base, headers, extra in (
            (ORIGIN, {"Origin": "https://different.example"}, {}),
            (ORIGIN, {"X-Forwarded-For": "192.168.0.1"}, {}),
            (ORIGIN, {}, {"REMOTE_ADDR": "203.0.113.20"}),
            ("http://192.168.5.10:8443", {}, {}),
        ):
            self.assertEqual(403, self.client.post(
                API, base_url=base, json={"action": "host"},
                headers={"X-CSRF-Token": csrf, **headers}, environ_overrides=extra,
            ).status_code)
        self.assertEqual(302, app.test_client().get(PAGE, base_url=ORIGIN).status_code)
        self.send(self.client, "host", status=400, extra="property")
        self.send(self.client, "host", status=400, extra="C" * 260)

    def test_lobby_enrollment_capacity_expiry_and_rate_limits(self):
        a, b = self.enroll()
        self.send(self.phone, "join", code=a["join_code"], status=409)
        c = self.send(self.visitor, "join", code=a["join_code"], status=201)
        self.assertEqual(3, c["station"])
        self.assertEqual("room_full", self.send(self.phone, "join", code=a["join_code"], status=409)["error"])
        self.send(self.phone, "leave", token=b["token"])
        self.assertEqual([1, 3], self.send(self.client, "state", token=a["token"])["stations"])
        for _ in range(9):
            self.send(self.phone, "join", code="AAAA2222", status=404)
        self.send(self.phone, "join", code="AAAA2222", status=429)
        self.now += 305
        self.send(self.visitor, "join", code=a["join_code"], status=404)
        self.send(self.visitor, "state", token=c["token"], status=410)

    def test_timed_miss_counts_only_after_ack_and_server_window(self):
        a, b = self.enroll()
        first = self.send(self.client, "start", token=a["token"], mode="duel")["target"]
        self.send(self.client, "ack", token=a["token"], ticket=first["ticket"])
        for _ in range(3):
            self.now += 3
            state = self.send(self.client, "state", token=a["token"])
            self.send(self.phone, "state", token=b["token"])
        self.assertEqual([{"id": 1, "hit": False, "ms": 7000}], state["rows"])
        self.assertEqual(2, state["target"]["station"])
        self.now += 2
        self.send(self.client, "state", token=a["token"])
        self.send(self.phone, "state", token=b["token"])
        self.now += 2
        self.send(self.client, "state", token=a["token"], status=410)
