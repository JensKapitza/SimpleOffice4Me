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


def encode_connection_invite(profile, invite_id, secret):
    """Encode a short-lived one-scan invitation. The payload is a secret."""
    peer = peer_profile(profile)
    invite_id = str(invite_id or "")
    secret = str(secret or "")
    if not 20 <= len(invite_id) <= 128 or not 32 <= len(secret) <= 128:
        raise ValueError("invalid federation connection invitation")
    raw = json.dumps(
        {"v": 1, "peer": peer, "invite_id": invite_id, "secret": secret},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sofp://pair/" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_connection_invite(value):
    value = str(value or "").strip()
    prefix = "sofp://pair/"
    if not value.startswith(prefix) or len(value) > 8192:
        raise ValueError("invalid federation connection invitation")
    encoded = value[len(prefix):]
    try:
        data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid federation connection invitation") from exc
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("unsupported federation connection invitation")
    invite_id, secret = data.get("invite_id"), data.get("secret")
    if not isinstance(invite_id, str) or not 20 <= len(invite_id) <= 128:
        raise ValueError("invalid federation connection invitation")
    if not isinstance(secret, str) or not 32 <= len(secret) <= 128:
        raise ValueError("invalid federation connection invitation")
    return {"peer": peer_profile(data.get("peer")), "invite_id": invite_id, "secret": secret}
