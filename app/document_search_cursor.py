"""Opaque continuation links for the authenticated document search."""
from __future__ import annotations

import base64
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, g, request


def _cipher(query: str, page: int) -> Fernet:
    # Domain separation also binds links to one search, viewer and archive.
    context = json.dumps([
        "document-search-cursor-v1", str(current_app.config["DOCUMENT_ROOT"]),
        g.user["username"], query, str(page),
    ], ensure_ascii=True, separators=(",", ":")).encode("ascii")
    secret = current_app.secret_key
    key = hmac.digest(secret.encode() if isinstance(secret, str) else secret, context, "sha256")
    return Fernet(base64.urlsafe_b64encode(key))


def search_link_page(store, query: str, page: int) -> dict:
    token = request.args.get("cursor", "")
    after = None
    if page > 1 and token and len(token) <= 256:
        try:
            position = _cipher(query, page).decrypt(token.encode(), ttl=3600)
            after = int.from_bytes(position, "big")
        except InvalidToken:
            pass  # Old or mismatched links still support ordinary page navigation.
    result = store.search_continuation_page(query, page=page, after_rowid=after)
    if after is not None:
        result["current_cursor"] = token
    next_position = result.pop("next_rowid", None)
    if next_position is not None:
        result["next_cursor"] = _cipher(query, page + 1).encrypt(
            next_position.to_bytes(8, "big")
        ).decode("ascii")
    return result
