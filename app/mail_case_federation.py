"""Federated transport for collaborative mail cases over the v3 envelope contract.

The mail account owner remains authoritative. Remote users never receive IMAP or
SMTP credentials. A receiving instance maps the remote participant identity to
one active local user explicitly through the federation administration.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any, Callable, Mapping
import uuid

from .federation_worker import _json_request, _request
from .mail_archive_preview import load_local_eml_bytes_by_id
from .mail_case_store import MailCaseStore
from .mail_client import MailStore, MAX_MESSAGE_BYTES
from .v3_federation import (
    ENVELOPE_VERSION,
    FederationContract,
    FederationEnvelope,
    negotiate,
)


_OBJECT_TYPE = "mail_cases"
_SCHEMA_VERSION = 1
_CONTENT_REF = re.compile(r"^/federation/v3/mail-cases/content/[A-Za-z0-9_-]{20,96}$")
_SHA512_REF = re.compile(r"^sha512:([0-9a-f]{128})$")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MailCaseFederation:
    """Bridge MailCaseStore operations through the existing v3 federation contract."""

    def __init__(self, root: str | Path, local_peer_id: str, *, history=None):
        self.root = Path(root).expanduser().resolve()
        self.contract = FederationContract(self.root, local_peer_id)
        self.cases = MailCaseStore(self.root, history=history)

    @staticmethod
    def _policy(peer: Mapping[str, Any]) -> dict[str, Any]:
        policy = peer.get("policy")
        if not isinstance(policy, dict):
            return {}
        classes = policy.get("data_classes")
        if isinstance(classes, dict) and isinstance(classes.get(_OBJECT_TYPE), dict):
            return dict(classes[_OBJECT_TYPE])
        direct = policy.get(_OBJECT_TYPE)
        return dict(direct) if isinstance(direct, dict) else {}

    def _peer_for_send(self, peer_id: str) -> dict[str, Any]:
        peer = self.contract.peers.get_peer(peer_id)
        if not peer or not peer.get("enabled"):
            raise ValueError("Federation peer is not active")
        if not self.contract._directly_trusted(peer_id):
            raise PermissionError("direct federation trust is required")
        if self._policy(peer).get("send") is not True:
            raise PermissionError("peer policy does not allow mail-case sending")
        return peer

    def _ensure_remote_capability(self, peer: dict[str, Any]) -> None:
        peer_id = str(peer["peer_id"])
        descriptor = self.contract.store.peer_capabilities(peer_id)
        negotiated = negotiate(descriptor)
        if _SCHEMA_VERSION in negotiated.get("objects", {}).get(_OBJECT_TYPE, []):
            return
        descriptor = _json_request(
            str(peer["base_url"]).rstrip("/") + "/federation/v3/capabilities",
            token=self.contract.peers.peer_token(peer_id),
            timeout=15,
        )
        negotiated = self.contract.store.remember_capabilities(peer_id, descriptor)
        if (
            negotiated.get("envelope_version") != ENVELOPE_VERSION
            or _SCHEMA_VERSION not in negotiated.get("objects", {}).get(_OBJECT_TYPE, [])
        ):
            raise ValueError("peer does not support compatible mail-case federation")

    def _send(
        self,
        peer_id: str,
        payload: dict[str, Any],
        *,
        object_ref: str,
    ) -> dict[str, Any]:
        peer = self._peer_for_send(peer_id)
        self._ensure_remote_capability(peer)
        envelope = FederationEnvelope(
            message_id=f"mailcase-{uuid.uuid4().hex}",
            sender_instance=self.contract.local_peer_id,
            recipient_instance=str(peer["peer_id"]),
            object_type=_OBJECT_TYPE,
            schema_version=_SCHEMA_VERSION,
            occurred_at=_utc(),
            object_ref=str(object_ref)[:240],
            payload=dict(payload),
        )
        # Re-parse to run all shared envelope bounds and syntax checks before IO.
        envelope = FederationEnvelope.from_mapping(envelope.to_mapping())
        try:
            result = _json_request(
                str(peer["base_url"]).rstrip("/") + "/federation/v3/receive",
                method="POST",
                token=self.contract.peers.peer_token(str(peer["peer_id"])),
                payload=envelope.to_mapping(),
                timeout=30,
            )
            self.contract.peers.set_peer_health(str(peer["peer_id"]), seen=True)
            self.contract.peers.record_event(
                "mail_case_v3_sent",
                peer_id=str(peer["peer_id"]),
                detail={
                    "message_id": envelope.message_id,
                    "operation": str(payload.get("operation") or "")[:80],
                    "status": str(result.get("status") or "")[:40],
                },
            )
            return result
        except Exception as exc:
            self.contract.peers.set_peer_health(
                str(peer["peer_id"]), error=type(exc).__name__
            )
            raise

    def snapshot_for(
        self,
        case_id: str,
        peer_id: str,
        remote_user_id: str,
    ) -> dict[str, Any]:
        case = self.cases.federated_case(peer_id, remote_user_id, case_id)
        messages: list[dict[str, Any]] = []
        for item in case["messages"]:
            row = {
                "mail_reference": item["mail_reference"],
                "direction": item["direction"],
                "message_id": item.get("message_id", ""),
                "in_reply_to": item.get("in_reply_to", ""),
                "references": list(item.get("references") or ())[:100],
                "created_at": item.get("created_at", ""),
            }
            if _SHA512_REF.fullmatch(str(item["mail_reference"])):
                token = self.contract.store.mail_content_grant(
                    case_id,
                    str(item["mail_reference"]),
                    peer_id,
                    remote_user_id,
                )
                row["content_ref"] = (
                    f"/federation/v3/mail-cases/content/{token}"
                )
            messages.append(row)
        drafts = [
            {
                key: item.get(key, "")
                for key in (
                    "id",
                    "author",
                    "sender_identity",
                    "recipients_to",
                    "recipients_cc",
                    "recipients_bcc",
                    "subject",
                    "body",
                    "status",
                    "created_at",
                    "updated_at",
                )
            }
            for item in case["drafts"]
        ]
        comments = [
            {
                key: item.get(key, "")
                for key in ("id", "author", "body", "created_at", "updated_at")
            }
            for item in case["comments"]
        ]
        return {
            "case_id": case_id,
            "title": case["title"],
            "status": case["status"],
            "permissions": list(case["permissions"]),
            "messages": messages,
            "comments": comments,
            "drafts": drafts,
        }

    def send_snapshot(
        self,
        case_id: str,
        peer_id: str,
        remote_user_id: str,
    ) -> dict[str, Any]:
        snapshot = self.snapshot_for(case_id, peer_id, remote_user_id)
        return self._send(
            peer_id,
            {
                "operation": "snapshot",
                "recipient_user_id": remote_user_id,
                "snapshot": snapshot,
            },
            object_ref=f"mail-case:{case_id}",
        )

    def send_revoke(
        self,
        case_id: str,
        peer_id: str,
        remote_user_id: str,
    ) -> dict[str, Any]:
        return self._send(
            peer_id,
            {
                "operation": "revoke",
                "recipient_user_id": remote_user_id,
                "remote_case_id": case_id,
            },
            object_ref=f"mail-case:{case_id}",
        )

    def send_snapshots_for_case(self, actor: str, case_id: str) -> list[dict[str, Any]]:
        case = self.cases.get_case(actor, case_id)
        results: list[dict[str, Any]] = []
        for participant in case["participants"]:
            if participant.get("participant_type") != "federated_user":
                continue
            results.append(
                self.send_snapshot(
                    case_id,
                    str(participant.get("peer_id") or ""),
                    str(participant.get("remote_user_id") or ""),
                )
            )
        return results

    def remote_action(
        self,
        local_user: str,
        local_case_id: str,
        operation: str,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        origin = self.cases.federated_origin(local_user, local_case_id)
        if origin is None:
            raise ValueError("mail case is not a federated mirror")
        peer_id = str(origin["peer_id"])
        remote_user_id = self.contract.store.remote_mail_user(peer_id, local_user)
        if not remote_user_id:
            raise PermissionError("no active federation user mapping")
        payload = {
            "operation": str(operation),
            "case_id": str(origin["case_id"]),
            "actor_user_id": remote_user_id,
            **dict(values),
        }
        result = self._send(
            peer_id,
            payload,
            object_ref=f"mail-case:{origin['case_id']}",
        )
        if result.get("status") not in {"accepted", "duplicate"}:
            raise PermissionError(
                f"remote mail-case operation was not applied ({result.get('status') or 'unknown'})"
            )
        if not isinstance(result.get("application"), dict):
            raise ValueError("remote mail-case operation has no application result")
        return dict(result["application"])

    def apply(
        self,
        envelope: FederationEnvelope,
        *,
        active_user: Callable[[str], bool],
    ) -> dict[str, Any]:
        if envelope.object_type != _OBJECT_TYPE or envelope.schema_version != _SCHEMA_VERSION:
            raise ValueError("unsupported mail-case envelope")
        existing = self.contract.store.application_result(envelope.message_id)
        if existing is not None:
            return existing
        payload = dict(envelope.payload or {})
        operation = str(payload.get("operation") or "")
        result: dict[str, Any]
        if operation == "snapshot":
            remote_user_id = str(payload.get("recipient_user_id") or "").strip()
            mapping = self.contract.store.mail_user_mapping(
                envelope.sender_instance, remote_user_id
            )
            if not mapping or not active_user(str(mapping["local_username"])):
                raise PermissionError("federated mail user is not mapped to an active local user")
            snapshot = payload.get("snapshot")
            if not isinstance(snapshot, dict):
                raise ValueError("invalid mail-case snapshot")
            local_case_id = self.cases.upsert_federated_snapshot(
                str(mapping["local_username"]),
                envelope.sender_instance,
                remote_user_id,
                snapshot,
            )
            result = {
                "operation": operation,
                "local_case_id": local_case_id,
                "remote_case_id": str(snapshot.get("case_id") or ""),
            }
        elif operation == "revoke":
            remote_user_id = str(payload.get("recipient_user_id") or "").strip()
            remote_case_id = str(payload.get("remote_case_id") or "").strip()
            mapping = self.contract.store.mail_user_mapping(
                envelope.sender_instance, remote_user_id
            )
            if not mapping or not remote_case_id:
                raise PermissionError("federated mail user mapping is unavailable")
            removed = self.cases.revoke_federated_snapshot(
                str(mapping["local_username"]),
                envelope.sender_instance,
                remote_case_id,
            )
            result = {
                "operation": operation,
                "remote_case_id": remote_case_id,
                "revoked": bool(removed),
            }
        else:
            case_id = str(payload.get("case_id") or "").strip()
            remote_user_id = str(payload.get("actor_user_id") or "").strip()
            if not case_id or not remote_user_id:
                raise ValueError("mail-case action identity is incomplete")
            if operation == "comment.create":
                comment_id = self.cases.add_federated_comment(
                    envelope.sender_instance,
                    remote_user_id,
                    case_id,
                    str(payload.get("comment_id") or ""),
                    str(payload.get("body") or ""),
                )
                result = {"operation": operation, "comment_id": comment_id}
            elif operation == "draft.create":
                draft_id = self.cases.create_federated_draft(
                    envelope.sender_instance,
                    remote_user_id,
                    case_id,
                    str(payload.get("draft_id") or ""),
                    str(payload.get("recipients_to") or ""),
                    str(payload.get("subject") or ""),
                    str(payload.get("body") or ""),
                    sender_identity=str(payload.get("sender_identity") or ""),
                    cc=str(payload.get("recipients_cc") or ""),
                    bcc=str(payload.get("recipients_bcc") or ""),
                )
                result = {"operation": operation, "draft_id": draft_id, "status": "draft"}
            elif operation == "draft.update":
                draft_id = str(payload.get("draft_id") or "")
                self.cases.update_federated_draft(
                    envelope.sender_instance,
                    remote_user_id,
                    case_id,
                    draft_id,
                    str(payload.get("recipients_to") or ""),
                    str(payload.get("subject") or ""),
                    str(payload.get("body") or ""),
                    sender_identity=str(payload.get("sender_identity") or ""),
                    cc=str(payload.get("recipients_cc") or ""),
                    bcc=str(payload.get("recipients_bcc") or ""),
                )
                result = {"operation": operation, "draft_id": draft_id, "status": "draft"}
            elif operation == "draft.request_send":
                draft_id = str(payload.get("draft_id") or "")
                status = self.cases.request_federated_draft_send(
                    envelope.sender_instance,
                    remote_user_id,
                    case_id,
                    draft_id,
                )
                result = {"operation": operation, "draft_id": draft_id, "status": status}
            elif operation == "status.set":
                status = str(payload.get("status") or "")
                self.cases.set_federated_status(
                    envelope.sender_instance,
                    remote_user_id,
                    case_id,
                    status,
                )
                result = {"operation": operation, "status": status}
            else:
                raise ValueError("unsupported mail-case federation operation")
        return self.contract.store.remember_application_result(
            envelope.message_id,
            _OBJECT_TYPE,
            result,
        )

    def content_for_grant(self, token: str, mail_store: MailStore) -> tuple[bytes, str]:
        grant = self.contract.store.resolve_mail_content_grant(token)
        if not grant:
            raise FileNotFoundError("unknown mail-case content grant")
        access = self.cases.federated_message_access(
            str(grant["peer_id"]),
            str(grant["remote_user_id"]),
            str(grant["case_id"]),
            str(grant["mail_reference"]),
        )
        match = _SHA512_REF.fullmatch(str(grant["mail_reference"]))
        if not match:
            raise ValueError("mail-case content is not an archived EML")
        digest = match.group(1)
        raw = load_local_eml_bytes_by_id(
            mail_store,
            str(access["account_owner"]),
            str(access["account_id"]),
            digest,
        )
        if hashlib.sha512(raw).hexdigest() != digest:
            raise ValueError("mail-case EML integrity mismatch")
        return raw, digest

    def fetch_remote_content(
        self,
        peer_id: str,
        content_ref: str,
        expected_mail_reference: str,
    ) -> bytes:
        if not _CONTENT_REF.fullmatch(str(content_ref or "")):
            raise ValueError("invalid mail-case content reference")
        match = _SHA512_REF.fullmatch(str(expected_mail_reference or ""))
        if not match:
            raise ValueError("invalid expected mail reference")
        peer = self.contract.peers.get_peer(peer_id)
        if not peer or not peer.get("enabled"):
            raise ValueError("Federation peer is not active")
        if self._policy(peer).get("receive") is not True:
            raise PermissionError("peer policy does not allow mail-case receiving")
        with _request(
            str(peer["base_url"]).rstrip("/") + content_ref,
            token=self.contract.peers.peer_token(peer_id),
            headers={"Accept": "message/rfc822"},
            timeout=60,
        ) as response:
            raw = response.read(MAX_MESSAGE_BYTES + 1)
        if not raw or len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("invalid federated EML size")
        if hashlib.sha512(raw).hexdigest() != match.group(1):
            raise ValueError("federated EML integrity mismatch")
        return raw
