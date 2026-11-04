"""OpenAI cloud inference for ContextFlow (synthetic demo roots only).

Third-party cloud path mirroring the Meta provider's discipline: pinned
official endpoint, explicit model allowlist (fail-closed), key only in
the Authorization header, no redirects, bounded reads, and errors that
never carry the key. Gating (consent + verified synthetic demo root)
stays with :func:`app.contextflow.vision.select_provider`.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Any

from .vision import (
    META_MODELS_TIMEOUT,
    VisionError,
    VisionProvider,
    _NoRedirectHandler,
)

OPENAI_API_ENDPOINT = "https://api.openai.com/v1"
OPENAI_MODELS_ENDPOINT = "https://api.openai.com/v1/models"
OPENAI_CHAT_COMPLETIONS_PATH = "/chat/completions"
OPENAI_CALL_TIMEOUT = 60.0
OPENAI_MAX_RESPONSE_BYTES = 256 * 1024
# Deliberately exact OpenAI IDs. Custom aliases could point at a
# different (billable) model while claiming a cheap name, so inference
# fails closed — same policy as META_OLLAMA_MODELS in vision.py.
OPENAI_MODELS = frozenset({"gpt-4o-mini", "gpt-4o"})


def require_openai_model(model: object) -> str:
    """Accept only explicitly supported OpenAI model IDs."""
    configured = str(model or "").strip().casefold()
    if configured not in OPENAI_MODELS:
        raise VisionError(
            f"Das OpenAI-Modell '{configured}' ist nicht freigegeben. "
            "ContextFlow erlaubt für OpenAI ausschließlich: "
            + ", ".join(sorted(OPENAI_MODELS)) + "."
        )
    return configured


def _openai_content(payload: Any) -> dict[str, Any]:
    """Extract and parse the first message.content JSON object."""
    if not isinstance(payload, dict):
        raise VisionError("OpenAI inference returned no suggestion JSON")
    error = payload.get("error") if isinstance(payload.get("error"), dict) else None
    if error is not None:
        raise VisionError("OpenAI inference did not complete (Fehlerantwort)")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise VisionError("OpenAI inference returned no suggestion JSON")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise VisionError("OpenAI inference returned no suggestion JSON")
    try:
        parsed = json.loads(content)
    except ValueError as exc:
        raise VisionError(
            f"OpenAI inference returned no suggestion JSON ({exc})"
        ) from None
    if not isinstance(parsed, dict):
        raise VisionError("OpenAI inference returned no suggestion JSON")
    return parsed


def check_openai_model(api_key: str, timeout: float = META_MODELS_TIMEOUT) -> tuple[bool, str]:
    """Check via GET /v1/models that a supported OpenAI model is listed.

    Returns (listed, short error). The key travels only in the
    Authorization header and never appears in errors, logs, or files.
    """
    if not api_key:
        return (False, "kein API-Schlüssel")
    request = urllib.request.Request(
        OPENAI_MODELS_ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except TimeoutError:
        return (False, "Zeitüberschreitung")
    except (urllib.error.URLError, OSError, ValueError):
        return (False, "Modelliste nicht erreichbar")
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return (False, "ungültige Modelliste")
    listed = any(
        isinstance(entry, dict) and str(entry.get("id", "")).strip().casefold() in OPENAI_MODELS
        for entry in entries
    )
    return (listed, "" if listed else "Modell nicht gelistet")


class OpenAIVisionProvider(VisionProvider):
    """OpenAI cloud inference: synthetic demo roots only (third-party).

    Speaks the OpenAI Chat Completions endpoint (``POST
    /v1/chat/completions``) with an explicitly allowlisted model
    (``gpt-4o-mini`` default). Like the Meta cloud path, this provider
    never runs on real roots: content sent to a third party could be
    used beyond the request, so even text-only calls require a verified
    synthetic demo root. The key travels only in the Authorization
    header; errors never carry the key, URLs with credentials, or
    tracebacks.
    """

    name = "openai"
    label = "OpenAI ChatGPT (chat/completions)"

    def __init__(self, api_key: str, endpoint: str, model: str) -> None:
        if not api_key:
            raise PermissionError("OpenAI inference needs an API key")
        if str(endpoint or "").strip() != OPENAI_API_ENDPOINT:
            raise VisionError("OpenAI API endpoint is not the official endpoint")
        self.api_key = api_key
        self.endpoint = OPENAI_API_ENDPOINT
        self.model = require_openai_model(model)

    def _post_chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint + OPENAI_CHAT_COMPLETIONS_PATH,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=OPENAI_CALL_TIMEOUT) as response:
                raw = response.read(OPENAI_MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise VisionError(f"OpenAI inference failed ({exc})") from None
        if len(raw) > OPENAI_MAX_RESPONSE_BYTES:
            raise VisionError("OpenAI inference returned too much data")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise VisionError(
                f"OpenAI inference returned no suggestion JSON ({exc})"
            ) from None
        return _openai_content(payload)

    def suggest(self, image: bytes, mime: str, context: dict[str, str]) -> dict[str, Any]:
        prompt = context.get("instruction") or (
            "Analysiere das Foto als Büro-Service-Befund. Antworte NUR mit "
            "einem JSON-Objekt mit den Schlüsseln title (max 240 Zeichen), "
            "description, evidence_class (observed|measured|derived|risk|"
            "unknown), confidence (0-100). Deutsch. "
            f"Kontext: {context.get('visit_title', '')} / "
            f"{context.get('asset_name', 'ohne Gerätebezug')}."
        )
        return self._post_chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    "data:" + mime + ";base64,"
                                    + base64.b64encode(image).decode("ascii")
                                )
                            },
                        },
                    ],
                }
            ]
        )

    def complete_text(self, prompt: str) -> dict[str, Any]:
        """Run one text-only JSON completion via the OpenAI Chat Completions API.

        Sends only the given prompt text (callers pass OCR text plus
        pasted user text; photo bytes never reach this call). The
        answer is parsed from the first message.content block and must
        be a JSON object; schema validation stays with the caller.
        """
        return self._post_chat(
            [{"role": "user", "content": [{"type": "text", "text": str(prompt)}]}]
        )
