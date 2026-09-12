import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_attestations import FederationAttestationStore, verify_attestation
from app.federation_identity import FederationIdentity


class FederationAttestationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="attestation-test", DOCUMENT_ROOT=str(self.root))
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()
        self.temp.cleanup()

    def test_signed_attestation_verifies_and_tampering_fails(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PEER_ID": "peer-a"}):
            store = FederationAttestationStore(self.root)
            value = store.add_signed("peer-b", "VERIFIED_IN_PERSON", propagation="TRANSITIVE", max_hops=2)
            public_key = FederationIdentity(self.root).public_identity()["public_key"]
        self.assertTrue(verify_attestation(value, public_key))
        changed = {**value, "verified_peer_id": "peer-c"}
        self.assertFalse(verify_attestation(changed, public_key))

    def test_direct_only_attestation_is_not_exported(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PEER_ID": "peer-a"}):
            store = FederationAttestationStore(self.root)
            store.add_signed("peer-b", "VERIFIED_ADMIN", propagation="DIRECT_ONLY")
            self.assertEqual(store.export_shareable(), [])

    def test_transitive_relay_hops_are_decremented(self):
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PEER_ID": "peer-a"}):
            origin = FederationAttestationStore(self.root)
            value = origin.add_signed("peer-b", "VERIFIED_IN_PERSON", propagation="TRANSITIVE", max_hops=1)
            public_key = FederationIdentity(self.root).public_identity()["public_key"]
        with tempfile.TemporaryDirectory() as remote_temp:
            remote_root = Path(remote_temp)
            with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PEER_ID": "peer-c"}):
                remote = FederationAttestationStore(remote_root)
                remote.save_verified(value, public_key)
                relayed = remote.export_shareable()
                self.assertEqual(len(relayed), 1)
                self.assertEqual(relayed[0]["relay_hops_remaining"], 0)
                remote.save_verified(relayed[0], public_key)
                self.assertEqual(remote.export_shareable(), [])


if __name__ == "__main__":
    unittest.main()
