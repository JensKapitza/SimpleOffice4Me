"""Regression tests for safe Mini Services firewall management."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import simpleoffice_firewall as firewall
import simpleoffice_firewall_agent as agent


class FirewallValidationTests(unittest.TestCase):
    def test_rule_validation_normalizes_range_and_source(self):
        rule = firewall.normalize_rule({
            "effect": "allow", "protocol": "udp", "port_start": "5004",
            "port_end": 5005, "source": "192.168.10.42/24", "zone": "lan",
        })
        self.assertEqual((5004, 5005, "udp"), (rule["port_start"], rule["port_end"], rule["protocol"]))
        self.assertEqual("192.168.10.0/24", rule["source"])
        with self.assertRaises(ValueError):
            firewall.normalize_rule({"effect": "allow", "protocol": "icmp", "port": 53})
        with self.assertRaises(ValueError):
            firewall.normalize_rules([])

    def test_loopback_service_never_generates_firewall_rules(self):
        service = {"name": "lokal", "bind": "127.0.0.1", "ports": [{"protocol": "tcp", "port_start": 8080, "port_end": 8080}]}
        with self.assertRaises(ValueError):
            firewall.rules_for_service(service, "allow")

    def test_control_store_queues_only_validated_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "instance" / "mini-services.json"
            store = firewall.FirewallControlStore(config)
            queued = store.enqueue("test", {"rules": [{"effect": "deny", "protocol": "tcp", "port": 2222}]})
            claimed = store.claim()
            self.assertEqual(queued["id"], claimed["id"])
            self.assertEqual(2222, claimed["payload"]["rules"][0]["port_start"])
            store.finish(queued["id"], True, {"test_id": "a" * 32})
            self.assertEqual("completed", store.operation(queued["id"])["state"])
            with self.assertRaises(ValueError):
                store.enqueue("shell", {})


class FirewallParserTests(unittest.TestCase):
    def test_ufw_status_parses_default_and_numbered_rules(self):
        verbose = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)
"""
        numbered = """[ 1] 53/udp                     ALLOW IN    Anywhere
[ 2] 5004:5005/udp              DENY IN     10.0.0.0/8 # simpleoffice-test-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
[ 3] 53/udp (v6)                DENY IN     Anywhere (v6)
"""
        result = agent.parse_ufw_status(verbose, numbered)
        self.assertTrue(result["active"])
        self.assertEqual("deny", result["default_incoming"])
        self.assertEqual(("allow", 53, 53), (result["rules"][0]["effect"], result["rules"][0]["port_start"], result["rules"][0]["port_end"]))
        self.assertEqual((5004, 5005), (result["rules"][1]["port_start"], result["rules"][1]["port_end"]))
        self.assertEqual("ipv6", result["rules"][2]["family"])

    def test_firewalld_zones_and_ports_are_normalized(self):
        self.assertEqual(["public", "home"], agent.parse_firewalld_active_zones("public\n  interfaces: eth0\nhome\n  interfaces: wlan0\n"))
        listing = """public (active)
  target: default
  interfaces: eth0
  sources:
  services:
  ports: 53/udp 8080/tcp
  rich rules:
    rule priority="-100" port port="2222" protocol="tcp" reject
"""
        with patch("simpleoffice_firewall_agent._firewalld_service_ports", return_value=[]):
            rules, meta = agent.parse_firewalld_zone("public", listing)
        self.assertEqual("eth0", meta["interfaces"][0])
        self.assertTrue(any(row["effect"] == "allow" and row["port_start"] == 8080 for row in rules))
        self.assertTrue(any(row["effect"] == "deny" and row["port_start"] == 2222 for row in rules))

    def test_two_active_managers_block_writes(self):
        with patch("simpleoffice_firewall_agent._firewalld_snapshot", return_value={"installed": True, "active": True, "rules": [], "active_zones": ["public"], "zones": []}),              patch("simpleoffice_firewall_agent._ufw_snapshot", return_value={"installed": True, "active": True, "rules": [], "default_incoming": "deny"}),              patch("simpleoffice_firewall_agent._pending_tests", return_value=[]),              patch("simpleoffice_firewall_agent.shutil.which", return_value="/usr/sbin/nft"):
            result = agent.firewall_snapshot()
        self.assertEqual("conflict", result["backend"])
        self.assertTrue(result["conflict"])
        self.assertFalse(result["writable"])

    def test_ufw_marker_is_verified_before_numbered_delete(self):
        marker = "simpleoffice-test-" + "a" * 32
        commands = []
        def fake_run(command, timeout=agent.COMMAND_TIMEOUT):
            commands.append(command)
            if command[:3] == ["ufw", "status", "numbered"]:
                return {"ok": True, "missing": False, "stdout": (
                    "[ 1] 22/tcp ALLOW IN Anywhere # foreign\n"
                    f"[ 3] 8081/tcp DENY IN Anywhere # {marker}\n"
                ), "stderr": "", "returncode": 0}
            return {"ok": True, "missing": False, "stdout": "", "stderr": "", "returncode": 0}
        with patch("simpleoffice_firewall_agent._run", side_effect=fake_run):
            agent._ufw_delete_marker(marker)
        self.assertIn(["ufw", "--force", "delete", "3"], commands)
        self.assertNotIn(["ufw", "--force", "delete", "1"], commands)

    def test_ufw_test_rule_is_inserted_first(self):
        command = agent._ufw_command({
            "effect": "deny", "protocol": "tcp", "port_start": 8081,
            "port_end": 8081, "source": "", "zone": "", "label": "",
        }, "simpleoffice-test-" + "b" * 32)
        self.assertEqual(["ufw", "--force", "insert", "1", "deny"], command[:5])
        self.assertNotIn("shell", " ".join(command).lower())


