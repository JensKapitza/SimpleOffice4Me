"""Read-only nft JSON boundaries; no nft installation or root required."""
import copy
import json
import unittest
from unittest.mock import patch

from simpleoffice_network_gateway_runtime import _linux_rule_structure, gateway_health, apply_gateway


class GatewayRuleStructureTests(unittest.TestCase):
    def setUp(self):
        self.status = {"platform": "linux", "mode": "route", "rule_counts": {"forward": 1}}
        self.rows = [
            {"metainfo": {"json_schema_version": 1}},
            {"table": {"family": "inet", "name": "simpleoffice_mini", "handle": 7}},
            {"chain": {"family": "inet", "table": "simpleoffice_mini", "name": "forward", "handle": 1,
                       "type": "filter", "hook": "forward", "prio": 0, "policy": "drop"}},
            {"rule": {"family": "inet", "table": "simpleoffice_mini", "chain": "forward", "handle": 3,
                      "expr": [{"accept": None}]}}
        ]

    def inspect(self, rows):
        with patch("simpleoffice_network_gateway_runtime._run", return_value={"ok": True, "stdout": json.dumps({"nftables": rows})}) as run:
            result = _linux_rule_structure(self.status)
        self.assertEqual((["nft", "-j", "list", "table", "inet", "simpleoffice_mini"], 2), run.call_args.args)
        return result

    def test_valid_structure_and_handles_are_not_semantic(self):
        self.assertTrue(self.inspect(self.rows))
        self.rows[3]["rule"]["handle"] = 999
        self.assertTrue(self.inspect(self.rows))

    def test_empty_or_extra_rules_and_missing_chain_are_unhealthy(self):
        for rows in (self.rows[:-1], self.rows + [self.rows[-1]], self.rows[:2] + self.rows[3:]):
            self.assertFalse(self.inspect(rows))

    def test_changed_policy_hook_priority_type_and_dormant_table_are_unhealthy(self):
        for field, value in (("policy", "accept"), ("hook", "input"), ("prio", 10), ("type", "nat")):
            rows = copy.deepcopy(self.rows)
            rows[2]["chain"][field] = value
            self.assertFalse(self.inspect(rows))
        self.rows[1]["table"]["flags"] = ["dormant"]
        self.assertFalse(self.inspect(self.rows))

    def test_deliberately_empty_forward_chain_is_supported(self):
        self.status["rule_counts"]["forward"] = 0
        self.assertTrue(self.inspect(self.rows[:-1]))

    def test_nat_requires_postrouting_structure_too(self):
        self.status.update(mode="nat", rule_counts={"forward": 1, "postrouting": 1})
        nat = {"nftables": [
            {"table": {"family": "ip", "name": "simpleoffice_mini_nat"}},
            {"chain": {"family": "ip", "table": "simpleoffice_mini_nat", "name": "postrouting", "type": "nat", "hook": "postrouting", "prio": 100, "policy": "accept"}},
            {"rule": {"family": "ip", "table": "simpleoffice_mini_nat", "chain": "postrouting", "expr": [{"masquerade": None}]}}
        ]}
        def run(args, timeout):
            self.assertEqual(2, timeout)
            return {"ok": True, "stdout": json.dumps(nat if args[-1] == "simpleoffice_mini_nat" else {"nftables": self.rows})}
        with patch("simpleoffice_network_gateway_runtime._run", side_effect=run):
            self.assertTrue(_linux_rule_structure(self.status))
            nat["nftables"].pop()
            self.assertFalse(_linux_rule_structure(self.status))

    def test_unreadable_or_malformed_status_is_unknown_not_confirmed_failure(self):
        for result in ({"ok": False}, {"ok": True, "stdout": "invalid"}, {"ok": True, "stdout": '{"nftables":[null]}'},
                       {"ok": True, "stdout": '{"nftables":[{"chain":null}]}' }):
            with patch("simpleoffice_network_gateway_runtime._linux_tables", return_value={("inet", "simpleoffice_mini")}), patch("simpleoffice_network_gateway_runtime._run", return_value=result):
                self.assertIsNone(gateway_health(self.status)["ok"])

    def test_apply_records_counts_from_enabled_rules(self):
        data = {"platform": "linux", "warnings": [], "enabled": True, "mode": "nat", "forward_ipv4": False,
                "allow_established": True, "allow_lan_to_wan": True, "allow_wan_to_lan": False,
                "effective_internal_interface": "lan0", "effective_external_interface": "wan0", "internal_network": "192.168.1.0/24"}
        with patch("simpleoffice_network_gateway_runtime.effective_gateway", return_value=data), patch("simpleoffice_network_gateway_runtime.shutil.which", return_value="tool"), patch("simpleoffice_network_gateway_runtime._replace_linux_rules"):
            self.assertEqual({"forward": 2, "postrouting": 1}, apply_gateway({})["rule_counts"])
            data.update(allow_established=False, allow_lan_to_wan=False, mode="route")
            self.assertEqual({"forward": 0, "postrouting": 0}, apply_gateway({})["rule_counts"])
