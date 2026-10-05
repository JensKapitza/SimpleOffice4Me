"""Real HTTP transport tests for status, redirects, limits and deadlines."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from app import app
from app.litellm_config import validate
from app.security_controls import protect_value
from app.litellm_gateway import GatewayError, MAX_BYTES, request_gateway


class Handler(BaseHTTPRequestHandler):
    status = 200
    payload = b'{}'
    delay = 0
    calls = []

    def log_message(self, *args):
        return

    def do_GET(self):
        self.calls.append((self.path, self.headers.get('Authorization')))
        time.sleep(self.delay)
        try:
            self.send_response(self.status)
            self.send_header('Location', 'http://169.254.169.254/')
            self.send_header('Content-Length', str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):
        self.request_body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.do_GET()


class LiteLLMTransportTest(unittest.TestCase):
    def setUp(self):
        Handler.status, Handler.payload, Handler.delay, Handler.calls = 200, b'{}', 0, []
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        with app.app_context():
            self.config = validate({'mode': 'local', 'enabled': True, 'model': 'model',
                                    'provider_model': 'openai/test', 'port': self.server.server_port,
                                    'api_key_enc': protect_value('test-only-key', 'litellm-api_key'),
                                    'retries': 0, 'timeout': 1})
        self.context = app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()
        self.server.shutdown()
        self.server.server_close()

    def test_bearer_and_successful_completion_without_sdk_dependency(self):
        Handler.payload = json.dumps({'choices': [{'message': {'content': 'test reply'}}]}).encode()
        result = request_gateway('/v1/chat/completions', {'model': 'model', 'messages': []}, config=self.config)
        self.assertEqual('test reply', result['choices'][0]['message']['content'])
        self.assertEqual([('/v1/chat/completions', 'Bearer test-only-key')], Handler.calls)

    def test_auth_redirect_invalid_response_and_response_limit(self):
        for status, payload, code in [(401, b'{}', 'unauthorized'), (403, b'{}', 'unauthorized'),
                                      (302, b'{}', 'gateway_rejected'), (503, b'{}', 'temporarily_unavailable'),
                                      (400, b'{"error":{"type":"no_db_connection"}}', 'unauthorized'),
                                      (400, b'{"error":{"type":"bad_request_error"}}', 'gateway_rejected'),
                                      (200, b'not-json', 'invalid_response'), (200, b'[]', 'invalid_response'),
                                      (200, b'x' * (MAX_BYTES + 1), 'response_too_large')]:
            Handler.status, Handler.payload, Handler.calls = status, payload, []
            with self.subTest(code=code), self.assertRaisesRegex(GatewayError, '^' + code + '$'):
                request_gateway('/v1/models', config=self.config)
            self.assertEqual(1, len(Handler.calls))

    def test_timeout_and_closed_endpoint_are_controlled(self):
        Handler.delay = 1.5
        started = time.monotonic()
        with self.assertRaisesRegex(GatewayError, "^timeout$"):
            request_gateway('/v1/models', config=self.config)
        self.assertLess(time.monotonic() - started, 1.3)
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaisesRegex(GatewayError, 'unreachable'):
            request_gateway('/v1/models', config=self.config)

    def test_request_limit_before_network_payload(self):
        with self.assertRaisesRegex(GatewayError, 'request_too_large'):
            request_gateway('/v1/chat/completions', {'text': 'x' * MAX_BYTES}, config=self.config)
        self.assertEqual([], Handler.calls)

    def test_external_transport_pins_ip_and_verifies_original_tls_hostname(self):
        cfg = {**self.config, 'mode': 'external', 'base_url': 'https://gateway.example'}
        connection, sock, tls = MagicMock(), MagicMock(), MagicMock()
        tls.wrap_socket.return_value = sock
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{}'
        with patch('app.litellm_gateway._resolve', return_value=[(2, 1, 6, "", ("1.1.1.1", 443))]) as resolver, \
                patch('app.litellm_gateway.socket.socket', return_value=sock), \
                patch('app.litellm_gateway.ssl.create_default_context', return_value=tls) as context, \
                patch('app.litellm_gateway.http.client.HTTPConnection', return_value=connection):
            self.assertEqual({}, request_gateway('/v1/models', config=cfg))
        resolver.assert_called_once()
        sock.connect.assert_called_once_with(('1.1.1.1', 443))
        context.assert_called_once_with()
        tls.wrap_socket.assert_called_once_with(sock, server_hostname='gateway.example')
        import ssl
        self.assertEqual(ssl.TLSVersion.TLSv1_2, tls.minimum_version)

    def test_connect_failover_uses_only_validated_addresses_before_post(self):
        from app import litellm_gateway as gateway
        cfg = {**self.config, 'mode': 'external', 'base_url': 'https://gateway.example'}
        connection, unreachable, working, tls = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        unreachable.connect.side_effect = OSError('IPv6 unavailable')
        tls.wrap_socket.return_value = working
        response = connection.getresponse.return_value
        response.status, response.read.return_value = 200, b'{}'
        answers = [(10, 1, 6, '', ('2606:4700:4700::1111', 443, 0, 0)), (2, 1, 6, '', ('1.1.1.1', 443))]
        with patch.object(gateway, '_resolve', return_value=answers) as resolver, \
                patch.object(gateway.socket, 'socket', side_effect=[unreachable, working]), \
                patch.object(gateway.ssl, 'create_default_context', return_value=tls), \
                patch.object(gateway.http.client, 'HTTPConnection', return_value=connection):
            self.assertEqual({}, request_gateway('/v1/chat/completions', {'model': 'model'}, config=cfg))
        resolver.assert_called_once()
        unreachable.connect.assert_called_once_with(answers[0][4])
        unreachable.close.assert_called()
        working.connect.assert_called_once_with(answers[1][4])
        connection.request.assert_called_once()

    def test_no_address_failover_after_request_or_tls_validation_failure(self):
        from app import litellm_gateway as gateway
        import ssl
        cfg = {**self.config, 'mode': 'external', 'base_url': 'https://gateway.example'}
        for error, stage in [(OSError('response lost'), 'response'), (ssl.SSLCertVerificationError('certificate'), 'tls')]:
            connection, sock, tls = MagicMock(), MagicMock(), MagicMock()
            tls.wrap_socket.return_value = sock
            if stage == 'tls':
                tls.wrap_socket.side_effect = error
            else:
                connection.getresponse.side_effect = error
            with self.subTest(stage=stage), \
                    patch.object(gateway, 'addresses', return_value=[(2, ('1.1.1.1', 443)), (2, ('1.0.0.1', 443))]), \
                    patch.object(gateway.socket, 'socket', return_value=sock) as sockets, \
                    patch.object(gateway.ssl, 'create_default_context', return_value=tls), \
                    patch.object(gateway.http.client, 'HTTPConnection', return_value=connection):
                with self.assertRaisesRegex(GatewayError, 'unreachable'):
                    request_gateway('/v1/chat/completions', {'model': 'model'}, config=cfg)
                sockets.assert_called_once()
                self.assertEqual(0 if stage == 'tls' else 1, connection.request.call_count)

    def test_slow_dns_is_bounded_and_resolver_capacity_is_finite(self):
        from app import litellm_gateway as gateway
        blocked = threading.Event()
        def slow_lookup(*args, **kwargs):
            blocked.wait(1)
            return [(2, 1, 6, '', ('1.1.1.1', 443))]
        started = time.monotonic()
        try:
            with patch.object(gateway, '_RESOLVERS', threading.BoundedSemaphore(4)), \
                    patch.object(gateway.socket, 'getaddrinfo', side_effect=slow_lookup):
                for _ in range(4):
                    with self.assertRaisesRegex(GatewayError, 'timeout'):
                        gateway._resolve('gateway.example', 443, .01)
                with self.assertRaisesRegex(GatewayError, 'resolver_busy'):
                    gateway._resolve('gateway.example', 443, .01)
        finally:
            blocked.set()
        self.assertLess(time.monotonic() - started, .3)

    def test_litellm_liveness_json_string_is_accepted_only_for_liveness(self):
        Handler.payload = json.dumps("I'm alive!").encode()
        self.assertEqual({'status': 'alive'}, request_gateway('/health/liveliness', config=self.config))
        with self.assertRaisesRegex(GatewayError, 'invalid_response'):
            request_gateway('/v1/models', config=self.config)
