"""Public gateway requests against a real TLS endpoint, with synthetic DNS/routes."""
import datetime
import json
import os
import socket
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app import app
from app.litellm_config import prepare, persist
from app.litellm_gateway import GatewayError, completion, request_gateway


class LiteLLMExternalTransportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'gateway.example')])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(1).not_valid_before(datetime.datetime(2020, 1, 1))
                .not_valid_after(datetime.datetime(2040, 1, 1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                    key_encipherment=True, data_encipherment=False, key_agreement=False,
                    key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('gateway.example')]), critical=False)
                .sign(key, hashes.SHA256()))
        cert_path = self.root / 'ca.pem'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path = self.root / 'key.pem'
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        key_path.chmod(0o600)
        self.requests = []
        recorded = self.requests

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                recorded.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                payload = b'{"choices":[{"message":{"content":"independent TLS reply"}}]}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'{}')

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(cert_path, key_path)
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = patch.dict(os.environ, {'SSL_CERT_FILE': str(cert_path),
                                          'SIMPLEOFFICE_MINI_SERVICES_CONFIG': str(self.root / 'mini-services.json')})
        self.env.start()
        self.context = app.app_context()
        self.context.push()
        persist(prepare({'enabled': True, 'mode': 'external', 'base_url': 'https://gateway.example:' + str(self.server.server_port),
                         'model': 'fixture-model', 'api_key': 'synthetic-tls-key', 'timeout': 2, 'retries': 0}))

    def tearDown(self):
        self.context.pop()
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def route(self, *, first_unreachable=False):
        """Replace only OS DNS/connect boundary, retaining actual HTTP and verified TLS."""
        port = self.server.server_port
        real_socket = socket.socket
        attempted = []

        class RoutedSocket(real_socket):
            def connect(self, address):
                attempted.append(address[0])
                if address[0] == '1.1.1.1' and first_unreachable:
                    raise ConnectionRefusedError('synthetic unavailable first route')
                return super().connect(('127.0.0.1', port))

        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', port))]
        if first_unreachable:
            addresses.append((socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', port)))
        return patch('socket.getaddrinfo', return_value=addresses), patch('socket.socket', RoutedSocket), attempted

    def test_completion_uses_second_validated_address_without_repeating_model_post(self):
        """An unreachable first DNS answer must not prevent the one observable model request."""
        dns, routes, attempted = self.route(first_unreachable=True)
        with dns, routes:
            result = completion([{'role': 'user', 'content': 'synthetic question'}], max_tokens=12)
        self.assertEqual('independent TLS reply', result['choices'][0]['message']['content'])
        self.assertEqual(['1.1.1.1', '8.8.8.8'], attempted)
        self.assertEqual([('/v1/chat/completions', {'model': 'fixture-model', 'messages': [
            {'role': 'user', 'content': 'synthetic question'}], 'max_tokens': 12, 'stream': False})], self.requests)

    def test_certificate_for_different_hostname_is_rejected_before_http_request(self):
        """Trusting the test CA never permits a certificate with the wrong gateway hostname."""
        persist(prepare({'base_url': 'https://wrong.example:' + str(self.server.server_port), 'retries': 2}))
        dns, routes, attempted = self.route()
        with dns, routes, self.assertRaisesRegex(GatewayError, '^unreachable$'):
            request_gateway('/v1/models')
        self.assertEqual([], self.requests)
        self.assertEqual(['1.1.1.1'], attempted)
