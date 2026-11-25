"""HTTP coverage of the real APK preview launch and session authorization path."""
from pathlib import Path
from xml.etree import ElementTree

from app import app
from app.db import get_db
from tests.http_api_fixture import CsrfMetadata, PublicHttpTestCase


ORIGIN = "http://127.0.0.1:8765"
PAGE = "/android/device-sources/"
APK = {"User-Agent": "Test Browser SimpleOffice4Me-Android/1"}
MANIFEST = Path(__file__).resolve().parents[1] / "android/apk/app/src/main/AndroidManifest.xml"
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


class AndroidDeviceSourceTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.mobile = app.test_client()
        self.signed_in()

    def signed_in(self):
        page = self.mobile.get("/auth/login", base_url=ORIGIN, headers=APK)
        form = CsrfMetadata()
        form.feed(page.get_data(as_text=True))
        login = self.mobile.post(
            "/auth/login", base_url=ORIGIN, headers={**APK, "X-CSRF-Token": form.token},
            data={"username": "owner", "password": "test-only-api-password"},
        )
        self.assertEqual(302, login.status_code)

    def gate(self, source, *, token=True, headers=None, client=None, data=None):
        client = self.mobile if client is None else client
        token_header = {}
        if token:
            page = client.get(PAGE, base_url=ORIGIN, headers=APK)
            self.assertEqual(200, page.status_code)
            parser = CsrfMetadata()
            parser.feed(page.get_data(as_text=True))
            token_header = {"X-CSRF-Token": parser.token}
        return client.post(PAGE + "authorize/" + source, base_url=ORIGIN,
                           headers={**APK, **token_header, **(headers or {})}, data=data)

    def test_apk_page_and_preflight_use_real_logged_in_session_but_never_return_records(self):
        page = self.mobile.get(PAGE, base_url=ORIGIN, headers=APK)
        self.assertEqual(200, page.status_code)
        self.assertEqual("no-store", page.headers["Cache-Control"])
        for source in ("calendar", "contacts"):
            self.assertIn(f'data-device-source="{source}" disabled', page.get_data(as_text=True))
            response = self.gate(source)
            self.assertEqual(204, response.status_code)
            self.assertEqual(b"", response.data)
            self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_non_apk_and_forwarded_requests_do_not_access_overview_or_gate(self):
        self.assertEqual(404, self.mobile.get(PAGE, base_url=ORIGIN).status_code)
        self.assertEqual(404, self.mobile.get(PAGE, base_url=ORIGIN, headers=APK,
                                             environ_overrides={"REMOTE_ADDR": "192.0.2.21"}).status_code)
        self.assertEqual(404, self.gate("contacts", headers={"X-Forwarded-For": "192.0.2.21"}).status_code)

    def test_csrf_login_and_input_are_required_but_no_payload_is_accepted(self):
        self.assertEqual(403, self.gate("calendar", token=False).status_code)
        self.assertEqual(403, self.gate("contacts", token=False, client=app.test_client()).status_code)
        self.assertEqual(404, self.gate("drive").status_code)
        self.assertEqual(400, self.gate("contacts", data={"record": "synthetic"}).status_code)

    def test_feature_access_is_independently_rechecked_on_preflight(self):
        # Apply fixture-only account policy to the synthetic foreground user.
        with app.app_context():
            db = get_db()
            db.execute("UPDATE user SET is_admin = 0 WHERE username = 'owner'")
            db.execute(
                "INSERT INTO user_permission (user_id, feature, enabled, updated_at) "
                "SELECT id, 'calendar', 0, CURRENT_TIMESTAMP FROM user WHERE username = 'owner'"
            )
            db.commit()
        response = self.mobile.get(PAGE, base_url=ORIGIN, headers=APK)
        self.assertEqual(200, response.status_code)
        self.assertNotIn('data-device-source="calendar"', response.get_data(as_text=True))
        self.assertEqual(403, self.gate("calendar").status_code)
        self.assertEqual(204, self.gate("contacts").status_code)

    def test_manifest_adds_separate_read_permissions_without_write_permissions(self):
        root = ElementTree.parse(MANIFEST).getroot()
        permissions = {item.attrib[ANDROID_NS + "name"] for item in root.findall("uses-permission")}
        self.assertIn("android.permission.READ_CALENDAR", permissions)
        self.assertIn("android.permission.READ_CONTACTS", permissions)
        self.assertNotIn("android.permission.WRITE_CALENDAR", permissions)
        self.assertNotIn("android.permission.WRITE_CONTACTS", permissions)
