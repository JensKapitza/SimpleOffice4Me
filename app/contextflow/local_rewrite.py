"""Local-only hint reformulation (P0-F).

The rule text always stays the fallback: only the ``local`` Ollama
provider may reformulate the static dashboard greeting, and every
inference failure (timeout, broken JSON, missing model, unreachable
backend) returns the rule text unchanged. Nothing here ever blocks
hints or domain actions, and no fact-bearing sentence is rewritten.
"""

from __future__ import annotations

from typing import Any

from . import vision
from .doc_tags import complete_text
from .vision import VisionError

RULE_GREETING = (
    "Der Engel klopft nur an, wenn wirklich etwas liegengeblieben ist – "
    "mit Quelle, Grund und genau einer freizugebenden Aktion."
)
REWRITE_MAX_CHARS = 280
REWRITE_NUM_PREDICT = 128
REWRITE_TIMEOUT = 8.0
REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}
REWRITE_PROMPT = (
    "Du bist der Bueroengel. Formuliere die Begruessung "
    "\"{text}\" in EINEM kurzen deutschen Satz neu (max. 280 Zeichen). "
    "Erfinde keine Fakten, Namen, Zahlen oder Termine. Antworte nur "
    "als JSON-Objekt {{\"text\": \"...\"}}."
)

_INFERENCE_ERRORS = (
    VisionError,
    PermissionError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
)


def rewrite_hint_lead(
    text: str,
    *,
    settings: dict[str, Any],
    root: object,
    timeout: float = REWRITE_TIMEOUT,
) -> tuple[str, bool]:
    """Reformulate the static greeting via local Ollama if configured.

    Returns (text, rewritten). ``rules_only`` (any provider other
    than ``local``) and every inference failure return the cleaned
    rule text with ``rewritten`` False — never an exception, never
    a mock answer, never a mutated fact.
    """
    clean = " ".join(str(text or "").split())
    if not clean:
        return "", False
    if str((settings or {}).get("provider", "local") or "local").lower() != "local":
        return clean, False
    try:
        provider = vision.select_provider(dict(settings or {}), False, root)
        provider.timeout = min(max(float(timeout or 0), 1.0), 30.0)
        payload = complete_text(
            provider,
            REWRITE_PROMPT.format(text=clean[:500]),
            json_schema=REWRITE_SCHEMA,
            num_predict=REWRITE_NUM_PREDICT,
        )
        candidate = " ".join(str(payload.get("text", "")).split())
    except _INFERENCE_ERRORS:
        return clean, False
    if not candidate or len(candidate) > REWRITE_MAX_CHARS:
        return clean, False
    return candidate, True
