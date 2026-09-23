"""Real loopback lifecycle tests; no LAN hardware or privileged ports."""
import copy
import errno
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import simpleoffice_mini_core as core
from simpleoffice_mini_runtime import DhcpService, DnsService, detect_foreign_dhcp_servers
from simpleoffice_network_boot import DEFAULT_BOOT_SETTINGS, TftpService
from simpleoffice_service_lifecycle import BoundedTasks, ServiceState, error_detail, service_health
from simpleoffice_sip_runtime import SipRegistrarService
from tools.mini_services import Worker


class ConfigStateTests(unittest.TestCase):
    def test_project_root_and_legacy_config(self):
        self.assertEqual(Path(core.__file__).resolve().parent, core.project_root())
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            root = Path(temp) / "repo"
            root.mkdir()
            with patch.object(core, "project_root", return_value=root):
                self.assertEqual(root / "instance/mini-services.json", core.default_config_path())
                legacy = Path(temp) / "instance/mini-services.json"
                core.save_config(core.DEFAULT_CONFIG, legacy)
                self.assertEqual(legacy, core.default_config_path())
                core.save_config(core.DEFAULT_CONFIG, root / "instance/mini-services.json")
                self.assertNotEqual(legacy, core.default_config_path())

    def test_invalid_json_is_not_silently_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            for raw in ("broken", "[]", "null"):
                path.write_text(raw)
                with self.assertRaises(ValueError):
                    core.load_config(path)

    def test_atomic_write_does_not_follow_predictable_temporary_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            outside = Path(temp) / "outside"
            outside.write_bytes(b"unchanged")
            path.with_name(path.name + ".tmp").symlink_to(outside)
            core.save_config(core.DEFAULT_CONFIG, path)
            self.assertEqual(b"unchanged", outside.read_bytes())
            if os.name == "posix":
                self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_stale_status_is_not_running(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            core.write_status({"state": "running", "dns_running": True,
                               "services": {"dns": {"state": "running"}}}, path)
            self.assertFalse(core.read_status(path)["stale"])
            with patch.object(core.time, "time", return_value=time.time() + 100):
                status = core.read_status(path)
            self.assertEqual("unavailable", status["state"])
            self.assertFalse(status["dns_running"])
            self.assertFalse(status["services"]["dns"]["health"]["ok"])

    def test_error_has_no_exception_secrets_and_retries_end(self):
        error = error_detail(OSError(errno.EADDRINUSE, "token=secret"))
        self.assertEqual("address_in_use", error["code"])
        self.assertNotIn("secret", json.dumps(error))
        state = ServiceState("dns", "DNS")
        for _ in range(6):
            state.failed(RuntimeError("secret"))
        self.assertIsNone(state.retry_at)


class SocketLifecycleTests(unittest.TestCase):
    def test_all_datagram_services_are_idempotent_and_restartable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            dhcp = DhcpService({**core.DEFAULT_CONFIG["dhcp"], "port": 0}, path)
            tftp = TftpService(DEFAULT_BOOT_SETTINGS, path)
            tftp.settings["tftp_port"] = 0
            with patch("simpleoffice_sip_runtime.auto_sip_bind_host", return_value="127.0.0.1"):
                sip = SipRegistrarService(path)
            sip.settings["registrar_port"] = 0
            for service in (dhcp, tftp, sip):
                with self.subTest(service=type(service).__name__):
                    try:
                        service.stop()
                        service.start()
                        thread = service.thread
                        service.start()
                        self.assertIs(thread, service.thread)
                        self.assertTrue(service_health(service))
                        service.stop()
                        service.stop()
                        self.assertFalse(service_health(service))
                        service.start()
                        self.assertTrue(service_health(service))
                    finally:
                        service.stop()

    def test_udp_conflict_does_not_leak_listener(self):
        with tempfile.TemporaryDirectory() as temp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy:
            busy.bind(("127.0.0.1", 0))
            service = DhcpService({**core.DEFAULT_CONFIG["dhcp"], "port": busy.getsockname()[1]}, Path(temp) / "config.json")
            with self.assertRaises(OSError):
                service.start()
            self.assertIsNone(service.socket)
            service.stop()

    def test_dns_tcp_bind_failure_closes_udp_socket(self):
        with tempfile.TemporaryDirectory() as temp, socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            service = DnsService({**core.DEFAULT_CONFIG["dns"], "port": port}, Path(temp) / "config.json")
            with self.assertRaises(OSError):
                service.start()
            self.assertEqual([], service.sockets)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.bind(("127.0.0.1", port))

    def test_dns_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            service = DnsService({**core.DEFAULT_CONFIG["dns"], "port": 0}, Path(temp) / "config.json")
            try:
                service.start()
                listeners = list(service.threads)
                service.start()
                self.assertEqual(listeners, service.threads)
                service.stop()
                service.start()
                self.assertTrue(service_health(service))
            finally:
                service.stop()

    def test_request_concurrency_is_bounded(self):
        tasks = BoundedTasks(1)
        release = threading.Event()
        try:
            self.assertTrue(tasks.submit(release.wait))
            self.assertFalse(tasks.submit(lambda: None))
        finally:
            release.set()
            tasks.stop()
        tasks.reset()


class DhcpConflictProbeTests(unittest.TestCase):
    def test_probe_detects_offer_without_accepting_a_lease(self):
        class ProbeSocket:
            def __init__(self):
                self.sent = None
                self.responses = 0

            def setsockopt(self, *_args):
                return None

            def bind(self, address):
                self.bound = address

            def settimeout(self, _timeout):
                return None

            def sendto(self, packet, destination):
                self.sent = packet
                self.destination = destination

            def recvfrom(self, _size):
                if self.responses:
                    raise socket.timeout()
                self.responses += 1
                xid = core.DHCP_HEADER.unpack_from(self.sent)[4]
                server = socket.inet_aton("192.168.50.1")
                header = core.DHCP_HEADER.pack(
                    2, 1, 6, 0, xid, 0, 0x8000,
                    b"\0" * 4, socket.inet_aton("192.168.50.100"),
                    b"\0" * 4, b"\0" * 4, b"\0" * 16, b"", b"",
                )
                payload = (
                    header + core.DHCP_MAGIC
                    + bytes((53, 1, core.DHCP_OFFER, 54, 4))
                    + server + bytes((255,))
                )
                return payload, ("192.168.50.1", 67)

            def close(self):
                return None

        config = {
            **core.DEFAULT_CONFIG["dhcp"],
            "bind": "192.168.50.2",
            "server_ip": "192.168.50.2",
            "network": "192.168.50.0/24",
            "pool_start": "192.168.50.20",
            "pool_end": "192.168.50.200",
        }
        fake = ProbeSocket()
        with patch("simpleoffice_mini_runtime.socket.socket", return_value=fake):
            servers = detect_foreign_dhcp_servers(config, timeout=0.1)

        self.assertEqual(["192.168.50.1"], servers)
        self.assertEqual(("192.168.50.2", 68), fake.bound)
        self.assertEqual(("255.255.255.255", 67), fake.destination)



class WorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = Worker(Path(self.temp.name) / "config.json")
        self.addCleanup(self.worker.stop)
        self.config = copy.deepcopy(core.DEFAULT_CONFIG)
        self.config["dhcp"]["enabled"] = self.config["dns"]["enabled"] = True
        core.save_config(self.config, self.worker.config_path)

    def test_start_failure_is_isolated_and_retry_budget_is_bounded(self):
        dns = MagicMock()
        with patch("tools.mini_services.DnsService", return_value=dns), patch(
            "tools.mini_services.BootAwareDhcpService", side_effect=PermissionError("secret")
        ), patch("tools.mini_services.SipRegistrarService"), patch("tools.mini_services.disable_gateway") as disable:
            self.worker._load_network_services()
            self.assertEqual("failed", self.worker.states["dhcp"].state)
            self.assertEqual("running", self.worker.states["dns"].state)
            dns.stop.assert_not_called()
            disable.assert_not_called()
            for _ in range(5):
                self.worker._start_one("dhcp")
            self.assertIsNone(self.worker.states["dhcp"].retry_at)

    def test_foreign_dhcp_server_blocks_our_listener(self):
        settings = {
            **copy.deepcopy(core.DEFAULT_CONFIG["dhcp"]),
            "enabled": True,
            "bind": "192.168.50.2",
            "server_ip": "192.168.50.2",
            "network": "192.168.50.0/24",
            "pool_start": "192.168.50.20",
            "pool_end": "192.168.50.200",
        }
        self.worker.desired["dhcp"] = (True, settings)
        with patch.object(self.worker, "_network_available", return_value=True), patch(
            "tools.mini_services.detect_foreign_dhcp_servers", return_value=["192.168.50.1"]
        ), patch("tools.mini_services.BootAwareDhcpService") as factory:
            self.worker._start_one("dhcp")

        factory.assert_not_called()
        self.assertEqual("failed", self.worker.states["dhcp"].state)
        self.assertEqual("dhcp_conflict", self.worker.states["dhcp"].last_error["code"])

    def test_unavailable_dhcp_probe_fails_closed(self):
        settings = {
            **copy.deepcopy(core.DEFAULT_CONFIG["dhcp"]),
            "enabled": True,
            "bind": "192.168.50.2",
            "server_ip": "192.168.50.2",
            "network": "192.168.50.0/24",
            "pool_start": "192.168.50.20",
            "pool_end": "192.168.50.200",
        }
        self.worker.desired["dhcp"] = (True, settings)
        with patch.object(self.worker, "_network_available", return_value=True), patch(
            "tools.mini_services.detect_foreign_dhcp_servers", side_effect=OSError("probe unavailable")
        ), patch("tools.mini_services.BootAwareDhcpService") as factory:
            self.worker._start_one("dhcp")

        factory.assert_not_called()
        self.assertEqual("failed", self.worker.states["dhcp"].state)
        self.assertEqual("dhcp_probe_unavailable", self.worker.states["dhcp"].last_error["code"])

    def test_noop_reload_preserves_sip_and_other_listeners(self):
        with patch("tools.mini_services.DnsService"), patch("tools.mini_services.BootAwareDhcpService"), patch(
            "tools.mini_services.SipRegistrarService"
        ) as sip:
            self.worker._load_network_services()
            self.worker._load_network_services()
            self.assertEqual(1, sip.call_count)
            sip.return_value.stop.assert_not_called()

    def test_invalid_reload_preserves_last_good_services(self):
        with patch("tools.mini_services.DnsService") as dns, patch("tools.mini_services.BootAwareDhcpService"), patch("tools.mini_services.SipRegistrarService"):
            self.worker._load_network_services()
            self.worker.config_path.write_text("invalid")
            with self.assertRaises(ValueError):
                self.worker._load_network_services()
            dns.return_value.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
