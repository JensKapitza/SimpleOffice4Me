"""Expected expressions for SimpleOffice's small nft rule subset."""
import copy
import ipaddress


def expected_rules(data):
    def match(left, right, op="=="):
        return {"match": {"op": op, "left": left, "right": right}}

    def interface(key, value):
        return match({"meta": {"key": key}}, value)

    forward = []
    if data["allow_established"]:
        forward.append([match({"ct": {"key": "state"}}, ["established", "related"], "in"), {"accept": None}])
    internal, external = data["effective_internal_interface"], data["effective_external_interface"]
    if internal and external:
        if data["allow_lan_to_wan"]:
            forward.append([interface("iifname", internal), interface("oifname", external), {"accept": None}])
        if data["allow_wan_to_lan"]:
            forward.append([interface("iifname", external), interface("oifname", internal), {"accept": None}])
    postrouting = []
    if data["mode"] == "nat":
        network = ipaddress.IPv4Network(data["internal_network"])
        address = str(network.network_address)
        source = address if network.prefixlen == 32 else {"prefix": {"addr": address, "len": network.prefixlen}}
        postrouting.append([match({"payload": {"protocol": "ip", "field": "saddr"}}, source),
                            interface("oifname", external), {"masquerade": None}])
    return {"forward": forward, "postrouting": postrouting}


def normalize_expressions(expressions):
    """Normalize only non-semantic encodings; preserve rule/statement order."""
    result = copy.deepcopy(expressions)
    for rule in result:
        for statement in rule:
            if statement == {"masquerade": {}}:
                statement["masquerade"] = None
            if not isinstance(statement, dict):
                continue
            match = statement.get("match")
            if not isinstance(match, dict) or match.get("left") != {"ct": {"key": "state"}} or match.get("op") != "in":
                continue
            right = match.get("right")
            if isinstance(right, list) and all(isinstance(item, str) for item in right):
                match["right"] = sorted(right)
    return result
