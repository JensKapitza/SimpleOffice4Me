"""Real SMS archive POST/auth/storage using a fake in-process native boundary."""
from pathlib import Path
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from app import app
from app.sms_archive_store import MAX_DEVICE_RECORDS, SmsArchive
from tests.http_api_fixture import CsrfMetadata, PublicHttpTestCase

BASE = "http://127.0.0.1:8765"
PAGE = "/communication/sms"
AGENT = "test SimpleOffice4Me-Android/fixture"
LEASE = "e" * 32
ALL_DATES = {"from": "", "through": ""}
MSG = {"source": "a" * 32, "provider_id": 20, "date_ms": 1750000001000,
       "address": "Synthetic sender", "body": "Synthetic <private> body", "direction": "inbox"}


class AndroidDeviceSmsTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.reader = Mock(side_effect=lambda lease, *_: ("ready", [MSG]) if lease == LEASE else ("unauthorized", []))
        self.active = Mock(return_value=True)
        self.root = app.config["DOCUMENT_ROOT"]
        self.webview = app.test_client()
        # Move the real authenticated test cookie to the exact embedded origin.
        name = app.config["SESSION_COOKIE_NAME"]
        self.webview.set_cookie(name, self.client.get_cookie(name).value, domain="127.0.0.1")
        app.config.update(ANDROID_PRIVATE_SMS_READER=self.reader, ANDROID_PRIVATE_SMS_LEASE_ACTIVE=self.active)
        page = self.webview.get(PAGE, base_url=BASE, headers={"User-Agent": AGENT})
        parser = CsrfMetadata()
        parser.feed(page.get_data(as_text=True))
        self.headers = {"X-CSRF-Token": parser.token, "X-SimpleOffice-SMS-Import": LEASE, "User-Agent": AGENT}

    def post(self, *, payload=None, headers=None, **kwargs):
        return self.webview.post(PAGE + "/device-import", base_url=BASE, headers=self.headers if headers is None else headers,
                                 json={"stage": "preview", "preview": "", **(ALL_DATES if payload is None else payload)}, **kwargs)

    def commit(self, preview, **period):
        return self.post(payload={**ALL_DATES, **period, "stage": "commit", "preview": preview})

    def test_preview_before_commit_idempotent_and_private(self):
        period = {"from": "2025-06-15", "through": "2025-06-15"}
        result = self.post(payload=period)
        self.assertEqual(200, result.status_code)
        ticket = result.json["preview"]
        self.assertRegex(ticket, r"^[a-f0-9]{32}$")
        self.assertEqual({"state": "preview", "preview": ticket, "added": 1, "already": 0}, result.json)
        self.reader.assert_called_once_with(LEASE, 1749945600000, 1750031999999)
        self.assertNotIn(MSG["body"].encode(), result.data)
        self.assertEqual("private, no-store", result.headers["Cache-Control"])
        self.assertEqual([], SmsArchive(self.root, "owner").page())
        self.assertEqual({"state": "ok", "added": 1, "already": 0}, self.commit(ticket, **period).json)
        self.assertEqual({"state": "preview_expired"}, self.commit(ticket, **period).json)
        replay = self.post().json
        self.assertEqual((0, 1), (replay["added"], replay["already"]))
        self.assertEqual({"state": "ok", "added": 0, "already": 1}, self.commit(replay["preview"]).json)
        self.assertEqual([MSG], SmsArchive(self.root, "owner").page())
        self.assertEqual([], SmsArchive(self.root, "unrelated").page())
        archived = self.webview.get(PAGE, base_url=BASE, headers={"User-Agent": AGENT})
        self.assertIn(b"Synthetic &lt;private&gt; body", archived.data)

    def test_preview_expires_and_cannot_be_redeemed_for_another_period(self):
        with patch("app.sms_device_preview.monotonic", return_value=100.0):
            ticket = self.post().json["preview"]
        with patch("app.sms_device_preview.monotonic", return_value=101.0):
            self.assertEqual({"state": "preview_expired"}, self.commit(ticket, **{"from": "2025-06-15"}).json)
        with patch("app.sms_device_preview.monotonic", return_value=401.0):
            self.assertEqual({"state": "preview_expired"}, self.commit(ticket).json)
        self.reader.assert_called_once()
        self.assertEqual([], SmsArchive(self.root, "owner").page())

    def test_other_session_cannot_redeem_preview(self):
        ticket = self.post().json["preview"]
        new_client = app.test_client()
        self._login(new_client, "owner")
        name = app.config["SESSION_COOKIE_NAME"]
        another = app.test_client()
        another.set_cookie(name, new_client.get_cookie(name).value, domain="127.0.0.1")
        page = another.get(PAGE, base_url=BASE, headers={"User-Agent": AGENT})
        parser = CsrfMetadata()
        parser.feed(page.get_data(as_text=True))
        response = another.post(PAGE + "/device-import", base_url=BASE, json={**ALL_DATES, "stage": "commit", "preview": ticket},
                                headers={**self.headers, "X-CSRF-Token": parser.token})
        self.assertEqual({"state": "preview_expired"}, response.json)
        self.reader.assert_called_once()
        self.assertEqual("ok", self.commit(ticket).json["state"])

    def test_extra_edited_or_removed_rows_require_a_new_preview(self):
        for changed in ([MSG, {**MSG, "provider_id": 21}], [{**MSG, "body": "Changed"}], []):
            with self.subTest(changed=len(changed)):
                self.reader.side_effect = None
                self.reader.return_value = "ready", [MSG]
                ticket = self.post().json["preview"]
                self.reader.return_value = "ready", changed
                self.assertEqual({"state": "preview_changed"}, self.commit(ticket).json)
                self.assertEqual([], SmsArchive(self.root, "owner").page())

    def test_changed_archive_counts_roll_back_all_new_rows(self):
        self.reader.side_effect = None
        self.reader.return_value = "ready", [MSG, {**MSG, "provider_id": 21}]
        ticket = self.post().json["preview"]
        archive = SmsArchive(self.root, "owner")
        archive.append([MSG])
        self.assertEqual({"state": "preview_changed"}, self.commit(ticket).json)
        self.assertEqual([MSG], archive.page())

    def test_login_csrf_origin_manifest_gate_and_one_time_native_lease(self):
        self.assertIn(b'id="sms-device-import"', self.webview.get(PAGE, base_url=BASE,
                                                                  headers={"User-Agent": AGENT}).data)
        self.assertNotIn(b'id="sms-device-import"', self.webview.get(PAGE, base_url=BASE).data)
        self.assertEqual(403, self.post(headers={k: v for k, v in self.headers.items() if k != "X-CSRF-Token"}).status_code)
        self.assertEqual(404, self.post(environ_overrides={"REMOTE_ADDR": "192.168.3.20"}).status_code)
        self.assertEqual(404, self.post(headers={**self.headers, "X-Forwarded-Proto": "https"}).status_code)
        self.assertEqual(403, self.post(headers={**self.headers, "X-SimpleOffice-SMS-Import": ""}).status_code)
        anonymous = app.test_client().post(PAGE + "/device-import", base_url=BASE, headers=self.headers,
                                           json={"from": "", "through": ""})
        self.assertIn(anonymous.status_code, (302, 403))
        self.reader.assert_not_called()
        self.assertEqual(403, self.post(headers={**self.headers, "X-SimpleOffice-SMS-Import": "0" * 32}).status_code)
        self.assertEqual([], SmsArchive(self.root, "owner").page())
        app.config.pop("ANDROID_PRIVATE_SMS_READER")
        self.assertEqual(404, self.post().status_code)

    def test_date_validation_bounds_and_no_browser_supplied_record(self):
        for payload, status in (
            ({"from": "2025-02-29", "through": ""}, 422),
            ({"from": "2025-07-01", "through": "2025-06-01"}, 422),
            ({"from": "", "through": "", "records": [MSG]}, 400),
            ({"from": "x" * 600, "through": ""}, 413),
        ):
            with self.subTest(status=status):
                self.assertEqual(status, self.post(payload=payload).status_code)
        self.reader.assert_not_called()

    def test_cancel_deny_and_invalid_provider_results_do_not_write(self):
        for state in ("denied", "unavailable", "too_many", "invalid_rows", "cancelled"):
            self.reader.side_effect = None
            self.reader.return_value = state, [MSG]
            self.assertEqual({"state": state}, self.post().json)
        self.reader.return_value = "ready", [MSG]
        self.active.return_value = False
        self.assertEqual({"state": "cancelled"}, self.post().json)
        self.active.return_value = True
        self.reader.return_value = "ready", [MSG] * (MAX_DEVICE_RECORDS + 1)
        self.assertEqual(503, self.post().status_code)
        self.reader.return_value = "ready", [MSG, MSG]
        self.assertEqual(503, self.post().status_code)
        self.assertEqual([], SmsArchive(self.root, "owner").page())

    def test_conflicting_snapshot_rolls_back_every_new_message(self):
        archive = SmsArchive(self.root, "owner")
        archive.append([MSG])
        self.reader.side_effect = None
        self.reader.return_value = "ready", [{**MSG, "provider_id": 21}, {**MSG, "body": "Reused ID"}]
        self.assertEqual({"state": "conflict"}, self.post().json)
        self.assertEqual([MSG], archive.page())

    def test_only_opt_in_manifest_declares_one_read_permission(self):
        root = Path(__file__).resolve().parents[1] / "android/apk/app/src"
        name = "{http://schemas.android.com/apk/res/android}name"
        def permissions(path):
            return [item.attrib[name] for item in ET.parse(root / path).getroot().findall("uses-permission")]
        self.assertNotIn("android.permission.READ_SMS", permissions("main/AndroidManifest.xml"))
        self.assertEqual(["android.permission.READ_SMS"], permissions("deviceSms/AndroidManifest.xml"))
