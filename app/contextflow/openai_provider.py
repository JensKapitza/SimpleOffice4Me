"""OpenAI and compatible inference with free model IDs and explicit data consent.

Native OpenAI uses its fixed official endpoint. Compatible mode uses validated
URLs, pinned DNS and verified TLS. Secrets never appear in public errors.
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


def require_openai_model(model: object) -> str:
    """Validate a free case-sensitive model identifier, not a vendor allowlist."""
    import re

    configured = str(model or "").strip()
    if len(configured) > 160 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*", configured):
        raise VisionError("Anbieter-Modell-ID ist leer oder ungültig.")
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
    except (ValueError, RecursionError) as exc:
        raise VisionError(
            f"OpenAI inference returned no suggestion JSON ({exc})"
        ) from None
    if not isinstance(parsed, dict):
        raise VisionError("OpenAI inference returned no suggestion JSON")
    return parsed


def check_openai_model(api_key: str, timeout: float = META_MODELS_TIMEOUT, *,
                       model: str = "gpt-4o-mini", endpoint: str = OPENAI_API_ENDPOINT) -> tuple[bool, str]:
    """Check the configured exact ID; never accept a different listed model."""
    from .provider_transport import request_json
    try:
        expected = require_openai_model(model)
        payload = request_json(endpoint, api_key, "/models", None, timeout)
    except (VisionError, ValueError):
        return False, "Modelliste nicht erreichbar oder Konfiguration ungültig"
    entries = payload.get("data")
    if not isinstance(entries, list):
        return False, "ungültige Modelliste"
    listed = any(isinstance(entry, dict) and entry.get("id") == expected for entry in entries)
    return listed, "" if listed else "Modell nicht gelistet"


class OpenAIVisionProvider(VisionProvider):
    """One shared Chat Completions adapter; external data needs explicit consent.

    Compatible mode also supports anonymous loopback services. Structured-result
    validation stays with callers; protocol support is not a live model test.
    """

    name = "openai"
    label = "OpenAI ChatGPT (chat/completions)"

    def __init__(self, api_key: str, endpoint: str, model: str, *, compatible: bool = False) -> None:
        from .provider_transport import is_local_endpoint, validate_endpoint

        clean = validate_endpoint(endpoint)
        if not compatible and clean != OPENAI_API_ENDPOINT:
            raise VisionError("OpenAI API endpoint is not the official endpoint")
        if not api_key and not (compatible and is_local_endpoint(clean)):
            raise PermissionError("OpenAI inference needs an API key")
        if not isinstance(api_key, str) or len(api_key) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in api_key):
            raise VisionError("Anbieter-Key ist ungültig.")
        self.api_key = api_key
        self.endpoint = clean
        self.model = require_openai_model(model)
        self.compatible = compatible
        if compatible:
            self.label = "OpenAI-kompatibles Modell (chat/completions)"

    def _post_chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        value = {"model": self.model, "messages": messages}
        if not self.compatible:
            value["response_format"] = {"type": "json_object"}
        if self.compatible:
            from .provider_transport import request_json
            return _openai_content(request_json(self.endpoint, self.api_key, "/chat/completions", value))
        body = json.dumps(value).encode("utf-8")
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
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise VisionError("OpenAI inference failed; endpoint, credentials and service status prüfen.") from None
        if len(raw) > OPENAI_MAX_RESPONSE_BYTES:
            raise VisionError("OpenAI inference returned too much data")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError) as exc:
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
