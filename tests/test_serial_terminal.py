from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash

from app import app
from app.datalogger_store import DataLoggerStore
from app.db import get_db
from tests.http_api_fixture import PublicHttpTestCase


PAGE = "/datalogger/serial"


class SerialTerminalTests(PublicHttpTestCase):
    def test_real_datalogger_page_and_loopback_render(self):
        self.assertIn(f'href="{PAGE}"', self.client.get("/datalogger/").get_data(as_text=True))
        store = DataLoggerStore(app.config["DOCUMENT_ROOT"])
        owned = store.create_channel("Messung", "owner")
        result = self.client.get(PAGE)
        self.assertEqual(200, result.status_code)
        self.assertIn("serial=(self)", result.headers["Permissions-Policy"])
        self.assertEqual("private, no-store", result.headers["Cache-Control"])
        soup = BeautifulSoup(result.data, "html.parser")
        self.assertIsNotNone(soup.select_one('main[data-local="1"] [data-connect]'))
        self.assertIsNotNone(soup.select_one('a[href$="#capture-first"]'))
        route = f"/datalogger/channels/{owned}/samples"
        form = soup.select_one("form[data-sample-form]")
        self.assertEqual(route, form["action"])
        self.assertTrue(form.select_one('[name="_csrf_token"]')["value"])
        reply = self.client.post(route, headers=self._csrf_headers(self.client, PAGE), data={"value": "21.5"})
        self.assertEqual(302, reply.status_code)
        self.assertEqual(21.5, store.samples(owned)[0]["value"])

    def test_network_only_exposes_help_and_never_loads_terminal_script(self):
        for request in ({"headers": {"X-Forwarded-For": "192.0.2.3"}},
                        {"environ_overrides": {"REMOTE_ADDR": "192.0.2.3"}}):
            with self.subTest(request=request):
                response = self.client.get(PAGE, **request)
                self.assertEqual(200, response.status_code)
                self.assertIn("serial=()", response.headers["Permissions-Policy"])
                self.assertIn(b'data-local="0"', response.data)
                self.assertNotIn(b"serial-terminal.js", response.data)
        self.assertEqual(302, app.test_client().get(PAGE).status_code)
        self.assertIn(self.client.post(PAGE, headers=self._csrf_headers(self.client, PAGE)).status_code, (404, 405))

    def test_readonly_channel_is_not_offered_or_writable(self):
        store = DataLoggerStore(app.config["DOCUMENT_ROOT"])
        other = app.test_client()
        with app.app_context():
            db = get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES(?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("reader", generate_password_hash("test-only-api-password")),
            )
            db.commit()
        self._login(other, "reader")
        read_only = store.create_channel("Nur Ansicht", "owner", readers=["reader"])
        writable = store.create_channel("Bearbeitbar", "owner", editors=["reader"])
        response = other.get(PAGE)
        soup = BeautifulSoup(response.data, "html.parser")
        choices = [item["value"] for item in soup.select("select[data-channel] option")]
        self.assertEqual([f"/datalogger/channels/{writable}/samples"], choices)
        route = f"/datalogger/channels/{read_only}/samples"
        token = self._csrf_headers(other, PAGE)
        reply = other.post(route, headers=token, data={"value": "42"})
        self.assertEqual(403, reply.status_code)
        self.assertEqual([], list(store.samples(read_only)))
