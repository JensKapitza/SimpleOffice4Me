import socket
import unittest

from app.sftp_server import _create_listener


class SftpListenerTests(unittest.TestCase):
    def exchange(self, bind, host, family):
        with _create_listener(bind, 0, 4) as listener:
            self.assertEqual(family, listener.family)
            listener.settimeout(2)
            with socket.create_connection((host, listener.getsockname()[1]), timeout=2) as client:
                connection, _ = listener.accept()
                with connection:
                    connection.sendall(b"SSH-2.0-test\r\n")
                    self.assertEqual(b"SSH-2.0-test\r\n", client.recv(255))
            if family == socket.AF_INET6 and socket.has_dualstack_ipv6():
                self.assertEqual(1, listener.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY))

    def test_ipv4_loopback_and_existing_hostname_bind(self):
        for bind in ("127.0.0.1", "localhost"):
            with self.subTest(bind=bind):
                self.exchange(bind, "127.0.0.1", socket.AF_INET)

    def test_ipv6_loopback_and_unspecified_bind(self):
        if not socket.has_ipv6:
            self.skipTest("IPv6 unavailable on this platform")
        # Probe OS support independently so an implementation family bug fails
        # the test rather than being interpreted as an unavailable environment.
        try:
            with socket.socket(socket.AF_INET6) as probe:
                probe.bind(("::1", 0))
        except OSError as exc:
            self.skipTest(f"IPv6 loopback unavailable: {exc}")
        for bind in ("::1", "::"):
            with self.subTest(bind=bind):
                self.exchange(bind, "::1", socket.AF_INET6)
