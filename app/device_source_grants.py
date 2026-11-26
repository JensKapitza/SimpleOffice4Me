"""In-process paired-browser permission state. No HTTP or provider access here.

A future transport must supply the current authenticated user, and bind the
owner port exclusively to a verified, interactive native Android session.
This module has neither a source provider nor network endpoints. State is
deliberately volatile: restarting disables all pairings and outstanding grants.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, TypeVar

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


_PROTOCOL = "simpleoffice-local-device-sources-v1"
_ACTIONS = frozenset({"search"})
_PAIR_SECONDS = 120
_CHALLENGE_SECONDS = 15
_PERMIT_SECONDS = 5
_MAX_GRANT_SECONDS = 600
_MAX_PENDING = 64
_MAX_DEVICES = 12
_MAX_SOURCES = 24
_MAX_GRANTS = 48
T = TypeVar("T")


class GrantDenied(PermissionError):
    """Intentionally non-descriptive across the untrusted requester boundary."""


@dataclass(frozen=True)
class Challenge:
    id: str
    message: bytes


@dataclass(frozen=True)
class PairedDevice:
    id: str
    fingerprint: str


@dataclass(frozen=True)
class _Pairing:
    key: ec.EllipticCurvePublicKey
    fingerprint: str
    challenge: Challenge
    expires: float
    verified: bool = False


@dataclass(frozen=True)
class _ConfirmedKey:
    fingerprint: str
    key: ec.EllipticCurvePublicKey


@dataclass(frozen=True)
class _Grant:
    actions: frozenset[str]
    expires: float
    revision: str


@dataclass(frozen=True)
class _Source:
    available: bool
    revision: str


@dataclass(frozen=True)
class _Permit:
    id: str
    device: str
    source: str
    action: str
    request_digest: str
    revision: str
    source_revision: str
    expires: float


@dataclass(frozen=True)
class _PendingRead:
    challenge: Challenge
    permit: _Permit
    expires: float


def _id() -> str:
    return uuid.uuid4().hex


def _opaque_id(value: str) -> bool:
    return (isinstance(value, str) and len(value) == 32
            and all(char in "0123456789abcdef" for char in value))


def _deny_unless(valid: bool) -> None:
    if not valid:
        raise GrantDenied("Device source access was not granted")


def _verify(key: ec.EllipticCurvePublicKey, signature: bytes, message: bytes) -> None:
    # Browser WebCrypto ECDSA uses the 64-byte r||s form. Do not invent or
    # process permissive signature formats at this boundary.
    _deny_unless(isinstance(signature, bytes) and len(signature) == 64)
    raw = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
    try:
        key.verify(raw, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise GrantDenied("Device source access was not granted") from exc


def _request_digest(request_payload: bytes) -> str:
    _deny_unless(isinstance(request_payload, bytes) and len(request_payload) <= 4096)
    return hashlib.sha256(request_payload).hexdigest()


class DeviceSourceRequester:
    """Requester-only side. Owner methods are not accessible via this facade."""

    def __init__(self, state: _State):
        self._state = state

    def begin_pair(self, actor: int, public_key: bytes) -> Challenge:
        return self._state.begin_pair(actor, public_key)

    def prove_pair(self, actor: int, challenge_id: str, signature: bytes) -> None:
        self._state.prove_pair(actor, challenge_id, signature)

    def prepare_access(self, actor: int, device: str, source: str, action: str, request_payload: bytes) -> Challenge:
        return self._state.prepare_access(actor, device, source, action, request_payload)

    def verify_access(self, actor: int, challenge_id: str, signature: bytes, request_payload: bytes) -> _Permit:
        return self._state.verify_access(actor, challenge_id, signature, request_payload)

    def deliver(self, actor: int, permit: _Permit, request_payload: bytes, load: Callable[[], T]) -> T:
        return self._state.deliver(actor, permit, request_payload, load)


class NativeOwnerApproval:
    """Local-only authority. Never pass to a request handler or serialize it."""

    def __init__(self, state: _State):
        self._state = state

    def fingerprint_for(self, challenge_id: str) -> str:
        return self._state.fingerprint_for(challenge_id)

    def approve_device(self, challenge_id: str, displayed_fingerprint: str) -> PairedDevice:
        return self._state.approve_device(challenge_id, displayed_fingerprint)

    def update_source_availability(self, source: str, available: bool) -> None:
        """Only the trusted native adapter may report a currently selected source."""
        self._state.update_source_availability(source, available)

    def grant_source(self, device: str, source: str, actions: frozenset[str], expires_in: int) -> None:
        self._state.grant_source(device, source, actions, expires_in)

    def revoke_source(self, device: str, source: str) -> None:
        self._state.revoke_source(device, source)

    def revoke_device(self, device: str) -> None:
        self._state.revoke_device(device)

    def remove_source(self, source: str) -> None:
        """Call on local removal, mismatch of grant/version, or observed SAF revoke."""
        self._state.remove_source(source)


def create_local_device_session(
    *, owner_user_id: int, clock: Callable[[], float] = time.monotonic,
) -> tuple[DeviceSourceRequester, NativeOwnerApproval]:
    """Build separate facades in a trusted native/session integration, not HTTP."""
    if type(owner_user_id) is not int or owner_user_id < 1:
        raise ValueError("A trusted local account is required")
    state = _State(owner_user_id, clock)
    return DeviceSourceRequester(state), NativeOwnerApproval(state)


class _State:
    def __init__(self, owner: int, clock: Callable[[], float]):
        self.owner = owner
        self.clock = clock
        self.instance = _id()
        self.lock = threading.RLock()
        self.pairings: dict[str, _Pairing] = {}
        self.devices: dict[str, _ConfirmedKey] = {}
        self.sources: dict[str, _Source] = {}
        self.grants: dict[tuple[str, str], _Grant] = {}
        self.pending_reads: dict[str, _PendingRead] = {}
        self.permits: dict[str, _Permit] = {}

    def check_actor(self, actor: int) -> None:
        # actor must come from the authenticated transport session, never a form field.
        _deny_unless(type(actor) is int and actor == self.owner)

    def challenge(self, purpose: str, **binding: str) -> Challenge:
        id_ = _id()
        transcript = {
            "protocol": _PROTOCOL, "audience": self.instance, "purpose": purpose, "challenge": id_,
            "nonce": secrets.token_hex(32), "user": self.owner, **binding,
        }
        return Challenge(id_, json.dumps(transcript, separators=(",", ":"), sort_keys=True).encode("ascii"))

    def prune(self) -> None:
        now = self.clock()
        for collection in (self.pairings, self.grants, self.pending_reads, self.permits):
            for key in [key for key, value in collection.items() if value.expires <= now]:
                del collection[key]

    def begin_pair(self, actor: int, public_key: bytes) -> Challenge:
        self.check_actor(actor)
        _deny_unless(isinstance(public_key, bytes) and len(public_key) == 65 and public_key[0] == 4)
        try:
            key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
        except ValueError as exc:
            raise GrantDenied("Device source access was not granted") from exc
        fingerprint = hashlib.sha256(public_key).hexdigest()
        with self.lock:
            self.prune()
            _deny_unless(len(self.pairings) < _MAX_PENDING)
            challenge = self.challenge("pair", fingerprint=fingerprint)
            self.pairings[challenge.id] = _Pairing(key, fingerprint, challenge, self.clock() + _PAIR_SECONDS)
            return challenge

    def prove_pair(self, actor: int, challenge_id: str, signature: bytes) -> None:
        self.check_actor(actor)
        with self.lock:
            self.prune()
            row = self.pairings.get(challenge_id) if _opaque_id(challenge_id) else None
            _deny_unless(row is not None and not row.verified)
            assert row is not None
            _verify(row.key, signature, row.challenge.message)
            self.pairings[challenge_id] = _Pairing(row.key, row.fingerprint, row.challenge, row.expires, True)

    def fingerprint_for(self, challenge_id: str) -> str:
        with self.lock:
            self.prune()
            row = self.pairings.get(challenge_id) if _opaque_id(challenge_id) else None
            _deny_unless(row is not None and row.verified)
            assert row is not None
            return row.fingerprint

    def approve_device(self, challenge_id: str, shown: str) -> PairedDevice:
        with self.lock:
            fingerprint = self.fingerprint_for(challenge_id)
            _deny_unless(isinstance(shown, str) and shown.isascii() and hmac.compare_digest(shown, fingerprint))
            _deny_unless(len(self.devices) < _MAX_DEVICES and not any(
                row.fingerprint == fingerprint for row in self.devices.values()
            ))
            device = PairedDevice(_id(), fingerprint)
            proven = self.pairings.pop(challenge_id)
            self.devices[device.id] = _ConfirmedKey(fingerprint, proven.key)
            return device

    def update_source_availability(self, source: str, available: bool) -> None:
        _deny_unless(_opaque_id(source) and type(available) is bool)
        with self.lock:
            previous = self.sources.get(source)
            if previous is not None and previous.available == available:
                return
            if previous is None:
                _deny_unless(available and len(self.sources) < _MAX_SOURCES)
            self.sources[source] = _Source(available, _id())

    def grant_source(self, device: str, source: str, actions: frozenset[str], expires_in: int) -> None:
        valid = (type(expires_in) is int and 1 <= expires_in <= _MAX_GRANT_SECONDS
                 and isinstance(actions, frozenset) and bool(actions) and actions <= _ACTIONS)
        _deny_unless(valid and _opaque_id(device) and _opaque_id(source))
        with self.lock:
            self.prune()
            key = (device, source)
            selected = self.sources.get(source)
            _deny_unless(selected is not None and selected.available and device in self.devices
                         and (key in self.grants or len(self.grants) < _MAX_GRANTS))
            self.grants[key] = _Grant(actions, self.clock() + expires_in, _id())

    def revoke_source(self, device: str, source: str) -> None:
        _deny_unless(_opaque_id(device) and _opaque_id(source))
        with self.lock:
            self.grants.pop((device, source), None)

    def revoke_device(self, device: str) -> None:
        _deny_unless(_opaque_id(device))
        with self.lock:
            self.devices.pop(device, None)
            for key in [key for key in self.grants if key[0] == device]:
                del self.grants[key]

    def remove_source(self, source: str) -> None:
        _deny_unless(_opaque_id(source))
        with self.lock:
            self.sources.pop(source, None)
            for key in [key for key in self.grants if key[1] == source]:
                del self.grants[key]

    def current_grant(self, device: str, source: str, action: str) -> _Grant:
        valid = _opaque_id(device) and _opaque_id(source) and isinstance(action, str) and action in _ACTIONS
        _deny_unless(valid and device in self.devices)
        selected = self.sources.get(source)
        _deny_unless(selected is not None and selected.available)
        grant = self.grants.get((device, source))
        _deny_unless(grant is not None and grant.expires > self.clock() and action in grant.actions)
        assert grant is not None
        return grant

    def prepare_access(self, actor: int, device: str, source: str, action: str, payload: bytes) -> Challenge:
        self.check_actor(actor)
        digest = _request_digest(payload)
        with self.lock:
            self.prune()
            grant = self.current_grant(device, source, action)
            _deny_unless(len(self.pending_reads) < _MAX_PENDING)
            source_revision = self.sources[source].revision
            challenge = self.challenge(
                "read", device=device, source=source, action=action, digest=digest,
                revision=grant.revision, source_revision=source_revision,
            )
            token = _Permit(challenge.id, device, source, action, digest, grant.revision, source_revision, grant.expires)
            self.pending_reads[challenge.id] = _PendingRead(
                challenge, token, min(grant.expires, self.clock() + _CHALLENGE_SECONDS),
            )
            return challenge

    def verify_access(self, actor: int, challenge_id: str, signature: bytes, payload: bytes) -> _Permit:
        self.check_actor(actor)
        digest = _request_digest(payload)
        _deny_unless(_opaque_id(challenge_id))
        with self.lock:
            self.prune()
            pending = self.pending_reads.pop(challenge_id, None)
            _deny_unless(pending is not None and len(self.permits) < _MAX_PENDING)
            assert pending is not None
            saved = pending.permit
            current = self.current_grant(saved.device, saved.source, saved.action)
            _deny_unless(current.revision == saved.revision and self.sources[saved.source].revision == saved.source_revision
                         and hmac.compare_digest(saved.request_digest, digest))
            _verify(self.devices[saved.device].key, signature, pending.challenge.message)
            permit = _Permit(saved.id, saved.device, saved.source, saved.action, digest, saved.revision,
                             saved.source_revision, min(current.expires, self.clock() + _PERMIT_SECONDS))
            self.permits[permit.id] = permit
            return permit

    def check_permit(self, permit: _Permit, digest: str) -> None:
        grant = self.current_grant(permit.device, permit.source, permit.action)
        _deny_unless(permit.expires > self.clock() and grant.revision == permit.revision
                     and self.sources[permit.source].revision == permit.source_revision
                     and hmac.compare_digest(permit.request_digest, digest))

    def deliver(self, actor: int, permit: _Permit, payload: bytes, load: Callable[[], T]) -> T:
        self.check_actor(actor)
        digest = _request_digest(payload)
        _deny_unless(isinstance(permit, _Permit))
        with self.lock:
            _deny_unless(self.permits.pop(permit.id, None) is permit)
            self.check_permit(permit, digest)
        result = load()
        # An in-flight result must not survive a revoked/replaced/expired grant.
        # A real provider must additionally recheck local SAF access and avoid
        # prefilled cross-device caches. Streaming/response delivery is not implemented.
        with self.lock:
            self.check_permit(permit, digest)
            return result
