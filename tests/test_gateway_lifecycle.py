import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simpleoffice_network_gateway import effective_gateway
from simpleoffice_network_gateway_runtime import _replace_linux_rules, disable_gateway, gateway_health
from tools.mini_services import Worker


class GatewayRuntimeTests(unittest.TestCase):
    def test_linux_replacement_is_one_transaction_and_only_owns_named_tables(self):
        tables = {("inet", "simpleoffice_mini"), ("ip", "simpleoffice_mini_nat"), ("inet", "host_firewall")}
        with patch("simpleoffice_network_gateway_runtime._linux_tables", return_value=tables), patch(
            "simpleoffice_network_gateway_runtime.shutil.which", return_value="/usr/sbin/nft"
        ), patch("simpleoffice_network_gateway_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            _replace_linux_rules("table inet simpleoffice_mini {}\n")
        run.assert_called_once()
        self.assertEqual(["/usr/sbin/nft", "-f", "-"], run.call_args.args[0])
        script = run.call_args.kwargs["input"]
        self.assertIn("delete table inet simpleoffice_mini", script)
        self.assertIn("delete table ip simpleoffice_mini_nat", script)
        self.assertNotIn("host_firewall", script)
        self.assertNotIn("flush", script)

    def test_stop_is_idempotent_but_read_or_delete_failure_is_not_success(self):
        with patch("simpleoffice_network_gateway_runtime.platform_kind", return_value="linux"):
            with patch("simpleoffice_network_gateway_runtime._linux_tables", return_value=set()), patch(
                "simpleoffice_network_gateway_runtime.subprocess.run"
            ) as run:
                self.assertTrue(disable_gateway({})["ok"])
                self.assertTrue(disable_gateway({})["ok"])
                run.assert_not_called()
            with patch("simpleoffice_network_gateway_runtime._linux_tables", side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    disable_gateway({})
            with patch("simpleoffice_network_gateway_runtime._linux_tables", return_value={("inet", "simpleoffice_mini")}), patch(
                "simpleoffice_network_gateway_runtime.shutil.which", return_value="/usr/sbin/nft"
            ), patch("simpleoffice_network_gateway_runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaises(RuntimeError):
                    disable_gateway({})

    def test_health_checks_rules_and_forwarding_independently(self):
        status = {"platform": "linux", "mode": "nat"}
        structure = patch("simpleoffice_network_gateway_runtime._linux_rule_structure", return_value=True)
        structure.start()
        self.addCleanup(structure.stop)
        with patch("simpleoffice_network_gateway_runtime._linux_tables", return_value={("inet", "simpleoffice_mini"), ("ip", "simpleoffice_mini_nat")}), patch.object(Path, "read_text", return_value="1"):
            self.assertTrue(gateway_health(status)["ok"])
            with patch.object(Path, "read_text", return_value="0"):
                self.assertFalse(gateway_health(status)["ok"])
        with patch("simpleoffice_network_gateway_runtime._linux_tables", return_value=set()), patch.object(Path, "read_text", return_value="1"):
            self.assertFalse(gateway_health(status)["ok"])
        with patch("simpleoffice_network_gateway_runtime._linux_tables", side_effect=PermissionError):
            self.assertIsNone(gateway_health(status)["ok"])

    def test_windows_stop_does_not_claim_success_on_permission_failure(self):
        with patch("simpleoffice_network_gateway_runtime.platform_kind", return_value="windows"), patch(
            "simpleoffice_network_gateway_runtime._powershell", return_value={"ok": False, "missing": False}
        ) as powershell:
            with self.assertRaises(RuntimeError):
                disable_gateway({"mode": "nat"})
            self.assertIn("-ErrorAction Stop", powershell.call_args.args[0])
            self.assertNotIn("SilentlyContinue", powershell.call_args.args[0])
            powershell.reset_mock()
            self.assertTrue(disable_gateway({"mode": "route"})["ok"])
            powershell.assert_not_called()

    def test_windows_health_reads_forwarding_and_custom_nat_name(self):
        with patch("simpleoffice_network_gateway_runtime._powershell", return_value={"ok": True, "stdout": json.dumps({"forwarding": True, "rules": True})}) as powershell:
            self.assertTrue(gateway_health({"platform": "windows", "mode": "nat", "nat_name": "LocalNat", "internal": "LAN", "external": "WAN"})["ok"])
            self.assertIn("Get-NetNat -Name 'LocalNat'", powershell.call_args.args[0])
            self.assertNotIn("Set-NetIPInterface", powershell.call_args.args[0])

    def test_detected_interfaces_are_validated_before_rule_generation(self):
        with patch("simpleoffice_network_gateway.detect_interfaces", return_value={"internal_interface": 'bad"; flush ruleset', "external_interface": "WAN", "snapshot": {"platform": "linux"}}):
            with self.assertRaises(ValueError):
                effective_gateway({"enabled": True, "mode": "nat"})

    def test_failed_stop_retains_ownership_for_explicit_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory) / "mini.json")
            worker.gateway_active = True
            worker.desired["gateway"] = (True, {"mode": "nat"})
            with patch("tools.mini_services.disable_gateway", side_effect=PermissionError):
                self.assertFalse(worker._stop_one("gateway"))
            self.assertTrue(worker.gateway_active)
            self.assertEqual("failed", worker.states["gateway"].state)
            with patch("tools.mini_services.disable_gateway", return_value={"ok": True}):
                self.assertTrue(worker._stop_one("gateway"))
            self.assertFalse(worker.gateway_active)

    def test_failed_explicit_stop_cannot_restart_gateway_during_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory) / "mini.json")
            worker.gateway_active = True
            worker.desired["gateway"] = (True, {"mode": "nat"})
            command = worker.control.enqueue("gateway", "stop")
            with patch("tools.mini_services.disable_gateway", side_effect=PermissionError):
                worker._execute(worker.control.claim())
            self.assertEqual("failed", worker.control.operation(command["id"])["state"])
            self.assertFalse(worker.desired["gateway"][0])
            with patch("tools.mini_services.disable_gateway", return_value={"ok": True}), patch("tools.mini_services.apply_gateway") as start:
                self.assertTrue(worker._stop_one("gateway"))
                worker._start_one("gateway")
                start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
