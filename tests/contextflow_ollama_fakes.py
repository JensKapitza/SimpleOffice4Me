"""Shared Ollama transport fakes for ContextFlow suites (no network).

The vision and model-guard suites fake the local Ollama transport
(/api/show, /api/tags, /api/chat) through these URL-routed doubles,
so guard and parser behavior stays covered without any live backend.
"""
from __future__ import annotations

import json
from io import BytesIO
from unittest import mock

SHOW_PLAIN = {"capabilities": ["completion", "vision"]}
SHOW_THINKING = {"capabilities": ["completion", "vision", "tools", "thinking"]}


def _stream_context(chunks) -> mock.MagicMock:
    """Fake one streamed NDJSON chat response from chunk dicts/raw lines."""
    lines = []
    for chunk in chunks:
        if isinstance(chunk, bytes):
            lines.append(chunk if chunk.endswith(b"\n") else chunk + b"\n")
        else:
            lines.append(
                json.dumps(chunk, ensure_ascii=False).encode("utf-8") + b"\n"
            )
    stream = BytesIO(b"".join(lines))
    response = mock.MagicMock()
    response.readline.side_effect = stream.readline
    context = mock.MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False
    return context


def _show_context(payload) -> mock.MagicMock:
    """Fake one single-JSON probe response (/api/show, /api/tags)."""
    body = json.dumps(payload).encode("utf-8")
    response = mock.MagicMock()
    response.read.return_value = body
    context = mock.MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False
    return context


def _local_route(*, show, tags=None, chat=None, calls=None):
    """Route local transport by URL path; each spec is a payload or raises it."""
    behaviors = {
        "/api/show": _show_context(show) if not isinstance(show, Exception) else show,
        "/api/tags": (
            _show_context({"models": []} if tags is None else tags)
            if not isinstance(tags, Exception)
            else tags
        ),
        "/api/chat": (
            _stream_context([{"done": True}] if chat is None else chat)
            if not isinstance(chat, Exception)
            else chat
        ),
    }

    def _route(request, timeout=None, **kwargs):
        _ = (timeout, kwargs)
        url = (getattr(request, "full_url", "") or "").rstrip("/")
        for suffix, behavior in behaviors.items():
            if url.endswith(suffix):
                if calls is not None:
                    calls.append(suffix)
                if isinstance(behavior, Exception):
                    raise behavior
                return behavior
        raise AssertionError(f"unexpected local URL: {url}")

    return _route


def _chat_requests(opened) -> list:
    return [
        call.args[0]
        for call in opened.call_args_list
        if (getattr(call.args[0], "full_url", "") or "").rstrip("/").endswith("/api/chat")
    ]


def _chat_bodies(opened) -> list:
    return [json.loads(request.data.decode("utf-8")) for request in _chat_requests(opened)]
