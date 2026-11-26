"""One active, volatile, phone-approved PC filename-search session."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from uuid import uuid4

from .device_source_grants import GrantDenied, create_local_device_session

_PAIR_WINDOW = 120
_LEASE = 600
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[0-9a-f]{32}")
_MAX_RESULTS = 40


def folder_profile(key: str | bytes, owner: int) -> str:
    return hmac.new(
        key.encode("utf-8") if isinstance(key, str) else key,
        f"android-local-folder:{owner}".encode(), hashlib.sha256,
    ).hexdigest()


def require(valid: bool) -> None:
    if not valid:
        raise GrantDenied("PC folder access is not available")


@dataclass
class _Session:
    profile: str
    generation: str
    deadline: float
    principal: int = 0
    tab: str = ""
    requester: object = None
    owner: object = None
    challenge: str = ""
    fingerprint: str = ""
    device: str = ""
    source: str = ""
    adapter: object = None
    active_until: float = 0


class PairedFolderSession:
    """The owner controls are called only from a foreground native dialog, never HTTP."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._session: _Session | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paired-folder-saf")
        self._pending = None

    def phone_enable(self, profile: str, generation: str) -> None:
        require(bool(_FINGERPRINT.fullmatch(profile) and _ID.fullmatch(generation)))
        with self._lock:
            self._close()
            self._session = _Session(profile, generation, self._clock() + _PAIR_WINDOW)

    def _close(self) -> None:
        room = self._session
        self._session = None
        if room and room.device:
            room.owner.revoke_device(room.device)

    def phone_stop(self, generation: str) -> None:
        with self._lock:
            if self._session and hmac.compare_digest(self._session.generation, generation):
                self._close()

    def _phone_room(self, profile: str, generation: str) -> _Session:
        room = self._session
        require(bool(room and hmac.compare_digest(room.profile, profile)
                     and hmac.compare_digest(room.generation, generation)))
        return room

    def phone_status(self, profile: str, generation: str) -> dict:
        with self._lock:
            room = self._phone_room(profile, generation)
            return {
                "fingerprint": room.fingerprint if room.deadline > self._clock() or room.device else "",
                "paired": bool(room.device),
                "shared": bool(room.source and room.active_until > self._clock()),
            }

    def phone_pair(self, profile: str, generation: str, fingerprint: str) -> None:
        with self._lock:
            room = self._phone_room(profile, generation)
            require(not room.device and room.deadline > self._clock())
            paired = room.owner.approve_device(room.challenge, fingerprint)
            room.device = paired.id

    def phone_share(self, profile: str, generation: str, fingerprint: str, adapter) -> None:
        with self._lock:
            room = self._phone_room(profile, generation)
            require(bool(room.device and hmac.compare_digest(room.fingerprint, fingerprint)))
            self._unshare(room)
            room.source = uuid4().hex
            room.owner.update_source_availability(room.source, True)
            room.owner.grant_source(room.device, room.source, frozenset({"search"}), _LEASE)
            room.adapter = adapter
            room.active_until = self._clock() + _LEASE

    def phone_unshare(self, profile: str, generation: str, fingerprint: str) -> None:
        with self._lock:
            room = self._phone_room(profile, generation)
            require(bool(room.device and hmac.compare_digest(room.fingerprint, fingerprint)))
            self._unshare(room)

    @staticmethod
    def _unshare(room: _Session) -> None:
        if room.source:
            room.owner.remove_source(room.source)
            room.source, room.adapter, room.active_until = "", None, 0

    def _pc_room(self, profile: str, actor: int, tab: str) -> _Session:
        room = self._session
        require(bool(room and hmac.compare_digest(room.profile, profile)
                     and room.principal == actor and hmac.compare_digest(room.tab, tab)))
        return room

    def begin(self, profile: str, actor: int, tab: str, key: bytes):
        with self._lock:
            room = self._session
            require(bool(room and hmac.compare_digest(room.profile, profile)
                         and self._clock() < room.deadline and not room.challenge))
            requester, owner = create_local_device_session(owner_user_id=actor, clock=self._clock)
            challenge = requester.begin_pair(actor, key)
            room.requester, room.owner = requester, owner
            room.principal, room.tab = actor, tab
            room.challenge = challenge.id
            return challenge

    def prove(self, profile: str, actor: int, tab: str, challenge: str, signature: bytes) -> None:
        with self._lock:
            room = self._pc_room(profile, actor, tab)
            require(bool(room.deadline > self._clock() and hmac.compare_digest(room.challenge, challenge)))
            room.requester.prove_pair(actor, challenge, signature)
            room.fingerprint = room.owner.fingerprint_for(challenge)

    def pc_status(self, profile: str, actor: int, tab: str) -> str:
        with self._lock:
            room = self._pc_room(profile, actor, tab)
            require(bool(room.device or room.deadline > self._clock()))
            if not room.device:
                return "awaiting_pair"
            if room.source and room.active_until > self._clock():
                return "shared"
            return "awaiting_source"

    def start_read(self, profile: str, actor: int, tab: str, query: str):
        with self._lock:
            room = self._pc_room(profile, actor, tab)
            require(bool(room.source))
            return room.requester.prepare_access(actor, room.device, room.source, "search", query.encode())

    @staticmethod
    def _read_adapter(adapter, query: str) -> object:
        response = str(adapter.readNamesForPc(query))
        require(len(response) <= 32_768)
        return json.loads(response)

    def _provider_search(self, room: _Session, adapter, source: str, query: str) -> dict:
        with self._lock:
            require(self._session is room and room.source == source and room.active_until > self._clock())
            require(self._pending is None or self._pending.done())
            self._pending = task = self._pool.submit(self._read_adapter, adapter, query)
        try:
            data = task.result(timeout=3)
            require(isinstance(data, dict) and data.get("status") == "ready")
            names = data.get("names")
            require(isinstance(names, list) and len(names) <= _MAX_RESULTS and all(
                isinstance(name, str) and 0 < len(name) <= 160 and "/" not in name and "\\" not in name
                for name in names
            ))
            # Only display names leave this process. No path, parent, provider,
            # document handle, URI, bytes, or arbitrary provider-owned JSON.
            return {"names": names if query else [], "limited": data.get("limited") is True, "scope": source}
        except (TimeoutError, ValueError, TypeError, GrantDenied):
            with self._lock:
                if self._session is room and room.source == source:
                    self._unshare(room)
            raise GrantDenied("PC folder access is not available") from None

    def read(self, profile: str, actor: int, tab: str, challenge: str, signature: bytes, query: str) -> dict:
        with self._lock:
            room = self._pc_room(profile, actor, tab)
            source, adapter = room.source, room.adapter
            require(bool(source and adapter))
            payload = query.encode()
            permit = room.requester.verify_access(actor, challenge, signature, payload)
        return room.requester.deliver(actor, permit, payload, lambda: self._provider_search(room, adapter, source, query))


def phone_call(action: str, profile: str, generation: str, fingerprint: str = "", adapter=None) -> str:
    """Only called by the Chaquopy native Activity. Not a browser binding."""
    try:
        if action == "enable":
            paired_folder_session.phone_enable(profile, generation)
        elif action == "status":
            return json.dumps(paired_folder_session.phone_status(profile, generation))
        elif action == "pair":
            paired_folder_session.phone_pair(profile, generation, fingerprint)
        elif action == "share" and adapter is not None:
            paired_folder_session.phone_share(profile, generation, fingerprint, adapter)
        elif action == "unshare":
            paired_folder_session.phone_unshare(profile, generation, fingerprint)
        elif action == "stop":
            paired_folder_session.phone_stop(generation)
        else:
            return '{}'
    except (GrantDenied, AttributeError):
        return '{}'
    return '{"ok":true}'


paired_folder_session = PairedFolderSession()
