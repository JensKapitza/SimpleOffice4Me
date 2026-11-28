"""Short-lived in-process approval records for native SMS import.

Only a snapshot digest/count is retained, never text, sender IDs, or messages.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from secrets import token_hex
from threading import Lock
from time import monotonic

_TTL = 300
_LIMIT = 64
_lock = Lock()
_pending = OrderedDict()


@dataclass(frozen=True)
class Preview:
    owner: str
    root: str
    session: str
    period: tuple[int, int]
    digest: str
    new: int
    expires: float


def _clean(now: float) -> None:
    for key in list(_pending):
        if _pending[key].expires <= now:
            del _pending[key]


def issue(owner: str, root: str, session: str, period: tuple[int, int], digest: str, new: int) -> str:
    now = monotonic()
    sid = sha256(session.encode()).hexdigest()
    with _lock:
        _clean(now)
        for key, entry in list(_pending.items()):
            if (entry.owner, entry.root, entry.session) == (owner, root, sid):
                del _pending[key]
        if len(_pending) >= _LIMIT:
            _pending.popitem(last=False)
        key = token_hex(16)
        _pending[key] = Preview(owner, root, sid, period, digest, new, now + _TTL)
        return key


def redeem(key: str, owner: str, root: str, session: str, period: tuple[int, int]) -> Preview | None:
    sid = sha256(session.encode()).hexdigest()
    with _lock:
        _clean(monotonic())
        entry = _pending.get(key)
        if entry is None or (entry.owner, entry.root, entry.session, entry.period) != (owner, root, sid, period):
            return None
        return _pending.pop(key)
