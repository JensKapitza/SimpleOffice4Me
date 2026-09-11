"""Policy checks and local notices for structured federated chat actions."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .chat_store import ChatStore

ACTION_MESSAGE_TYPES = {"contact", "poll", "request"}
ACTION_LABELS = {
    "contact": "Kontaktangebot",
    "poll": "Umfrage",
    "request": "Anfrage",
}


def action_for_message_type(message_type: str) -> str:
    value = str(message_type or "").strip().casefold()
    return value if value in ACTION_MESSAGE_TYPES else ""


def action_allowed(peer: dict[str, Any], message_type: str, direction: str) -> bool:
    """Structured actions are default-deny; ordinary text uses chat.send/receive only."""
    action = action_for_message_type(message_type)
    if not action:
        return True
    if direction not in {"send", "receive"}:
        raise ValueError("Ungültige Chat-Policy-Richtung")
    policy = peer.get("policy") if isinstance(peer, dict) else None
    chat = policy.get("chat") if isinstance(policy, dict) else None
    actions = chat.get("actions") if isinstance(chat, dict) else None
    rule = actions.get(action) if isinstance(actions, dict) else None
    return bool(isinstance(rule, dict) and rule.get(direction) is True)


def policy_notice_id(related_message_id: str, perspective: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simpleoffice-chat-policy:{perspective}:{related_message_id}"))


def _notice_text(action: str, perspective: str, source_username: str = "") -> str:
    label = ACTION_LABELS.get(action, action or "Aktion")
    if perspective == "receiver":
        source = f" von {source_username}" if source_username else ""
        return f"Aktion „{label}“{source}: Durch die Serverregeln deines Admins abgelehnt."
    if perspective == "sender_local":
        return f"Aktion „{label}“: Durch die Serverregeln deines Admins blockiert."
    if perspective == "sender_remote":
        return f"Aktion „{label}“: Durch die Serverregeln des Chatpartners abgelehnt."
    raise ValueError("Ungültige Policy-Hinweis-Perspektive")


def record_policy_notice(
    store: ChatStore,
    room_id: str,
    related_message_id: str,
    action: str,
    perspective: str,
    *,
    peer_id: str = "",
    source_username: str = "",
) -> dict[str, Any]:
    """Create one deterministic system message without copying blocked action data."""
    room = store.room(room_id)
    message_id = policy_notice_id(related_message_id, perspective)
    body = _notice_text(action, perspective, source_username)
    payload = {
        "event": "policy_denied",
        "action": action,
        "reason_code": "admin_policy",
        "perspective": perspective,
        "related_message_id": str(related_message_id),
        "peer_id": str(peer_id or ""),
    }
    now = int(time.time())
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with store._db() as db:
        old = db.execute("SELECT * FROM chat_message WHERE message_id=?", (message_id,)).fetchone()
        if old is None:
            db.execute(
                "INSERT INTO chat_message(message_id,room_id,sender_kind,sender_username,sender_label,message_type,body,payload_json,created_at,received_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (message_id, room["room_id"], "system", "system", "System", "system", body, encoded, now, now),
            )
            db.execute("UPDATE chat_room SET updated_at=? WHERE room_id=?", (now, room["room_id"]))
    return store.message(message_id)
