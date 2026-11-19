"""Choose a reachable origin for authenticated document download links."""
from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit

from flask import current_app, url_for

from .desktop_proxy_status import _lan_proxy_status


def _local(host: str) -> bool:
    if host.rstrip(".").lower() == "localhost":
        return True
    try:
        address = ip_address(host)
        return address.is_loopback or address.is_unspecified
    except ValueError:
        return False


def download_address(document_id: str) -> tuple[str, bool]:
    url = url_for("documents.transfer_download", document_id=document_id, _external=True)
    parts = urlsplit(url)
    if not _local(parts.hostname or ""):
        return url, False
    state = _lan_proxy_status(current_app.config["DOCUMENT_ROOT"])
    if state.get("state") == "running":
        try:
            proxy = urlsplit(state.get("origin") or "")
            if (proxy.scheme == "https" and proxy.hostname and not _local(proxy.hostname)
                    and proxy.username is None and proxy.password is None
                    and proxy.path in {"", "/"} and not proxy.query and not proxy.fragment):
                return parts._replace(scheme="https", netloc=proxy.netloc).geturl(), False
        except ValueError:
            pass
    return url, True
