"""Optional breadcrumb from Advanced Document Search to the same search results."""

from __future__ import annotations

import re
from urllib.parse import SplitResult, parse_qs, urlsplit

from flask import Response, after_this_request, request, url_for

from .search_query import MAX_SEARCH_QUERY_CHARS

# Space for a fully URL-encoded maximum-length Unicode query and a cursor.
_MAX_REFERRER_LENGTH = 26_000
_CURSOR = re.compile(r"[A-Za-z0-9_=-]{1,256}")
_PAGE = re.compile(r"[0-9]{1,9}")


def _origin(parts: SplitResult) -> tuple[str, str, int] | None:
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    port = parts.port
    return parts.scheme, parts.hostname, port if port is not None else (443 if parts.scheme == "https" else 80)


def _search_return_url() -> str | None:
    referrer = request.referrer or ""
    if not referrer or len(referrer) > _MAX_REFERRER_LENGTH or any(ord(c) < 32 or ord(c) == 127 for c in referrer):
        return None
    try:
        source = urlsplit(referrer)
        origin = _origin(source)
        if (
            origin is None or origin != _origin(urlsplit(request.host_url))
            or source.path != url_for("documents.document_search") or source.fragment
        ):
            return None
        args = parse_qs(source.query, keep_blank_values=True, errors="strict", max_num_fields=12)
    except ValueError:
        return None
    if any(len(args.get(key, ())) > 1 for key in ("q", "page", "cursor")):
        return None
    query = args.get("q", [""])[0].strip()
    if not query or len(query) > MAX_SEARCH_QUERY_CHARS:
        return None
    values: dict[str, str | int] = {"q": query}
    page_value = args.get("page", [""])[0]
    page = int(page_value) if _PAGE.fullmatch(page_value) else 1
    if page > 1:
        values["page"] = page
        cursor = args.get("cursor", [""])[0]
        if _CURSOR.fullmatch(cursor):
            values["cursor"] = cursor
    return url_for("documents.document_search", **values)


def document_search_return() -> str | None:
    """Never use Referer as an authorization check or a redirect destination."""
    target = _search_return_url()

    @after_this_request
    def protect_search_context(response: Response) -> Response:
        response.vary.add("Referer")
        if target:
            response.headers["Cache-Control"] = "private, no-store"
        return response

    return target