class FirewallSafetyTests(unittest.TestCase):
    def test_watchdog_is_armed_before_first_mutation(self):
        order = []
        rule = firewall.normalize_rule({"effect": "allow", "protocol": "tcp", "port": 8081})
        lock = unittest.mock.MagicMock()
        with patch("simpleoffice_firewall_agent.firewall_snapshot", return_value={
                "backend": "ufw", "active": True, "writable": True, "conflict": False,
                "active_zones": [], "rules": [], "tests": []}),              patch("simpleoffice_firewall_agent._write_plan", side_effect=lambda _plan: order.append("plan")),              patch("simpleoffice_firewall_agent._locked_plan", return_value=lock),              patch("simpleoffice_firewall_agent._schedule_rollback", side_effect=lambda _ident, _delay: order.append("watchdog") or "process"),              patch("simpleoffice_firewall_agent._ufw_apply_prepared", side_effect=lambda _value: order.append("apply")):
            result = agent.test_rules([rule])
        lock.close.assert_called_once()
        self.assertEqual("ufw", result["backend"])
        self.assertLess(order.index("watchdog"), order.index("apply"))
        self.assertEqual("plan", order[order.index("apply") - 1])

    def test_firewall_decision_keeps_listener_and_policy_semantics_conservative(self):
        ufw = {"backend": "ufw", "active": True, "conflict": False, "default_incoming": "deny", "rules": []}
        self.assertEqual("blocked", firewall.firewall_decision(ufw, "tcp", 8080, 8080)["state"])
        ufw["rules"] = [{"effect": "allow", "protocol": "tcp", "port_start": 8080, "port_end": 8080, "order": 1, "summary": "allow"}]
        self.assertEqual("allowed", firewall.firewall_decision(ufw, "tcp", 8080, 8080)["state"])
        self.assertEqual("local-only", firewall.firewall_decision(ufw, "tcp", 8080, 8080, "127.0.0.1")["state"])
        mixed = {
            "backend": "ufw", "active": True, "conflict": False, "default_incoming": "deny",
            "rules": [
                {"effect": "allow", "protocol": "tcp", "port_start": 8080, "port_end": 8080, "order": 1, "family": "ipv4"},
                {"effect": "deny", "protocol": "tcp", "port_start": 8080, "port_end": 8080, "order": 2, "family": "ipv6"},
            ],
        }
        self.assertEqual("unknown", firewall.firewall_decision(mixed, "tcp", 8080, 8080, "")["state"])

    def test_firewalld_multiple_zones_stay_unknown_and_accept_target_is_honored(self):
        multiple = {
            "backend": "firewalld", "active": True, "conflict": False,
            "active_zones": ["public", "home"], "zones": [], "rules": [],
        }
        self.assertEqual("unknown", firewall.firewall_decision(multiple, "tcp", 8080, 8080, "192.168.1.2")["state"])
        accepting = {
            "backend": "firewalld", "active": True, "conflict": False,
            "active_zones": ["trusted"], "zones": [{"zone": "trusted", "target": "ACCEPT"}], "rules": [],
        }
        self.assertEqual("allowed", firewall.firewall_decision(accepting, "tcp", 8080, 8080)["state"])

    def test_partial_confirmation_cleans_owned_rules_before_returning_to_pending(self):
        ident = "c" * 32
        applied = [
            firewall.normalize_rule({"effect": "allow", "protocol": "tcp", "port": 8081}),
            firewall.normalize_rule({"effect": "allow", "protocol": "tcp", "port": 8082}),
        ]
        plan = {
            "id": ident, "backend": "ufw", "status": "pending",
            "expires_at": agent.time.time() + 60, "applied": applied,
            "confirmation_changes": [],
        }
        cleaned = []
        lock = unittest.mock.MagicMock()
        with patch("simpleoffice_firewall_agent._locked_plan", return_value=lock), \
             patch("simpleoffice_firewall_agent._read_plan", return_value=plan), \
             patch("simpleoffice_firewall_agent._write_plan"), \
             patch("simpleoffice_firewall_agent._ufw_add_managed", side_effect=[None, RuntimeError("fail")]), \
             patch("simpleoffice_firewall_agent._remove_confirmation_change", side_effect=lambda change: cleaned.append(change["marker"])):
            with self.assertRaises(RuntimeError):
                agent.confirm_test(ident)
        self.assertEqual("pending", plan["status"])
        self.assertEqual([], plan["confirmation_changes"])
        self.assertEqual(2, len(cleaned))
        lock.close.assert_called_once()

    def test_agent_rejects_extra_fields_per_action(self):
        with self.assertRaises(ValueError):
            agent.handle_request({"action": "snapshot", "rules": []})

    def test_service_inventory_uses_current_configured_ports(self):
        with patch("simpleoffice_firewall.read_status", return_value={"services": {}}),              patch("simpleoffice_firewall.load_config", return_value={
                 "dhcp": {"enabled": True, "bind": "192.168.1.2", "port": 1067},
                 "dns": {"enabled": True, "bind": ["192.168.1.2"], "port": 1053},
             }),              patch("simpleoffice_firewall.load_boot_settings", return_value={"enabled": True, "tftp_enabled": True, "tftp_port": 1069, "tftp_bind": "192.168.1.2"}),              patch("simpleoffice_firewall.effective_sip_settings", return_value={"registrar_port": 15060, "transport": "udp", "bind_host": "192.168.1.2"}),              patch("simpleoffice_firewall._audio_receiver_settings", return_value={"enabled": True, "port": 15004, "bind": "192.168.1.2"}),              patch("simpleoffice_firewall.load_relay_settings", return_value={"enabled": True, "turn_port": 13478, "tls_enabled": True, "turn_tls_port": 15349, "min_port": 49160, "max_port": 49200, "listen_ip": "192.168.1.2"}),              patch("simpleoffice_firewall.load_media_renderer_settings", return_value={"enabled": True, "port": 18200, "bind": "192.168.1.2"}),              patch("simpleoffice_firewall._web_settings", return_value={"host": "0.0.0.0", "port": 18080}):
            rows = firewall.service_port_inventory(Path("/tmp/simpleoffice-test/mini-services.json"))
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(1067, by_id["dhcp"]["ports"][0]["port_start"])
        self.assertEqual(1053, by_id["dns"]["ports"][0]["port_start"])
        self.assertEqual(1069, by_id["tftp"]["ports"][0]["port_start"])
        self.assertEqual(15060, by_id["sip"]["ports"][0]["port_start"])
        self.assertEqual(15004, by_id["audio-receiver"]["ports"][0]["port_start"])
        self.assertEqual(13478, by_id["relay"]["ports"][0]["port_start"])
        self.assertEqual(18200, by_id["media-renderer"]["ports"][0]["port_start"])
        self.assertEqual(18080, by_id["http-boot"]["ports"][0]["port_start"])
        self.assertTrue(by_id["http-boot"]["critical"])


