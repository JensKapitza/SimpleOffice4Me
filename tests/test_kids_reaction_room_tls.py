"""End-to-end loopback TLS through the existing application's real LAN ingress."""
import json
import os
import socket
import ssl
import tempfile
import threading
from http.client import HTTPSConnection
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from werkzeug.serving import make_server

from app import app
from app.lan_proxy import LanProxyRuntime
from app.lan_proxy_self_signed import CERT_NAME
from tests.http_api_fixture import CsrfMetadata, PublicHttpTestCase
from tests.test_kids_reaction_rooms import API, PAGE


class TLSBrowserClient:
    """Independent auth/cookie jar over HTTP; not a renderer or a phone."""

    def __init__(self, connection):
        self.connection = connection
        self.cookies = SimpleCookie()
        self.token = ""
        self.origin = f"https://127.0.0.1:{connection.port}"

    def fetch(self, method, path, body=None, mime=None):
        headers = {"Cookie": "; ".join(f"{key}={v.value}" for key, v in self.cookies.items())}
        if mime is not None:
            headers.update({"Content-Type": mime, "X-CSRF-Token": self.token, "Origin": self.origin})
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        for key, value in response.getheaders():
            if key.lower() == "set-cookie":
                self.cookies.load(value)
        return response.status, response.read()

    def read_form(self, url):
        status, page = self.fetch("GET", url)
        if status != 200:
            raise AssertionError((status, page[:200]))
        parser = CsrfMetadata()
        parser.feed(page.decode())
        if not parser.token:
            raise AssertionError("CSRF metadata missing from real page")
        self.token = parser.token
        return page

    def login(self, username):
        self.read_form("/auth/login")
        return self.fetch("POST", "/auth/login", urlencode({
            "username": username, "password": "test-only-api-password",
        }), "application/x-www-form-urlencoded")[0]

    def room(self, action, *, token="", **kwargs):
        path = API
        message = json.dumps({"action": action, **kwargs})
        headers = {
            "Cookie": "; ".join(f"{key}={v.value}" for key, v in self.cookies.items()),
            "Content-Type": "application/json", "Origin": self.origin,
            "X-CSRF-Token": self.token, "X-Reaction-Station": token,
        }
        self.connection.request("POST", path, body=message, headers=headers)
        response = self.connection.getresponse()
        return response.status, json.loads(response.read())


class ReactionRoomTLSIntegrationTests(PublicHttpTestCase):
    def test_two_signed_in_clients_share_game_state_over_verified_https(self):
        self._register(self.client, "station")
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        cert_dir = Path(work.name) / "tls"
        previous = app.extensions.pop("kids_reaction_rooms", None)

        def cleanup_state():
            app.extensions.pop("kids_reaction_rooms", None)
            if previous is not None:
                app.extensions["kids_reaction_rooms"] = previous

        self.addCleanup(cleanup_state)
        backend = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()

        def shutdown():
            backend.shutdown()
            backend.server_close()
            thread.join(timeout=3)

        self.addCleanup(shutdown)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        runtime = LanProxyRuntime()
        with patch.dict(os.environ, {"SIMPLEOFFICE_LAN_PROXY_TLS_DIR": str(cert_dir)}):
            state = runtime.start(app, {
                "enabled": True, "bind_host": "127.0.0.1", "port": port,
                "tls_cert": "", "tls_key": "",
            }, backend_host="127.0.0.1", backend_port=backend.server_port,
                document_root=app.config["DOCUMENT_ROOT"])
        self.assertEqual("running", state["state"], state["message"])
        self.addCleanup(runtime.stop)
        context = ssl.create_default_context(cafile=str(cert_dir / CERT_NAME))
        browsers = []
        for identity in ("owner", "station"):
            connection = HTTPSConnection("127.0.0.1", port, context=context, timeout=5)
            self.addCleanup(connection.close)
            browser = TLSBrowserClient(connection)
            self.assertEqual(302, browser.login(identity))
            self.assertIn(b"data-room-host", browser.read_form(PAGE))
            browsers.append(browser)
        host, phone = browsers
        status, a = host.room("host")
        self.assertEqual(201, status)
        status, b = phone.room("join", code=a["join_code"])
        self.assertEqual(201, status)
        status, active = host.room("start", token=a["token"], mode="practice")
        self.assertEqual(200, status)
        for station, enrolled in zip(browsers, (a, b)):
            status, seen = station.room("state", token=enrolled["token"])
            self.assertEqual(200, status)
            self.assertEqual(active["target"], seen["target"])
            ticket = seen["target"]["ticket"]
            self.assertEqual(200, station.room("ack", token=enrolled["token"], ticket=ticket)[0])
            status, active = station.room("press", token=enrolled["token"], ticket=ticket, ms=0)
            self.assertEqual(200, status)
        status, shared = host.room("state", token=a["token"])
        self.assertEqual(200, status)
        self.assertEqual([1, 2], [row["id"] for row in shared["scores"][0]["rows"]])
        self.assertEqual(200, host.room("stop", token=a["token"])[0])
        self.assertEqual(410, phone.room("state", token=b["token"])[0])
