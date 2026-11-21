"""Authorization boundary for Resource Commander HTTP APIs.

Browser requests use the normal authenticated Flask session. Remote Resource
Commander access is intentionally separate from the general federation
transport token because the Commander can expose filesystem-like operations.
"""
from __future__ import annotations

import hmac

from flask import current_app, g, request

from . import resource_commander_credentials as _credentials
from .access_control import is_admin


def _bearer() -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.startswith("Bearer ") else ""


def remote_access_authorized() -> bool:
    """Accept only the dedicated Resource Commander bearer token.

    SIMPLEOFFICE_FEDERATION_TOKEN is deliberately not accepted here. Operators
    who need server-to-server Commander access must configure a distinct
    commander token on the receiving instance (peer-discovery admin section)
    and use that value as the peer token on the calling instance.
    """
    expected = _credentials.inbound_token()
    supplied = _bearer()
    if expected and supplied:
        return hmac.compare_digest(expected, supplied)
    return bool(current_app.testing and not expected)


def api_access_authorized() -> bool:
    user = getattr(g, "user", None)
    if user is not None and is_admin(user):
        # The local provider currently operates over a storage root rather
        # than per-document ACLs. Keep browser sessions at the admin boundary
        # until every provider operation is actor-aware.
        return True
    return remote_access_authorized()
