"""QR payload encoding for peer exchange."""
import base64
import json

from .federation_peer_profile import peer_profile


def encode_peer(profile):
    data = {"v": 1, "peer": peer_profile(profile)}
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sofp://peer/" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_peer(value):
    value = str(value or "").strip()
    prefix = "sofp://peer/"
    if not value.startswith(prefix):
        raise ValueError("invalid federation QR payload")
    encoded = value[len(prefix):]
    encoded += "=" * (-len(encoded) % 4)
    data = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("unsupported federation QR payload")
    return peer_profile(data.get("peer"))
