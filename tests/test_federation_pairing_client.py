import http.client
import ipaddress
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from app.federation_identity import public_key_fingerprint
from app.federation_pairing_client import (
    PairingClientError,
    _read_json,
    claim_pairing_code,
    remote_pairing_status,
)


class _Response:
    def __init__(self, status, value):
        self.status = status
        self._raw = json.dumps(value).encode("utf-8")

    def read(self, limit):
        return self._raw[:limit]


class _Connection:
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class FederationPairingClientTests(unittest.TestCase):
    public_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    def peer(self):
        return {
            "peer_id": "peer-b",
            "label": "Peer B",
            "base_url": "https://peer-b.example/simpleoffice",
            "country": "DE",
            "public_key": self.public_key,
            "fingerprint": public_key_fingerprint(self.public_key),
        }

    def profile(self, **changes):
        value = {
            **self.peer(),
            "application": {"name": "SimpleOffice4Me", "version": "3.0.0"},
            "federation": {
                "name": "simpleoffice-federation",
                "min_version": 1,
                "max_version": 1,
            },
            "features": {},
        }
        value.update(changes)
        return value

    @staticmethod
    def target():
        parsed = urlsplit("https://peer-b.example/simpleoffice")
        return parsed.geturl(), parsed, 443, [ipaddress.ip_address("203.0.113.10")]

    def test_claim_rechecks_identity_then_posts_to_same_pinned_address(self):
        discovery = _Connection(_Response(200, self.profile()))
        claim = _Connection(_Response(200, {"pairing": {"peer_id": "peer-a", "status": "claimed"}}))
        request_body = {
            "protocol": "simpleoffice-pairing-v1",
            "operation": "claim",
            "peer_id": "peer-a",
        }

        with patch(
            "app.federation_pairing_client._validated_target", return_value=self.target()
        ), patch(
            "app.federation_pairing_client._connection_for", side_effect=[discovery, claim]
        ) as connection_for, patch(
            "app.federation_pairing_client.build_pairing_request", return_value=request_body
        ):
            result = claim_pairing_code("/tmp/root", self.peer(), "2345-6789-ABCD")

        self.assertEqual({"peer_id": "peer-a", "status": "claimed"}, result)
        self.assertEqual("GET", discovery.requests[0][0])
        self.assertEqual(
            "/simpleoffice/.well-known/simpleoffice-federation", discovery.requests[0][1]
        )
        self.assertEqual("POST", claim.requests[0][0])
        self.assertEqual("/simpleoffice/federation/v1/pairing/claim", claim.requests[0][1])
        self.assertEqual(request_body, json.loads(claim.requests[0][2]))
        self.assertEqual("203.0.113.10", connection_for.call_args_list[0].args[1])
        self.assertEqual("203.0.113.10", connection_for.call_args_list[1].args[1])
        self.assertTrue(discovery.closed)
        self.assertTrue(claim.closed)

    def test_identity_mismatch_prevents_code_submission(self):
        mismatched = self.profile(peer_id="different-peer")
        discovery = _Connection(_Response(200, mismatched))

        with patch(
            "app.federation_pairing_client._validated_target", return_value=self.target()
        ), patch(
            "app.federation_pairing_client._connection_for", return_value=discovery
        ) as connection_for, patch(
            "app.federation_pairing_client.build_pairing_request"
        ) as build_request:
            with self.assertRaisesRegex(PairingClientError, "Identität der Gegenstelle"):
                claim_pairing_code("/tmp/root", self.peer(), "2345-6789-ABCD")

        connection_for.assert_called_once()
        build_request.assert_not_called()
        self.assertEqual(1, len(discovery.requests))
        self.assertNotIn("2345-6789-ABCD", repr(discovery.requests))

    def test_empty_or_oversized_code_is_rejected_before_network_access(self):
        with patch("app.federation_pairing_client._validated_target") as validate:
            for value in ("", "A" * 65):
                with self.subTest(value=value), self.assertRaisesRegex(
                    PairingClientError, "ungültig"
                ):
                    claim_pairing_code("/tmp/root", self.peer(), value)
        validate.assert_not_called()


    def test_incomplete_http_reply_is_user_safe_and_closes_the_pinned_connection(self):
        discovery = _Connection(_Response(200, self.profile()))
        status = _Connection(_Response(200, {"pairing": {"peer_id": "peer-a", "status": "missing"}}))
        with patch(
            "app.federation_pairing_client._validated_target", return_value=self.target(),
        ), patch(
            "app.federation_pairing_client._connection_for", side_effect=[discovery, status],
        ), patch(
            "app.federation_pairing_client.build_pairing_request", return_value={"peer_id": "peer-a"},
        ), patch.object(
            status.response, "read", side_effect=http.client.IncompleteRead(b"private diagnostic body")
        ):
            with self.assertRaises(PairingClientError) as fault:
                remote_pairing_status("/tmp/root", self.peer())

        self.assertEqual("transport", fault.exception.reason)
        self.assertIn("outbound HTTP connection", fault.exception.user_message("en"))
        self.assertNotIn("private diagnostic", fault.exception.user_message("en"))
        self.assertTrue(discovery.closed)
        self.assertTrue(status.closed)

    def test_endpoint_validation_denial_never_opens_a_connection(self):
        with patch(
            "app.federation_pairing_client._validated_target", side_effect=ValueError("internal exit policy"),
        ), patch("app.federation_pairing_client._connection_for") as connect:
            with self.assertRaises(PairingClientError) as fault:
                remote_pairing_status("/tmp/root", self.peer())

        self.assertEqual("endpoint", fault.exception.reason)
        self.assertIn("local network policy", fault.exception.user_message("en"))
        self.assertNotIn("internal exit policy", fault.exception.user_message("en"))
        connect.assert_not_called()

    def test_decoder_depth_failure_reports_invalid_response(self):
        response = _Response(200, {})
        with patch("app.federation_pairing_client.json.loads", side_effect=RecursionError("fixture decoder error")):
            with self.assertRaises(PairingClientError) as fault:
                _read_json(response, 1024)
        self.assertEqual("response", fault.exception.reason)
        detail = fault.exception.user_message("en")
        self.assertIn("did not return a valid pairing response", detail)
        self.assertNotIn("fixture decoder error", detail)

    def test_remote_authentication_refusal_differs_from_missing_pairing_endpoint(self):
        for http_status, body, expected in (
            (401, {"error": "pairing_authentication_failed"}, "identity_rejected"),
            (404, {}, "remote_endpoint"),
        ):
            with self.subTest(http_status=http_status):
                discovery = _Connection(_Response(200, self.profile()))
                status = _Connection(_Response(http_status, body))
                with patch(
                    "app.federation_pairing_client._validated_target", return_value=self.target(),
                ), patch(
                    "app.federation_pairing_client._connection_for", side_effect=[discovery, status],
                ), patch(
                    "app.federation_pairing_client.build_pairing_request", return_value={"peer_id": "peer-a"},
                ):
                    with self.assertRaises(PairingClientError) as fault:
                        remote_pairing_status("/tmp/root", self.peer())
                self.assertEqual(expected, fault.exception.reason)
                self.assertTrue(status.closed)


if __name__ == "__main__":
    unittest.main()
