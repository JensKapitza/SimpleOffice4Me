import os
import unittest
from unittest.mock import patch

from flask import Flask

from app.resource_commander_access import api_access_authorized, remote_access_authorized
from app.resource_federation import FederationResourceProvider
from app.resource_provider import ProviderCapabilities


class ResourceCommanderAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(TESTING=False, SECRET_KEY="test-secret")

    def test_general_federation_token_does_not_authorize_commander(self):
        env = {
            "SIMPLEOFFICE_FEDERATION_TOKEN": "federation-secret",
            "SIMPLEOFFICE_RESOURCE_COMMANDER_TOKEN": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.app.test_request_context(
                "/resource-commander/api/list",
                headers={"Authorization": "Bearer federation-secret"},
            ):
                self.assertFalse(remote_access_authorized())
                self.assertFalse(api_access_authorized())

    def test_dedicated_commander_token_authorizes_remote_access(self):
        env = {
            "SIMPLEOFFICE_FEDERATION_TOKEN": "federation-secret",
            "SIMPLEOFFICE_RESOURCE_COMMANDER_TOKEN": "commander-secret",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.app.test_request_context(
                "/resource-commander/api/list",
                headers={"Authorization": "Bearer commander-secret"},
            ):
                self.assertTrue(remote_access_authorized())
                self.assertTrue(api_access_authorized())


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
