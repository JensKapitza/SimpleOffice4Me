"""Coverage for LAN proxy configuration, trust boundaries and HTTPS forwarding."""

import ipaddress
import json
import os
import socket
import tempfile
import sys
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, jsonify, make_response, request
from werkzeug.serving import make_server

from app.lan_proxy import (
    LanProxyHeadersMiddleware,
    LanProxyRuntime,
    _tls_context,
    _secure_response_cookies,
)
from app.lan_proxy_config import (
    DEFAULT_LAN_PROXY_SETTINGS,
    normalize_lan_proxy_settings,
    validate_lan_proxy_settings,
)


class LanProxyConfigTests(unittest.TestCase):
    def test_defaults_are_disabled_and_loopback_only(self):
        self.assertFalse(DEFAULT_LAN_PROXY_SETTINGS["enabled"])
        self.assertEqual("127.0.0.1", DEFAULT_LAN_PROXY_SETTINGS["bind_host"])

    def test_tls_file_names_must_be_safe_and_paired_when_enabled(self):
        with self.assertRaises(ValueError):
            validate_lan_proxy_settings({"enabled": True, "tls_cert": "cert.pem", "tls_key": "../key.pem"})
        with self.assertRaises(ValueError):
            validate_lan_proxy_settings({"enabled": True, "tls_cert": "/tmp/cert.pem", "tls_key": "key.pem"})
        with self.assertRaises(ValueError):
            validate_lan_proxy_settings({"enabled": True, "tls_cert": "cert.pem"})

    def test_legacy_absolute_tls_paths_are_preserved_or_migrated_within_tls_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            tls_directory = Path(temporary) / "tls"
            tls_directory.mkdir()
            certificate = tls_directory / "certs" / "fullchain.pem"
            certificate.parent.mkdir()
            key = tls_directory / "privkey.pem"
            with patch.dict(os.environ, {"SIMPLEOFFICE_LAN_PROXY_TLS_DIR": str(tls_directory)}):
                migrated = normalize_lan_proxy_settings({"tls_cert": str(certificate), "tls_key": str(key)})
                self.assertEqual("certs/fullchain.pem", migrated["tls_cert"])
                self.assertEqual("privkey.pem", migrated["tls_key"])
                self.assertFalse(migrated["legacy_tls_paths"])

                legacy = normalize_lan_proxy_settings(
                    {"tls_cert": str(Path(temporary) / "outside.pem"), "tls_key": str(key)}
                )
                self.assertTrue(legacy["legacy_tls_paths"])
                self.assertEqual(str(Path(temporary) / "outside.pem"), legacy["tls_cert"])
                with self.assertRaisesRegex(ValueError, "relativer Dateipfad"):
                    validate_lan_proxy_settings(
                        {
                            "enabled": True,
                            "tls_cert": legacy["tls_cert"],
                            "tls_key": legacy["tls_key"],
                        }
                    )

    def test_wildcard_is_allowed_only_for_explicit_container_mode(self):
        settings = {**DEFAULT_LAN_PROXY_SETTINGS, "bind_host": "0.0.0.0"}
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            validate_lan_proxy_settings(settings)
        with patch.dict(os.environ, {"SIMPLEOFFICE_LAN_PROXY_CONTAINER_MODE": "0"}):
            with self.assertRaises(ValueError):
                validate_lan_proxy_settings(settings)
        with patch.dict(os.environ, {"SIMPLEOFFICE_LAN_PROXY_CONTAINER_MODE": "1"}):
            self.assertEqual("0.0.0.0", validate_lan_proxy_settings(settings)["bind_host"])

    def test_wildcard_certificate_uses_existing_container_public_host(self):
        settings = {**DEFAULT_LAN_PROXY_SETTINGS, "enabled": True, "bind_host": "0.0.0.0"}
        with patch.dict(os.environ, {
            "SIMPLEOFFICE_LAN_PROXY_CONTAINER_MODE": "1",
            "SIMPLEOFFICE_LAN_PROXY_PUBLIC_HOST": "office.example.test",
            "SIMPLEOFFICE_LAN_PROXY_PUBLIC_PORT": "9443",
        }):
            config = validate_lan_proxy_settings(settings)
            self.assertEqual("office.example.test", LanProxyRuntime._certificate_host(config))
            self.assertEqual("office.example.test:9443", LanProxyRuntime._public_host(config))
        with patch.dict(os.environ, {"SIMPLEOFFICE_LAN_PROXY_CONTAINER_MODE": "1"}, clear=True):
            config = validate_lan_proxy_settings(settings)
            with self.assertRaisesRegex(ValueError, "PUBLIC_HOST"):
                LanProxyRuntime._certificate_host(config)

    def test_public_or_multicast_bind_addresses_are_rejected(self):
        for address in ("8.8.8.8", "224.0.0.1", "not-an-ip"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                validate_lan_proxy_settings({**DEFAULT_LAN_PROXY_SETTINGS, "bind_host": address})

    def test_https_proxy_marks_cookies_secure_without_duplicate_attribute(self):
        cookies = _secure_response_cookies(
            [
                ("Set-Cookie", "session=value; Path=/; HttpOnly"),
                ("Set-Cookie", "other=value; Secure; SameSite=Lax"),
            ]
        )
        self.assertEqual("session=value; Path=/; HttpOnly; Secure", cookies[0][1])
        self.assertEqual("other=value; Secure; SameSite=Lax", cookies[1][1])


class LanProxyHeadersTests(unittest.TestCase):
    def make_environ(self, **values):
        environ = {
            "REMOTE_ADDR": "127.0.0.1",
            "wsgi.url_scheme": "http",
            "HTTP_HOST": "backend.local",
            "HTTP_X_FORWARDED_FOR": "192.168.1.42",
            "HTTP_X_FORWARDED_HOST": "office.example.test:8443",
            "HTTP_X_FORWARDED_PROTO": "https",
            "HTTP_X_SIMPLEOFFICE_LAN_PROXY": "secret-token",
        }
        environ.update(values)
        return environ

    def call(self, environ):
        captured = {}

        def application(values, start_response):
            captured.update(values)
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ok"]

        middleware = LanProxyHeadersMiddleware(
            type("App", (), {"config": {"LAN_PROXY_TOKEN": "secret-token"}})(),
            application,
        )
        with patch.object(middleware, "_ensure_started"):
            middleware(environ, lambda *_args: None)
        return captured

    def test_valid_token_trusts_private_client_and_https_origin(self):
        environ = self.make_environ()
        captured = self.call(environ)
        self.assertEqual("https", captured["wsgi.url_scheme"])
        self.assertEqual("office.example.test:8443", captured["HTTP_HOST"])
        self.assertEqual("192.168.1.42", captured["REMOTE_ADDR"])
        self.assertNotIn("HTTP_X_SIMPLEOFFICE_LAN_PROXY", captured)
        self.assertNotIn("HTTP_X_FORWARDED_FOR", captured)

    def test_spoofed_token_from_non_loopback_does_not_trust_forwarded_headers(self):
        captured = self.call(self.make_environ(REMOTE_ADDR="192.168.1.99"))
        self.assertEqual("http", captured["wsgi.url_scheme"])
        self.assertEqual("backend.local", captured["HTTP_HOST"])
        self.assertEqual("192.168.1.99", captured["REMOTE_ADDR"])
        self.assertNotIn("HTTP_X_SIMPLEOFFICE_LAN_PROXY", captured)

    def test_public_or_malformed_forwarded_client_is_not_trusted(self):
        for address in ("8.8.8.8", "malformed"):
            with self.subTest(address=address):
                captured = self.call(self.make_environ(HTTP_X_FORWARDED_FOR=address))
                self.assertEqual("http", captured["wsgi.url_scheme"])
                self.assertEqual("backend.local", captured["HTTP_HOST"])


class LanProxyStartupTests(unittest.TestCase):
    def test_any_wsgi_entrypoint_autostarts_saved_proxy_once_on_first_request(self):
        from app.settings_store import SettingsStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "documents"
            root.mkdir()
            SettingsStore(root).save(
                {
                    "lan_proxy": {
                        "enabled": True,
                        "bind_host": "192.168.1.42",
                        "port": 8443,
                        "tls_cert": "certificate.pem",
                        "tls_key": "private-key.pem",
                    }
                },
                "test",
            )
            flask_app = Flask("autostart")
            flask_app.config.update(DOCUMENT_ROOT=str(root), MAX_CONTENT_LENGTH=1024)
            wrapped = LanProxyHeadersMiddleware(flask_app, lambda environ, start_response: [])
            manager = type("Manager", (), {"start": Mock(return_value={"state": "running", "message": ""})})()
            environ = {"REMOTE_ADDR": "127.0.0.1", "SERVER_PORT": "43871"}
            with patch("app.lan_proxy.lan_proxy_runtime", manager):
                wrapped(dict(environ), lambda *_args: None)
                wrapped(dict(environ), lambda *_args: None)
            manager.start.assert_called_once()
            self.assertEqual(43871, manager.start.call_args.kwargs["backend_port"])


class LanProxyHttpsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.documents = self.root / "documents"
        self.documents.mkdir()
        self.tls_directory = self.root / "tls"
        self.tls_directory.mkdir()
        self.tls_directory_environment = patch.dict(
            os.environ,
            {"SIMPLEOFFICE_LAN_PROXY_TLS_DIR": str(self.tls_directory)},
        )
        self.tls_directory_environment.start()
        self.addCleanup(self.tls_directory_environment.stop)
        self.certificate, self.private_key = self._make_certificate()
        self.backend = Flask("lan-proxy-backend")

        @self.backend.post("/probe")
        def probe():
            payload = request.get_data(as_text=True)
            result = {
                "scheme": request.scheme,
                "host": request.host,
                "remote": request.remote_addr,
                "payload": payload,
                "forwarded_header": request.headers.get("X-Forwarded-For"),
            }
            response = make_response(jsonify(result))
            response.set_cookie("session", "synthetic", httponly=True)
            response.headers["X-Forwarded-For"] = "spoofed-backend-value"
            return response

        self.backend.config["LAN_PROXY_TOKEN"] = ""
        backend_app = LanProxyHeadersMiddleware(self.backend, self.backend.wsgi_app)
        self.backend_server = make_server("127.0.0.1", 0, backend_app, threaded=True)
        self.backend_thread = threading.Thread(target=self.backend_server.serve_forever, daemon=True)
        self.backend_thread.start()
        self.addCleanup(self._stop_backend)

    def _make_certificate(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime
        import ipaddress

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False
            )
            .sign(key, hashes.SHA256())
        )
        cert_path = self.tls_directory / "test-cert.pem"
        key_path = self.tls_directory / "test-key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        key_path.chmod(0o600)
        return cert_path, key_path

    def _stop_backend(self):
        self.backend_server.shutdown()
        self.backend_server.server_close()
        self.backend_thread.join(timeout=3)

    def _start_proxy(
        self,
        max_body_bytes=1024,
        stdlib_fallback=False,
        *,
        auto_tls=False,
        bind_host="127.0.0.1",
    ):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            proxy_port = listener.getsockname()[1]
        self.backend.config.update(
            DOCUMENT_ROOT=str(self.documents),
            MAX_CONTENT_LENGTH=max_body_bytes,
            SESSION_COOKIE_SECURE=False,
        )
        runtime = LanProxyRuntime()
        absent_aiohttp = patch.dict(sys.modules, {"aiohttp": None}) if stdlib_fallback else nullcontext()
        container_env = (
            patch.dict(os.environ, {
                "SIMPLEOFFICE_LAN_PROXY_CONTAINER_MODE": "1",
                "SIMPLEOFFICE_LAN_PROXY_PUBLIC_HOST": "127.0.0.1",
                "SIMPLEOFFICE_LAN_PROXY_PUBLIC_PORT": str(proxy_port),
            })
            if bind_host == "0.0.0.0" else nullcontext()
        )
        with absent_aiohttp, container_env:
            result = runtime.start(
                self.backend,
                {
                    "enabled": True,
                    "bind_host": bind_host,
                    "port": proxy_port,
                    "tls_cert": "" if auto_tls else self.certificate.name,
                    "tls_key": "" if auto_tls else self.private_key.name,
                },
                backend_host="127.0.0.1",
                backend_port=self.backend_server.server_port,
                document_root=self.documents,
            )
        self.assertEqual("running", result["state"], result["message"])
        self.addCleanup(runtime.stop)
        return runtime, proxy_port

    def test_auto_tls_starts_for_container_wildcard_and_is_trusted_by_configured_host(self):
        from http.client import HTTPSConnection
        import ssl
        from cryptography import x509

        _runtime, proxy_port = self._start_proxy(auto_tls=True, bind_host="0.0.0.0")
        certificate_path = self.tls_directory / "lan-proxy-self-signed.crt"
        certificate = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        self.assertIn(
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value,
        )
        context = ssl.create_default_context(cafile=str(certificate_path))
        connection = HTTPSConnection("127.0.0.1", proxy_port, context=context, timeout=5)
        self.addCleanup(connection.close)
        connection.request("POST", "/probe?source=generated", body="trusted")
        response = connection.getresponse()
        body = json.loads(response.read())
        self.assertEqual(200, response.status)
        self.assertEqual("trusted", body["payload"])

    def test_https_request_is_forwarded_with_trusted_origin_and_secure_cookie(self):
        from http.client import HTTPSConnection
        import ssl

        _runtime, proxy_port = self._start_proxy()

        connection = HTTPSConnection(
            "127.0.0.1",
            proxy_port,
            context=ssl._create_unverified_context(),
            timeout=5,
        )
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/probe?source=synthetic",
            body="hello",
            headers={"X-Forwarded-For": "8.8.8.8", "X-Forwarded-Host": "evil.example"},
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        self.assertEqual(200, response.status)
        self.assertEqual("https", body["scheme"])
        self.assertEqual(f"127.0.0.1:{proxy_port}", body["host"])
        self.assertEqual("127.0.0.1", body["remote"])
        self.assertEqual("hello", body["payload"])
        self.assertIsNone(body["forwarded_header"])
        self.assertIsNone(response.getheader("X-Forwarded-For"))
        self.assertIn("Secure", response.getheader("Set-Cookie", ""))

    def test_tls_symlink_cannot_escape_the_server_owned_directory(self):
        external_key = self.root / "external-key.pem"
        external_key.write_bytes(self.private_key.read_bytes())
        external_key.chmod(0o600)
        escaped_key = self.tls_directory / "external.key"
        escaped_key.symlink_to(external_key)
        with self.assertRaisesRegex(ValueError, "freigegebenen TLS-Ordner"):
            _tls_context(self.certificate.name, escaped_key.name, self.documents, self.tls_directory)

    def test_tls_private_key_must_not_be_group_or_other_readable(self):
        if os.name != "posix":
            self.skipTest("POSIX private-key permission bits are unavailable")
        import ssl

        self.private_key.chmod(0o640)
        with self.assertRaisesRegex(ValueError, "zu weit freigegeben"):
            _tls_context(self.certificate.name, self.private_key.name, self.documents, self.tls_directory)
        self.private_key.chmod(0o600)
        context = _tls_context(self.certificate.name, self.private_key.name, self.documents, self.tls_directory)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_tls_directory_must_not_be_group_or_other_writable(self):
        if os.name != "posix":
            self.skipTest("POSIX directory permission bits are unavailable")
        self.tls_directory.chmod(0o777)
        self.addCleanup(self.tls_directory.chmod, 0o700)
        with self.assertRaisesRegex(ValueError, "TLS-Ordner darf"):
            _tls_context(self.certificate.name, self.private_key.name, self.documents, self.tls_directory)

    def test_chunked_upload_over_limit_is_rejected(self):
        from http.client import HTTPSConnection
        import ssl

        _runtime, proxy_port = self._start_proxy(max_body_bytes=1024)
        connection = HTTPSConnection(
            "127.0.0.1",
            proxy_port,
            context=ssl._create_unverified_context(),
            timeout=5,
        )
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/probe",
            body=[b"a" * 600, b"b" * 600],
        )
        response = connection.getresponse()
        response.read()
        self.assertEqual(413, response.status)

    def test_standard_library_fallback_forwards_https_without_aiohttp(self):
        from http.client import HTTPSConnection
        import ssl

        _runtime, proxy_port = self._start_proxy(stdlib_fallback=True)
        connection = HTTPSConnection(
            "127.0.0.1",
            proxy_port,
            context=ssl._create_unverified_context(),
            timeout=5,
        )
        self.addCleanup(connection.close)
        connection.request(
            "POST",
            "/probe?source=stdlib",
            body="offline",
            headers={"X-Forwarded-For": "8.8.8.8"},
        )
        response = connection.getresponse()
        body = json.loads(response.read())
        self.assertEqual(200, response.status)
        self.assertEqual("https", body["scheme"])
        self.assertEqual("offline", body["payload"])
        self.assertIsNone(body["forwarded_header"])
        self.assertIsNone(response.getheader("X-Forwarded-For"))
        self.assertIn("Secure", response.getheader("Set-Cookie", ""))

    def test_port_conflict_is_reported_without_disturbing_first_listener(self):
        from http.client import HTTPSConnection
        import ssl

        first, proxy_port = self._start_proxy()
        second = LanProxyRuntime()
        conflict = second.start(
            self.backend,
            {
                "enabled": True,
                "bind_host": "127.0.0.1",
                "port": proxy_port,
                "tls_cert": self.certificate.name,
                "tls_key": self.private_key.name,
            },
            backend_host="127.0.0.1",
            backend_port=self.backend_server.server_port,
            document_root=self.documents,
        )
        self.addCleanup(second.stop)
        self.assertEqual("error", conflict["state"])
        self.assertIn("already in use", conflict["message"])
        self.assertEqual("running", first.status()["state"])


if __name__ == "__main__":
    unittest.main()
