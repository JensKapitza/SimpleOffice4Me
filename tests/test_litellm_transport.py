"""Real HTTP transport tests for status, redirects, limits and deadlines."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

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
                                      (200, b'not-json', 'invalid_response'), (200, b'[]', 'invalid_response'),
                                      (200, b'x' * (MAX_BYTES + 1), 'response_too_large')]:
            Handler.status, Handler.payload, Handler.calls = status, payload, []
            with self.subTest(code=code), self.assertRaisesRegex(GatewayError, '^' + code + '$'):
                request_gateway('/v1/models', config=self.config)
            self.assertEqual(1, len(Handler.calls))

    def test_timeout_and_closed_endpoint_are_controlled(self):
        Handler.delay = 1.5
        started = time.monotonic()
        with self.assertRaises(GatewayError):
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
