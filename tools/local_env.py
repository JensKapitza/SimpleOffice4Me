"""Load selected keys from a parent-folder .env file (local starts only).

The file lives NEXT TO the checkout (``../.env`` relative to the repo
root), never inside it, and is not tracked by git. Only allowlisted
keys are loaded, only when unset in the environment (existing values
always win), and empty values are ignored. The loader never prints or
logs anything, so secrets cannot leak into console output or files.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PARENT_ENV_FILE = REPO_ROOT.parent / ".env"
DEFAULT_KEYS = ("MODEL_API_KEY",)
MAX_ENV_BYTES = 64 * 1024
_KEY_RE = re.compile(r"[A-Z_][A-Z0-9_]*\Z")


def _parse_line(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        return None
    key, _, value = text.partition("=")
    key = key.strip()
    value = value.strip()
    if not _KEY_RE.match(key):
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if not value:
        return None
    return (key, value)


def load_parent_env_file(
    path: str | Path | None = None,
    keys: tuple[str, ...] = DEFAULT_KEYS,
) -> tuple[str, ...]:
    """Load allowlisted keys from the parent .env into os.environ.

    Returns the loaded key names (never values). Missing or
    unreadable files are a silent no-op.
    """
    env_path = Path(path) if path is not None else PARENT_ENV_FILE
    try:
        if not env_path.is_file() or env_path.stat().st_size > MAX_ENV_BYTES:
            return ()
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return ()
    allowed = set(keys)
    loaded: list[str] = []
    for line in text.splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if key not in allowed:
            continue
        if key in os.environ:
            continue
        os.environ[key] = value
        loaded.append(key)
    return tuple(loaded)
