"""Compare generated intent against explicit nft JSON expressions."""
import copy
import unittest
from simpleoffice_gateway_rule_content import expected_rules, normalize_expressions


class GatewayRuleContentTests(unittest.TestCase):
    def setUp(self):
        self.data = {"allow_established": True, "allow_lan_to_wan": True, "allow_wan_to_lan": False,
                     "effective_internal_interface": "lan0", "effective_external_interface": "wan0",
                     "mode": "nat", "internal_network": "192.168.1.0/24"}
        self.forward = [
            [{"match": {"op": "in", "left": {"ct": {"key": "state"}}, "right": ["related", "established"]}}, {"accept": None}],
            [{"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": "lan0"}},
             {"match": {"op": "==", "left": {"meta": {"key": "oifname"}}, "right": "wan0"}}, {"accept": None}]
        ]
        self.nat = [[{"match": {"op": "==", "left": {"payload": {"protocol": "ip", "field": "saddr"}},
                                "right": {"prefix": {"addr": "192.168.1.0", "len": 24}}}},
                     {"match": {"op": "==", "left": {"meta": {"key": "oifname"}}, "right": "wan0"}}, {"masquerade": {}}]]

    def test_expected_intent_matches_documented_forms_without_mutating_input(self):
        original = copy.deepcopy(self.forward)
        expected = expected_rules(self.data)
        self.assertEqual(normalize_expressions(expected["forward"]), normalize_expressions(self.forward))
        self.assertEqual(normalize_expressions(expected["postrouting"]), normalize_expressions(self.nat))
        self.assertEqual(original, self.forward)

    def test_wrong_direction_state_action_and_order_are_not_normalized_away(self):
        good = normalize_expressions(self.forward)
        changed = copy.deepcopy(self.forward)
        changed[1][0]["match"]["right"] = "wan0"
        self.assertNotEqual(good, normalize_expressions(changed))
        changed = copy.deepcopy(self.forward)
        changed[0][0]["match"]["right"].append("new")
        self.assertNotEqual(good, normalize_expressions(changed))
        changed = copy.deepcopy(self.forward)
        changed[0][0]["match"]["op"] = "=="
        self.assertNotEqual(good, normalize_expressions(changed))
        self.assertNotEqual(good, normalize_expressions(list(reversed(self.forward))))

    def test_changed_prefix_and_masquerade_options_are_detected(self):
        good = normalize_expressions(self.nat)
        changed = copy.deepcopy(self.nat)
        changed[0][0]["match"]["right"]["prefix"]["len"] = 16
        self.assertNotEqual(good, normalize_expressions(changed))
        changed = copy.deepcopy(self.nat)
        changed[0][-1] = {"masquerade": {"flags": "random"}}
        self.assertNotEqual(good, normalize_expressions(changed))

    def test_route_only_and_single_host_network(self):
        self.data.update(mode="route", allow_established=False, allow_lan_to_wan=False)
        self.assertEqual({"forward": [], "postrouting": []}, expected_rules(self.data))
        self.data.update(mode="nat", internal_network="192.168.1.1/32")
        self.assertEqual("192.168.1.1", expected_rules(self.data)["postrouting"][0][0]["match"]["right"])
