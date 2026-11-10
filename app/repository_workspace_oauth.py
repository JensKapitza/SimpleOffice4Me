"""Restricted GitHub App Device Flow transport; no repository write endpoint."""
from __future__ import annotations

import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .repository_workspace_store import WorkspaceAuthError

DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
MAX_RESPONSE_BYTES = 32 * 1024


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = build_opener(_RejectRedirects())


def _read_response(response) -> dict:
    declared = response.headers.get("Content-Length")
    if declared is not None:
        length = int(declared)
        if length < 0 or length > MAX_RESPONSE_BYTES:
            raise ValueError("Invalid OAuth response length")
    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("OAuth response too large")
    result = json.loads(payload.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Invalid OAuth response")
    return result


def post(url: str, fields: dict[str, str]) -> dict:
    if url not in {DEVICE_URL, TOKEN_URL}:
        raise WorkspaceAuthError("Unzulässiger GitHub-Anmeldeendpunkt.", 400)
    request = Request(url, data=urlencode(fields).encode("ascii"), method="POST", headers={
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "SimpleOffice4Me-ReadOnly-Workspace",
    })
    try:
        with _OPENER.open(request, timeout=10) as response:
            return _read_response(response)
    except HTTPError as exc:
        # GitHub may return the standard OAuth error object with HTTP 400.
        try:
            if exc.code == 400:
                payload = _read_response(exc)
                if isinstance(payload.get("error"), str):
                    return payload
        except (ValueError, UnicodeError, TypeError, RecursionError):
            pass
        finally:
            exc.close()
        retry_after = 60 if exc.code in {429, 503} else 0
        raise WorkspaceAuthError("GitHub konnte die Anmeldung nicht verarbeiten. Bitte später erneut versuchen.",
                                 502, retry_after) from None
    except (URLError, OSError, socket.timeout):
        raise WorkspaceAuthError("GitHub ist für die Anmeldung nicht erreichbar. Bitte erneut versuchen.", 502) from None
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise WorkspaceAuthError("GitHub hat eine ungültige Anmeldeantwort geliefert.", 502) from None
