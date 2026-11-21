import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app import app_config_store
from app.resource_commander_access import api_access_authorized, remote_access_authorized
from app.resource_commander_credentials import save as save_commander_credentials
from app.resource_federation import FederationResourceProvider
from app.resource_provider import ProviderCapabilities


class ResourceCommanderAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=False, SECRET_KEY="test-secret")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store_patcher = patch.object(
            app_config_store, "settings_path",
            return_value=Path(self.temp.name) / "app-config.json",
        )
        store_patcher.start()
        self.addCleanup(store_patcher.stop)
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)

    def test_unconfigured_commander_token_rejects_any_bearer(self):
        with self.app.test_request_context(
            "/resource-commander/api/list",
            headers={"Authorization": "Bearer commander-secret"},
        ):
            self.assertFalse(remote_access_authorized())
            self.assertFalse(api_access_authorized())

    def test_dedicated_commander_token_authorizes_remote_access(self):
        save_commander_credentials({"token": "commander-secret"})
        with self.app.test_request_context(
            "/resource-commander/api/list",
            headers={"Authorization": "Bearer commander-secret"},
        ):
            self.assertTrue(remote_access_authorized())
            self.assertTrue(api_access_authorized())

    def test_local_browser_commander_requires_admin_role(self):
        from flask import g

        with self.app.test_request_context("/resource-commander/api/list"):
            g.user = {"is_admin": False, "is_disabled": False}
            self.assertFalse(api_access_authorized())
            g.user = {"is_admin": True, "is_disabled": False}
            self.assertTrue(api_access_authorized())

    def test_internal_metadata_windows_alias_is_denied(self):
        from app.resource_local import LocalResourceProvider, ProviderError

        with tempfile.TemporaryDirectory() as temp:
            provider = LocalResourceProvider(temp)
            for path in (
                ".SIMPLEOFFICE-META/secret.json",
                "folder/.simpleoffice-meta. /secret.json",
                r"folder\\.simpleoffice-history \\secret.json",
            ):
                with self.subTest(path=path), self.assertRaises(ProviderError):
                    provider.read_range(path, 0, 20)


class FederationCapabilityTests(unittest.TestCase):
    def test_remote_capabilities_cannot_raise_local_policy_ceiling(self):
        allowed = ProviderCapabilities(
            read=True,
            write=False,
            delete=False,
            move=False,
            copy=True,
            folders=True,
            search=True,
            metadata=True,
            streaming=True,
            smart_view=False,
            server_side_copy=True,
        )
        provider = FederationResourceProvider(
            "peer-a", "Peer A", "https://example.invalid", "token",
            allowed_capabilities=allowed,
        )
        provider._apply_remote_capabilities({
            key: True for key in ProviderCapabilities.__dataclass_fields__
        })
        self.assertTrue(provider.capabilities.read)
        self.assertTrue(provider.capabilities.copy)
        self.assertFalse(provider.capabilities.write)
        self.assertFalse(provider.capabilities.delete)
        self.assertFalse(provider.capabilities.move)
        self.assertFalse(provider.capabilities.smart_view)


if __name__ == "__main__":
    unittest.main()
