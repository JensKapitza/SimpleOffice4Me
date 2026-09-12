"""Publish the local peer profile to configured directories."""
from .federation_discovery_email import email_hash
from .federation_discovery_service import bootstrap_token, bootstrap_urls
from .federation_local_profile import local_profile
from .federation_worker import _json_request


def publish(root, email="", urls=None, token="", ttl_seconds=86400):
    token = token or bootstrap_token()
    profile = local_profile(root)
    payload = {"profile": profile, "ttl_seconds": max(60, min(int(ttl_seconds), 7 * 86400))}
    if email:
        payload["lookup"] = email_hash(email)
    published = []
    errors = {}
    for base in urls or bootstrap_urls():
        try:
            _json_request(
                base + "/federation/v1/discovery/register",
                method="POST",
                token=token,
                payload=payload,
                timeout=10,
            )
            published.append(base)
        except Exception as exc:
            errors[base] = str(exc)[:300]
    return {"published": published, "errors": errors}
