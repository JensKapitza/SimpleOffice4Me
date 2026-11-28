"""Request boundary for browser pages that expose local devices."""
from urllib.parse import urlsplit

from flask import request


def direct_loopback_request() -> bool:
    try:
        host = urlsplit(request.host_url).hostname
    except ValueError:
        return False
    return (
        host in {"localhost", "127.0.0.1", "::1"}
        and request.remote_addr in {"127.0.0.1", "::1"}
        and not any(request.headers.get(header) for header in (
            "Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
            "X-Forwarded-Prefix", "X-Real-IP",
        ))
    )
