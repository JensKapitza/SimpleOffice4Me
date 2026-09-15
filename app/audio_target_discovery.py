"""RTP target projection over the existing, bounded federation LAN scanner."""
from urllib.parse import urlsplit

from .federation_discovery_lan import is_private_lan_ipv4


def receiver_capability(status):
    """Only advertise a live desktop receiver bound to a private LAN address."""
    receiver = status.get("receiver", {})
    port = receiver.get("port")
    if receiver.get("running") is not True or not is_private_lan_ipv4(receiver.get("bind")):
        return None
    if type(port) is not int or not 1024 <= port <= 65534:
        return None
    return {"host": receiver["bind"], "port": port, "codec": "opus", "transport": "rtp-udp"}


def targets_from_profiles(profiles):
    targets = {}
    for profile in profiles[:1024]:
        if not isinstance(profile, dict):
            continue
        capabilities = profile.get("capabilities")
        value = capabilities.get("audio_receiver") if isinstance(capabilities, dict) else None
        if not isinstance(value, dict):
            continue
        try:
            endpoint = urlsplit(str(profile.get("base_url", "")))
        except ValueError:
            continue
        host, port = value.get("host"), value.get("port")
        # Never let another peer redirect capture to an arbitrary third party.
        if not is_private_lan_ipv4(host) or host != endpoint.hostname:
            continue
        if type(port) is not int or not 1024 <= port <= 65534:
            continue
        if value.get("codec") != "opus" or value.get("transport") != "rtp-udp":
            continue
        target = f"{host}:{port}"
        targets[target] = {"id": target, "host": host, "port": port,
                           "label": str(profile.get("label") or host)[:160]}
        if len(targets) >= 64:
            break
    return sorted(targets.values(), key=lambda row: (row["label"].casefold(), row["id"]))
