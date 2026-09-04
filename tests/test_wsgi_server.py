import os
import socket
import unittest
from unittest.mock import patch

from tools.launcher import endpoint_available, running_web_pid, waitress_options


class WsgiServerSettingsTest(unittest.TestCase):
    def test_secure_local_defaults_match_application_upload_limit(self):
        with patch.dict(os.environ, {}, clear=True):
            options = waitress_options({"host": "127.0.0.1", "port": 8080}, 512 * 1024 * 1024)

        self.assertEqual("127.0.0.1", options["host"])
        self.assertEqual(8080, options["port"])
        self.assertEqual(4, options["threads"])
        self.assertEqual(120, options["channel_timeout"])
        self.assertEqual(512 * 1024 * 1024, options["max_request_body_size"])
        self.assertFalse(options["expose_tracebacks"])
        self.assertNotIn("clear_untrusted_proxy_headers", options)

    def test_environment_overrides_are_bounded(self):
        environment = {
            "SIMPLEOFFICE_HOST": "0.0.0.0",
            "SIMPLEOFFICE_PORT": "9090",
            "SIMPLEOFFICE_WSGI_THREADS": "8",
            "SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT": "300",
            "SIMPLEOFFICE_TRUSTED_PROXY_HOPS": "1",
        }
        with patch.dict(os.environ, environment, clear=True):
            options = waitress_options({"host": "127.0.0.1", "port": 8080}, 1024)

        self.assertEqual("0.0.0.0", options["host"])
        self.assertEqual(9090, options["port"])
        self.assertEqual(8, options["threads"])
        self.assertEqual(300, options["channel_timeout"])
        self.assertFalse(options["clear_untrusted_proxy_headers"])

    def test_invalid_values_stop_instead_of_starting_with_surprises(self):
        for name, value in (
            ("SIMPLEOFFICE_PORT", "0"),
            ("SIMPLEOFFICE_WSGI_THREADS", "many"),
            ("SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT", "5"),
            ("SIMPLEOFFICE_TRUSTED_PROXY_HOPS", "17"),
        ):
            with self.subTest(name=name), patch.dict(os.environ, {name: value}, clear=True):
                with self.assertRaises(RuntimeError):
                    waitress_options({"host": "127.0.0.1", "port": 8080}, 1024)

    def test_endpoint_probe_detects_free_and_occupied_local_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            host, port = listener.getsockname()
            listener.listen(1)
            self.assertFalse(endpoint_available(host, port))

        self.assertTrue(endpoint_available(host, port))

    def test_running_web_pid_returns_live_registered_service(self):
        record = {"pid": 1234, "marker": "launcher.py"}
        with patch("tools.service_control.read", return_value=record), patch(
            "tools.service_control.process_matches", return_value=True
        ), patch("tools.service_control.unregister") as unregister:
            self.assertEqual(1234, running_web_pid())
            unregister.assert_not_called()

    def test_running_web_pid_removes_stale_record(self):
        record = {"pid": 1234, "marker": "launcher.py"}
        with patch("tools.service_control.read", return_value=record), patch(
            "tools.service_control.process_matches", return_value=False
        ), patch("tools.service_control.unregister") as unregister:
            self.assertIsNone(running_web_pid())
            unregister.assert_called_once_with("web", 1234)


if __name__ == "__main__":
    unittest.main()