class FirewallPackagingTests(unittest.TestCase):
    def test_systemd_boundary_keeps_web_unprivileged(self):
        root = Path(__file__).resolve().parents[1]
        worker = (root / "packaging/simpleoffice-mini-services.service").read_text(encoding="utf-8")
        socket_unit = (root / "packaging/simpleoffice-firewall-agent.socket").read_text(encoding="utf-8")
        agent_unit = (root / "packaging/simpleoffice-firewall-agent.service").read_text(encoding="utf-8")
        self.assertIn("SupplementaryGroups=simpleoffice-firewall", worker)
        self.assertIn("SocketGroup=simpleoffice-firewall", socket_unit)
        self.assertIn("SocketMode=0660", socket_unit)
        self.assertIn("User=root", agent_unit)
        self.assertIn("NoNewPrivileges=true", agent_unit)
        self.assertIn("ProtectSystem=strict", agent_unit)
        self.assertNotIn("sudo", (root / "app/firewall_admin.py").read_text(encoding="utf-8"))
        self.assertNotIn("shell=True", (root / "simpleoffice_firewall_agent.py").read_text(encoding="utf-8"))

    def test_package_removal_rolls_back_pending_tests(self):
        root = Path(__file__).resolve().parents[1]
        prerm = (root / "packaging/prerm.sh").read_text(encoding="utf-8")
        self.assertIn("--rollback-pending", prerm)
        self.assertNotIn("--rollback-pending >/dev/null 2>&1 || true", prerm)
        service = (root / "packaging/simpleoffice-firewall-agent.service").read_text(encoding="utf-8")
        self.assertIn("/var/lib/simpleoffice4me-firewall-agent", service)
        self.assertNotIn("/var/lib/simpleoffice4me/firewall-agent", service)
        build = (root / "packaging/build-fpm.sh").read_text(encoding="utf-8")
        self.assertIn("simpleoffice-firewall-agent.socket", build)
        self.assertIn("simpleoffice-firewall-agent.service", build)


if __name__ == "__main__":
    unittest.main()
