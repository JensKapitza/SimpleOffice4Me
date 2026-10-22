"""Black-box verification that the persisted WebDAV quota controls uploads."""
from unittest.mock import patch

from app import app, configured_webdav_quota_bytes
from app.settings_store import SettingsStore

try:
    from .webdav_test_base import WebDavTestBase
except ImportError:
    from webdav_test_base import WebDavTestBase


class NetworkSettingsWebDavQuotaTests(WebDavTestBase):
    def test_persisted_quota_applies_after_service_configuration_reload(self):
        document_root = app.config["DOCUMENT_ROOT"]
        SettingsStore(document_root).save(
            {"network": {"webdav_quota_mib": 1}}, "jens"
        )

        # Scope the simulated process restart to this request so the shared test app
        # cannot leak the quota into later tests.
        quota_bytes = configured_webdav_quota_bytes(document_root)
        with patch.dict(app.config, {"WEBDAV_QUOTA_BYTES": quota_bytes}):
            response = self.client.put(
                f"{self.files}/quota-test.txt",
                data=b"x" * (1024 * 1024),
                headers=self.auth,
            )
        self.assertEqual(507, response.status_code)
        self.assertIn(b"quota-not-exceeded", response.data)


if __name__ == "__main__":
    import unittest

    unittest.main()
