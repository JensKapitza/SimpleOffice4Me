"""Independent production services enforce the policy before side effects."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

import simpleoffice_runtime_support as policy
from tools import launcher, mini_services
import simpleoffice_firewall_agent as firewall
import simpleoffice_https_connect_tunnel as tunnel


class StandaloneRuntimeSupportTests(unittest.TestCase):
    def setUp(self):
        cached = policy._RUNTIME_PROBLEM_CACHE
        policy._RUNTIME_PROBLEM_CACHE = policy._RUNTIME_PROBLEM_UNSET
        self.addCleanup(setattr, policy, "_RUNTIME_PROBLEM_CACHE", cached)
        rejected = patch.object(policy, "runtime_problem", return_value="unsupported runtime")
        rejected.start()
        self.addCleanup(rejected.stop)

    def test_worker_start_and_restart_reject_before_creation_or_dispatch(self):
        for command in ("start", "restart"):
            with self.subTest(command=command), patch.object(mini_services, "Worker") as worker, \
                    patch.object(mini_services, "service_command") as dispatch:
                with self.assertRaises(SystemExit) as raised:
                    mini_services.main([command, "--service", "dns"])
                self.assertEqual(78, raised.exception.code)
                worker.assert_not_called()
                dispatch.assert_not_called()

    def test_direct_worker_start_rejects_before_recovery(self):
        worker = Mock()
        with self.assertRaisesRegex(RuntimeError, "unsupported runtime"):
            mini_services.Worker.start(worker)
        worker.control.recover_interrupted.assert_not_called()

    def test_firewall_serve_rejects_before_recovery_or_socket(self):
        with patch.object(firewall, "recover_pending") as recover, patch.object(firewall.socket, "socket") as socket:
            with self.assertRaises(SystemExit) as raised:
                firewall.main(["--serve"])
            self.assertEqual(78, raised.exception.code)
            recover.assert_not_called()
            socket.assert_not_called()

    def test_firewall_rollback_remains_available(self):
        with patch.object(firewall, "rollback_all_pending", return_value={"failed": []}) as rollback:
            firewall.main(["--rollback-pending"])
        rollback.assert_called_once()

    def test_worker_stop_and_status_remain_available(self):
        with patch.object(mini_services, "service_command", return_value=({"ok": True}, 0)) as dispatch:
            for command in ("stop", "status"):
                mini_services.main([command, "--service", "dns"])
        self.assertEqual(2, dispatch.call_count)

    def test_launcher_restart_rejects_before_stopping_existing_services(self):
        from tools import service_control
        with patch.object(sys, "argv", ["launcher", "restart"]), patch.object(service_control, "stop") as stop:
            with self.assertRaises(SystemExit) as raised:
                launcher.main()
            self.assertEqual(78, raised.exception.code)
            stop.assert_not_called()

    def test_direct_launcher_and_tunnel_start_reject_before_configuration_or_connection(self):
        with patch.object(launcher, "first_start_configure") as configure, patch.object(tunnel, "_connect") as connect:
            with self.assertRaises(SystemExit) as raised:
                launcher.start()
            self.assertEqual(78, raised.exception.code)
            with self.assertRaisesRegex(RuntimeError, "unsupported runtime"):
                tunnel.tunnel(Path("unused"), "example.org:443")
            configure.assert_not_called()
            connect.assert_not_called()


class RuntimePackagingTests(unittest.TestCase):
    def test_mini_services_policy_exit_does_not_restart(self):
        unit = (Path(__file__).resolve().parents[1] / "packaging/simpleoffice-mini-services.service").read_text()
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("RestartPreventExitStatus=78", unit)
        root = Path(__file__).resolve().parents[1]
        for name in ("simpleoffice-firewall-agent.service", "simpleoffice4me.service"):
            service = (root / "packaging" / name).read_text()
            self.assertIn("RestartPreventExitStatus=78", service)

    def test_recovery_entrypoint_uses_independent_wrapper(self):
        root = Path(__file__).resolve().parents[1]
        project = (root / "pyproject.toml").read_text()
        wrapper = (root / "simpleoffice_recovery_cli.py").read_text()
        self.assertIn('simpleoffice-v2-recovery = "simpleoffice_recovery_cli:main"', project)
        self.assertIn("_simpleoffice_recovery_import", wrapper)
