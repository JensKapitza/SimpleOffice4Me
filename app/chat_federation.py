"""Synchronous sender for stage-1 federated chat events and attachments."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .chat_documents import attachment_bytes
from .chat_federation_auth import ChatRequestProof, headers_for, new_nonce
from .chat_policy import action_allowed, action_for_message_type, record_policy_notice
from .chat_store import ChatStore
from .federation_core import sanitize_peer_id
from .federation_store import FederationStore

MAX_RESPONSE_BYTES = 256 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _local_peer_id() -> str:
    configured = os.environ.get("SIMPLEOFFICE_FEDERATION_PEER_ID", "").strip()
    fallback = socket.gethostname().strip().casefold().replace(" ", "-")[:128]
    return sanitize_peer_id(configured or fallback)


def _chat_send_allowed(peer: dict[str, Any]) -> bool:
    policy = peer.get("policy") if isinstance(peer.get("policy"), dict) else {}
    chat = policy.get("chat") if isinstance(policy, dict) else None
    return bool(peer.get("enabled") and isinstance(chat, dict) and chat.get("send") is True)


def _endpoint(peer: dict[str, Any], suffix: str) -> str:
    base = str(peer.get("base_url") or "").strip().rstrip("/")
    if not base.startswith(("https://", "http://")) or "@" in base.split("//", 1)[-1].split("/", 1)[0]:
        raise ValueError("Ungültige Federation-URL")
    return base + "/federation/v1/chat/" + suffix.lstrip("/")


def _decode_response(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Federation-Antwort ist zu groß")
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Ungültige Federation-Antwort") from exc
    return parsed if isinstance(parsed, dict) else {}


def _request(
    url: str,
    payload: bytes,
    content_type: str,
    proof: ChatRequestProof,
    token: str,
    *,
    return_http_error: bool = False,
) -> tuple[int, dict[str, Any]]:
    headers = headers_for(proof, token)
    headers.update({"Content-Type": content_type, "Accept": "application/json", "Cache-Control": "no-store"})
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=20) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            return int(response.status), _decode_response(body)
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BYTES + 1)
        if return_http_error:
            try:
                return int(exc.code), _decode_response(body)
            except ValueError:
                return int(exc.code), {}
        raise ValueError(f"Federation-Chat HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ValueError("Federation-Chat nicht erreichbar") from exc


def _proof(kind: str, resource_id: str, payload: bytes) -> ChatRequestProof:
    return ChatRequestProof(
        _local_peer_id(),
        int(time.time()),
        new_nonce(),
        kind,
        resource_id,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
    )


def event_payload(store: ChatStore, message_id: str) -> dict[str, Any]:
    message = store.message(message_id)
    room = store.room(message["room_id"])
    full = next((x for x in store.messages(room["room_id"], limit=1000) if x["message_id"] == message_id), message)
    return {
        "schema": 1,
        "room": {"room_id": room["room_id"], "title": room["title"]},
        "source_users": store.local_users(room["room_id"]),
        "target_users": store.remote_users(room["room_id"]),
        "message": {
            "message_id": message["message_id"],
            "sender_username": message["sender_username"],
            "message_type": message["message_type"],
            "body": message["body"],
            "payload": message["payload"],
            "created_at": message["created_at"],
        },
        "attachments": [
            {
                "attachment_id": x["attachment_id"],
                "filename": x["filename"],
                "mime_type": x["mime_type"],
                "size": x["size"],
                "sha256": x["sha256"],
                "visibility": x["visibility"],
            }
            for x in full.get("attachments", [])
        ],
    }


def _policy_preflight(
    chat: ChatStore,
    federation: FederationStore,
    peer: dict[str, Any],
    token: str,
    message: dict[str, Any],
    room: dict[str, Any],
) -> None:
    action = action_for_message_type(message["message_type"])
    if not action:
        return
    peer_id = str(room["remote_peer_id"])
    if not action_allowed(peer, action, "send"):
        record_policy_notice(chat, room["room_id"], message["message_id"], action, "sender_local", peer_id=peer_id)
        chat.mark_delivery(message["message_id"], peer_id, "failed", "policy_denied:local")
        federation.record_event(
            "chat_action_policy_denied",
            peer_id=peer_id,
            detail={"message_id": message["message_id"], "action": action, "direction": "send"},
        )
        raise PermissionError("Diese Chat-Aktion wurde durch die Serverregeln deines Admins blockiert")
    preflight = {
        "schema": 1,
        "room": {"room_id": room["room_id"], "title": room["title"]},
        "source_users": chat.local_users(room["room_id"]),
        "target_users": chat.remote_users(room["room_id"]),
        "message_id": message["message_id"],
        "sender_username": message["sender_username"],
        "message_type": action,
    }
    payload = json.dumps(preflight, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    status, response = _request(
        _endpoint(peer, "actions/preflight"),
        payload,
        "application/json; charset=utf-8",
        _proof("policy", message["message_id"], payload),
        token,
        return_http_error=True,
    )
    if status == 403 and response.get("reason_code") == "admin_policy":
        record_policy_notice(chat, room["room_id"], message["message_id"], action, "sender_remote", peer_id=peer_id)
        chat.mark_delivery(message["message_id"], peer_id, "failed", "policy_denied:remote")
        federation.record_event(
            "chat_action_policy_denied",
            peer_id=peer_id,
            detail={"message_id": message["message_id"], "action": action, "direction": "remote_receive"},
        )
        raise PermissionError("Dein Angebot wurde durch die Serverregeln des Chatpartners abgelehnt")
    if status < 200 or status >= 300 or response.get("allowed") is not True:
        raise ValueError(f"Federation-Chat Policy-Preflight fehlgeschlagen (HTTP {status})")


def send_message(root: str | Path, message_id: str) -> dict[str, Any]:
    chat = ChatStore(root)
    message = chat.message(message_id)
    room = chat.room(message["room_id"])
    peer_id = str(room.get("remote_peer_id") or "")
    if not peer_id:
        return {"federated": False, "status": "local"}
    federation = FederationStore(root)
    peer = federation.get_peer(peer_id)
    if not peer or not _chat_send_allowed(peer):
        chat.mark_delivery(message_id, peer_id, "failed", "chat.send ist für den Peer nicht freigegeben")
        raise ValueError("Federation-Peer erlaubt keinen Chat-Versand")
    token = federation.peer_token(peer_id)
    _policy_preflight(chat, federation, peer, token, message, room)
    payload = json.dumps(event_payload(chat, message_id), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    chat.mark_delivery(message_id, peer_id, "sending")
    try:
        _request(_endpoint(peer, "events"), payload, "application/json; charset=utf-8", _proof("event", message_id, payload), token)
        attachments = next(
            (x.get("attachments", []) for x in chat.messages(room["room_id"], limit=1000) if x["message_id"] == message_id),
            [],
        )
        sent = 0
        for item in attachments:
            if not item.get("document_id"):
                raise ValueError("Chat-Anhang ist lokal noch nicht vollständig")
            data = attachment_bytes(root, item["document_id"])
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("Chat-Anhang stimmt nicht mehr mit der Nachricht überein")
            _request(
                _endpoint(peer, f"attachments/{item['attachment_id']}"),
                data,
                "application/octet-stream",
                _proof("attachment", item["attachment_id"], data),
                token,
            )
            sent += 1
        chat.mark_delivery(message_id, peer_id, "complete")
        federation.record_event("chat_message_sent", peer_id=peer_id, detail={"message_id": message_id, "attachments": sent})
        return {"federated": True, "status": "complete", "attachments": sent}
    except PermissionError:
        raise
    except Exception as exc:
        chat.mark_delivery(message_id, peer_id, "failed", str(exc))
        federation.record_event("chat_message_failed", peer_id=peer_id, detail={"message_id": message_id, "error": str(exc)[:500]})
        raise
