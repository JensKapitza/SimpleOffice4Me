import hmac
import ipaddress
import json
import os
import re
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from flask import Flask

from app.federation_diagnostics import _json_bytes, probe_peer, response_signature
from app.federation_discovery_endpoint import _getaddrinfo_bounded
from app.federation_http import bp
from app.federation_local_profile import local_peer_id
from app.federation_peer_auth import headers as peer_headers, sign as peer_sign
from app.federation_store import FederationStore


class _ProbeHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(size)
        self.server.request_count += 1
        nonce = self.headers.get("X-SimpleOffice-Peer-Nonce", "")
        peer_id = self.headers.get("X-SimpleOffice-Peer-ID", "")
        timestamp = self.headers.get("X-SimpleOffice-Peer-Timestamp", "")
        supplied = self.headers.get("X-SimpleOffice-Peer-Signature", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", nonce):
            self.send_response(400)
            self.end_headers()
            return
        expected = peer_sign(peer_id, self.server.peer_token, "POST", self.path, int(timestamp), nonce, body)
        if not hmac.compare_digest(supplied, expected):
            self.send_response(401)
            self.end_headers()
            return
        if self.server.status != 200:
            self.send_response(self.server.status)
            self.end_headers()
            return
        try:
            probe = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        response_body = _json_bytes({
            "peer_id": "remote-peer",
            "probe_id": probe["probe_id"],
            "received_at": 1_800_000_000,
        })
        signature = response_signature(self.server.peer_token, nonce, response_body)
        if self.server.invalid_signature:
            signature = "0" * 64
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-SimpleOffice-Diagnostic-Nonce", nonce)
        self.send_header("X-SimpleOffice-Diagnostic-Signature", signature)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)


class FederationDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="diagnostic-test-secret", DOCUMENT_ROOT=str(self.root))
        self.app.register_blueprint(bp)
        self.store = FederationStore(self.root)
        with self.app.app_context():
            self.store.save_peer("remote-peer", "Remote", "http://127.0.0.1:9", "unique-peer-token")
            self.token = self.store.peer_token("remote-peer")
        self.client = self.app.test_client()
        self.env = patch.dict(os.environ, {
            "SIMPLEOFFICE_FEDERATION_TOKEN": "shared-federation-token",
            "SIMPLEOFFICE_FEDERATION_ALLOW_LOOPBACK": "1",
        })
        self.env.start()

    def test_dns_lookup_deadline_returns_without_leaking_a_resolver_thread(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow_lookup(*_args, **_kwargs):
            started.set()
            try:
                release.wait(1)
                return []
            finally:
                finished.set()

        with patch("app.federation_discovery_endpoint.socket.getaddrinfo", side_effect=slow_lookup):
            with self.assertRaises(TimeoutError):
                _getaddrinfo_bounded("slow.invalid", 443, 0.01)
            self.assertTrue(started.wait(0.5))
            release.set()
            self.assertTrue(finished.wait(1))

    def tearDown(self):
        self.env.stop()
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(timeout=2)
        self.temp.cleanup()

    def _inbound_request(self, *, probe_id="probe-test", signature_override=None):
        path = "/federation/v1/diagnostics/probe"
        payload = _json_bytes({"probe_id": probe_id, "requested_at": 1_800_000_000})
        proof = peer_headers("remote-peer", self.token, "POST", path, payload)
        if signature_override is not None:
            proof["X-SimpleOffice-Peer-Signature"] = signature_override
        return self.client.post(
            path,
            data=payload,
            headers={
                "Authorization": "Bearer shared-federation-token",
                "Content-Type": "application/json",
                **proof,
            },
        ), proof, payload

    def test_inbound_authenticated_probe_is_persisted_and_receipt_is_signed(self):
        response, proof, _payload = self._inbound_request()
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual(proof["X-SimpleOffice-Peer-Nonce"], response.headers["X-SimpleOffice-Diagnostic-Nonce"])
        self.assertEqual(
            response_signature(self.token, proof["X-SimpleOffice-Peer-Nonce"], response.data),
            response.headers["X-SimpleOffice-Diagnostic-Signature"],
        )
        self.assertEqual(local_peer_id(), response.json["peer_id"])
        self.assertEqual("probe-test", response.json["probe_id"])
        diagnostic = self.store.network_diagnostic("remote-peer", direction="inbound", source="remote-peer")
        self.assertEqual("success", diagnostic["status"])
        self.assertEqual("authenticated_probe", diagnostic["category"])

    def test_inbound_probe_rejects_bad_signature_and_replay_without_claiming_success(self):
        response, _proof, _payload = self._inbound_request(signature_override="0" * 64)
        self.assertEqual(401, response.status_code)
        self.assertIsNone(self.store.network_diagnostic("remote-peer", direction="inbound", source="remote-peer"))

        valid, valid_proof, _ = self._inbound_request(probe_id="fresh-probe")
        self.assertEqual(200, valid.status_code)
        replay = self.client.post(
            "/federation/v1/diagnostics/probe",
            data=_json_bytes({"probe_id": "fresh-probe", "requested_at": 1_800_000_000}),
            headers={
                "Authorization": "Bearer shared-federation-token",
                "Content-Type": "application/json",
                **valid_proof,
            },
        )
        self.assertEqual(401, replay.status_code)

    def test_inbound_probe_rate_limit_is_bounded_and_recorded(self):
        responses = [self._inbound_request(probe_id=f"probe-{index}")[0] for index in range(11)]
        self.assertTrue(all(response.status_code == 200 for response in responses[:10]))
        self.assertEqual(429, responses[10].status_code)
        diagnostic = self.store.network_diagnostic("remote-peer", direction="inbound", source="remote-peer")
        self.assertEqual("success", diagnostic["status"])
        self.assertEqual("authenticated_probe", diagnostic["category"])

    def test_diagnostic_history_is_bounded_and_last_success_survives_failure(self):
        self.store.record_network_diagnostic(
            "remote-peer", direction="outbound", source="local", status="success",
            checked_at=1_800_000_000, detail={"stages": {"authentication": "ok"}},
        )
        for index in range(22):
            self.store.record_network_diagnostic(
                "remote-peer", direction="outbound", source="local", status="failure",
                category="http", checked_at=1_800_000_001 + index,
                detail={"attempt": index},
            )
        diagnostic = self.store.network_diagnostic("remote-peer", direction="outbound")
        history = self.store.network_diagnostic_history("remote-peer", direction="outbound", limit=20)
        self.assertEqual(1_800_000_000, diagnostic["last_success_at"])
        self.assertEqual(20, len(history))
        self.assertEqual(21, history[0]["detail"]["attempt"])
        self.assertEqual(2, history[-1]["detail"]["attempt"])

    def _start_probe_server(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
        self.server.peer_token = self.token
        self.server.status = 200
        self.server.invalid_signature = False
        self.server.request_count = 0
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        with self.app.app_context():
            self.store.save_peer(
                "remote-peer", "Remote",
                f"http://127.0.0.1:{self.server.server_port}/simpleoffice",
                "unique-peer-token",
            )

    def test_outbound_probe_uses_signed_request_and_persists_stage_results(self):
        self._start_probe_server()
        with self.app.app_context():
            result = probe_peer(self.root, "remote-peer", now=1_800_000_001)
        self.assertEqual("success", result["status"])
        self.assertEqual("ok", result["detail"]["stages"]["authentication"])
        self.assertEqual(1_800_000_000, result["detail"]["remote_received_at"])
        self.assertEqual(1, self.server.request_count)
        self.assertEqual(1_800_000_001, result["last_success_at"])

    def test_outbound_authentication_and_http_failures_are_classified(self):
        self._start_probe_server()
        self.server.invalid_signature = True
        with self.app.app_context():
            signature_failure = probe_peer(self.root, "remote-peer")
        self.assertEqual("authentication", signature_failure["category"])
        self.assertEqual("signature_failed", signature_failure["detail"]["stages"]["authentication"])

        self.server.invalid_signature = False
        self.server.status = 403
        with self.app.app_context():
            permission_failure = probe_peer(self.root, "remote-peer")
        self.assertEqual("permission", permission_failure["category"])
        self.assertEqual("403", permission_failure["detail"]["stages"]["http"])
        self.server.status = 401
        with self.app.app_context():
            authentication_failure = probe_peer(self.root, "remote-peer")
        self.assertEqual("authentication", authentication_failure["category"])

    def test_outbound_probe_classifies_offline_tcp_and_dns_failure(self):
        with self.app.app_context():
            self.store.save_peer("remote-peer", "Remote", "http://127.0.0.1:9", "unique-peer-token")
        with patch("app.federation_diagnostics.socket.create_connection", side_effect=ConnectionRefusedError("offline")):
            with self.app.app_context():
                tcp_failure = probe_peer(self.root, "remote-peer")
        self.assertEqual("tcp", tcp_failure["category"])
        self.assertEqual("failed", tcp_failure["detail"]["stages"]["tcp"])

        with patch(
            "app.federation_diagnostics.resolve_pinned_endpoint",
            side_effect=ValueError("peer endpoint cannot be resolved"),
        ):
            with self.app.app_context():
                dns_failure = probe_peer(self.root, "remote-peer")
        self.assertEqual("dns", dns_failure["category"])
        self.assertEqual("failed", dns_failure["detail"]["stages"]["dns"])

    def test_outbound_probe_keeps_tls_certificate_validation_enabled(self):
        with self.app.app_context():
            self.store.save_peer("remote-peer", "Remote", "https://127.0.0.1:443", "unique-peer-token")
        raw_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.side_effect = ssl.SSLCertVerificationError("certificate mismatch")
        with patch("app.federation_diagnostics.resolve_pinned_endpoint", return_value=(
            "https://127.0.0.1", urlsplit("https://127.0.0.1"), 443, [ipaddress.ip_address("127.0.0.1")],
        )), patch("app.federation_diagnostics.socket.create_connection", return_value=raw_socket), patch(
            "app.federation_diagnostics.ssl.create_default_context", return_value=context,
        ):
            with self.app.app_context():
                result = probe_peer(self.root, "remote-peer")
        self.assertEqual("tls_certificate", result["category"])
        self.assertEqual("certificate_failed", result["detail"]["stages"]["tls"])
        self.assertEqual(ssl.TLSVersion.TLSv1_2, context.minimum_version)
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="127.0.0.1")
        raw_socket.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
