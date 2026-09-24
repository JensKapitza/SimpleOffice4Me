from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_http import bp
from app.federation_peer_auth import headers as peer_headers, sign as peer_sign
from app.federation_store import FederationStore
from app.federation_worker import remote_encrypted_recovery_availability
from app.v2.authorization import AuthorizationStore, GrantRight
from app.v2.contracts import LogicalObjectId
from app.v2.encrypted_blob_store import EncryptedBlobStore
from app.v2.encrypted_recovery_descriptor import build_encrypted_recovery_descriptor
from app.v2.federation_policy import FederationPolicyStore


PATH = "/federation/v1/recovery/encrypted/availability"
SOURCE_PEER = "peer-a"
SOURCE_TOKEN = "peer-a-secret"
RECEIVER_TOKEN = "receiver-secret"


class FederationEncryptedRecoverySearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.root.mkdir()
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="encrypted-recovery-search-test",
            DOCUMENT_ROOT=str(self.root),
        )
        self.app.register_blueprint(bp)
        self.context = self.app.app_context()
        self.context.push()
        self.environment = patch.dict(
            os.environ,
            {
                "SIMPLEOFFICE_FEDERATION_TOKEN": RECEIVER_TOKEN,
                "SIMPLEOFFICE_FEDERATION_PEER_ID": "peer-local",
            },
            clear=False,
        )
        self.environment.start()
        self.client = self.app.test_client()

        federation = FederationStore(self.root)
        federation.save_peer(
            SOURCE_PEER,
            "Source",
            "https://source.invalid",
            SOURCE_TOKEN,
            enabled=True,
        )

        self.encrypted = EncryptedBlobStore(
            self.root,
            b"k" * 32,
            chunk_size=64 * 1024,
        )
        self.object_id = LogicalObjectId("encrypted-recovery-search-object")
        payload = (b"encrypted-recovery-search-" * 5000) + b"tail"
        version = self.encrypted.write(self.object_id, payload)
        self.descriptor = build_encrypted_recovery_descriptor(
            self.encrypted.version_manifest(version.version_id),
            recovery_profile_hash="1" * 64,
        )
        self.grant_ref = f"recovery:{self.descriptor['descriptor_id']}"
        self.authorization = AuthorizationStore(self.root)
        self.grant = self.authorization.issue_root(
            issuer="controller",
            subject=SOURCE_PEER,
            rights=(GrantRight.READ,),
            object_refs=(self.grant_ref,),
            expires_at=int(time.time()) + 600,
        )

    def tearDown(self):
        self.environment.stop()
        self.context.pop()
        self.temp.cleanup()

    def _payload(
        self,
        *,
        descriptor=None,
        authorization_ref=None,
        chunk_indexes=None,
    ):
        value = {
            "authorization_ref": authorization_ref or self.grant.grant_id,
            "descriptor": descriptor or self.descriptor,
        }
        if chunk_indexes is not None:
            value["chunk_indexes"] = list(chunk_indexes)
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def _headers(self, body: bytes):
        return {
            "Authorization": f"Bearer {RECEIVER_TOKEN}",
            "Content-Type": "application/json",
            **peer_headers(
                SOURCE_PEER,
                SOURCE_TOKEN,
                "POST",
                PATH,
                body,
            ),
        }

    def _post(self, *, descriptor=None, authorization_ref=None, chunk_indexes=None):
        body = self._payload(
            descriptor=descriptor,
            authorization_ref=authorization_ref,
            chunk_indexes=chunk_indexes,
        )
        return self.client.post(PATH, data=body, headers=self._headers(body))

    def test_exact_descriptor_grant_returns_only_index_availability(self):
        response = self._post()

        self.assertEqual(200, response.status_code)
        result = response.get_json()
        self.assertEqual(
            "simpleoffice-v2-encrypted-recovery-availability/v1",
            result["format"],
        )
        self.assertEqual(self.descriptor["descriptor_id"], result["descriptor_id"])
        self.assertEqual(
            result["requested_indexes"],
            result["available_indexes"],
        )
        self.assertEqual([], result["missing_indexes"])
        self.assertNotIn("physical_id", response.get_data(as_text=True))

        events = FederationStore(self.root).events(20)
        allowed = next(
            row for row in events
            if row["action"] == "encrypted_recovery_query_allowed"
        )
        self.assertEqual(
            self.descriptor["descriptor_id"],
            allowed["detail"]["descriptor_id"],
        )
        self.assertNotIn("physical_id", json.dumps(allowed["detail"]))

    def test_valid_peer_cannot_probe_an_ungranted_descriptor(self):
        other_id = LogicalObjectId("another-recovery-object")
        version = self.encrypted.write(other_id, b"other")
        other = build_encrypted_recovery_descriptor(
            self.encrypted.version_manifest(version.version_id),
            recovery_profile_hash="1" * 64,
        )

        response = self._post(descriptor=other)

        self.assertEqual(403, response.status_code)
        self.assertEqual("recovery_query_forbidden", response.get_json()["error"])

    def test_revoked_descriptor_grant_is_denied(self):
        self.authorization.revoke(self.grant.grant_id)

        response = self._post()

        self.assertEqual(403, response.status_code)

    def test_storage_block_overrides_positive_descriptor_grant(self):
        FederationPolicyStore(self.root).block(
            SOURCE_PEER,
            scope="storage",
            reason="synthetic deny",
        )

        response = self._post()

        self.assertEqual(403, response.status_code)
        denied = [
            row for row in FederationStore(self.root).events(20)
            if row["action"] == "encrypted_recovery_query_denied"
        ]
        self.assertTrue(denied)
        self.assertIn("policy:explicit_block", denied[0]["detail"]["reason"])

    def test_local_ciphertext_damage_is_reported_as_missing(self):
        first = self.descriptor["ciphertext_chunks"][0]
        chunk = (
            self.root
            / ".simpleoffice-v2"
            / "encrypted-blob-store"
            / "chunks"
            / f"{uuid.UUID(first['physical_id']).hex}.bin"
        )
        changed = bytearray(chunk.read_bytes())
        changed[0] ^= 1
        chunk.write_bytes(changed)

        response = self._post(chunk_indexes=(0,))

        self.assertEqual(200, response.status_code)
        result = response.get_json()
        self.assertEqual([], result["available_indexes"])
        self.assertEqual([0], result["missing_indexes"])

    def test_peer_signature_nonce_cannot_be_replayed(self):
        body = self._payload(chunk_indexes=(0,))
        headers = self._headers(body)

        first = self.client.post(PATH, data=body, headers=headers)
        second = self.client.post(PATH, data=body, headers=headers)

        self.assertEqual(200, first.status_code)
        self.assertEqual(401, second.status_code)

    def test_query_rate_limit_is_peer_scoped_and_persistent(self):
        with patch("app.federation_http.ENCRYPTED_RECOVERY_QUERY_LIMIT", 2):
            first = self._post(chunk_indexes=(0,))
            second = self._post(chunk_indexes=(0,))
            limited = self._post(chunk_indexes=(0,))

        self.assertEqual(200, first.status_code)
        self.assertEqual(200, second.status_code)
        self.assertEqual(429, limited.status_code)

    def test_normal_bearer_token_is_still_required(self):
        body = self._payload(chunk_indexes=(0,))
        headers = self._headers(body)
        headers.pop("Authorization")

        response = self.client.post(PATH, data=body, headers=headers)

        self.assertEqual(401, response.status_code)

    def test_client_signs_exact_body_with_source_peer_identity(self):
        federation = FederationStore(self.root)
        federation.save_peer(
            "peer-target",
            "Target",
            "https://target.invalid",
            "target-bearer-secret",
            enabled=True,
        )
        response_payload = {
            "format": "simpleoffice-v2-encrypted-recovery-availability/v1",
            "descriptor_id": self.descriptor["descriptor_id"],
            "object_id": self.descriptor["object_id"],
            "version_id": self.descriptor["version_id"],
            "total_chunks": len(self.descriptor["ciphertext_chunks"]),
            "requested_indexes": [0],
            "available_indexes": [0],
            "missing_indexes": [],
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with patch(
            "app.federation_worker._request",
            return_value=FakeResponse(),
        ) as request_call:
            result = remote_encrypted_recovery_availability(
                self.root,
                "peer-target",
                self.descriptor,
                self.grant.grant_id,
                chunk_indexes=(0,),
            )

        self.assertEqual([0], result["available_indexes"])
        kwargs = request_call.call_args.kwargs
        self.assertEqual("target-bearer-secret", kwargs["token"])
        self.assertEqual("POST", kwargs["method"])
        signed = kwargs["headers"]
        self.assertEqual("peer-local", signed["X-SimpleOffice-Peer-ID"])
        expected = peer_sign(
            "peer-local",
            RECEIVER_TOKEN,
            "POST",
            PATH,
            int(signed["X-SimpleOffice-Peer-Timestamp"]),
            signed["X-SimpleOffice-Peer-Nonce"],
            kwargs["body"],
        )
        self.assertEqual(expected, signed["X-SimpleOffice-Peer-Signature"])

    def test_capabilities_advertise_descriptor_scoped_recovery_search(self):
        response = self.client.get(
            "/federation/v1/capabilities",
            headers={"Authorization": f"Bearer {RECEIVER_TOKEN}"},
        )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()["encrypted_recovery_availability"])
        self.assertEqual(
            "recovery:<descriptor_id>",
            response.get_json()["encrypted_recovery_grant_scope"],
        )


if __name__ == "__main__":
    unittest.main()
