"""HTTP blackbox coverage of persisted WebDAV quota behavior."""
from app.settings_store import SettingsStore

try:
    from .webdav_test_base import WebDavTestBase
except ImportError:
    from webdav_test_base import WebDavTestBase

from app import app


class NetworkQuotaBlackboxTest(WebDavTestBase):
    def test_persisted_quota_rejects_upload(self):
        SettingsStore(app.config["DOCUMENT_ROOT"]).save(
            {"network": {"webdav_quota_bytes": 1}}, "jens"
        )
        response = self.client.put(
            f"{self.files}/quota-test.txt", data=b"more than one byte", headers=self.auth
        )
        self.assertEqual(response.status_code, 507)
        self.assertIn(b"quota-not-exceeded", response.data)

    def test_legacy_deployment_quota_still_applies_before_override(self):
        app.config["WEBDAV_QUOTA_BYTES"] = 1
        response = self.client.put(
            f"{self.files}/quota-test.txt", data=b"more than one byte", headers=self.auth
        )
        self.assertEqual(response.status_code, 507)

    def test_explicit_zero_disables_legacy_quota(self):
        app.config["WEBDAV_QUOTA_BYTES"] = 1
        SettingsStore(app.config["DOCUMENT_ROOT"]).save(
            {"network": {"webdav_quota_bytes": 0}}, "jens"
        )
        response = self.client.put(
            f"{self.files}/quota-test.txt", data=b"allowed", headers=self.auth
        )
        self.assertIn(response.status_code, (201, 204))
