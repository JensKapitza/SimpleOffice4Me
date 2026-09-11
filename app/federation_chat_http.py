"""Receive signed stage-1 chat messages, action preflights and attachment copies."""
from __future__ import annotations

import json
import uuid
from flask import Blueprint, Response, current_app, jsonify, request

from .chat_documents import save_attachment
from .chat_federation_auth import authenticate_request
from .chat_policy import action_allowed, action_for_message_type, record_policy_notice
from .chat_store import ChatStore
from .db import get_db
from .federation_store import FederationStore

bp = Blueprint("federation_chat", __name__, url_prefix="/federation/v1/chat")
MAX_EVENT_BYTES = 1024 * 1024
MAX_POLICY_BYTES = 64 * 1024

def _root(): return current_app.config["DOCUMENT_ROOT"]
def _chat(): return ChatStore(_root())
def _federation(): return FederationStore(_root())

def _active_users(usernames: list[str]) -> list[str]:
    wanted=sorted({str(x or "").strip() for x in usernames if str(x or "").strip()})
    if not wanted: return []
    placeholders=",".join("?" for _ in wanted)
    rows=get_db().execute(f"SELECT username FROM user WHERE username IN ({placeholders}) AND is_disabled=0",tuple(wanted)).fetchall(); found=sorted(str(r["username"]) for r in rows)
    if found != wanted: raise ValueError("Unbekannte oder deaktivierte Chat-Teilnehmer: " + ", ".join(sorted(set(wanted)-set(found))))
    return found

def _admins() -> list[str]:
    return [str(r["username"]) for r in get_db().execute("SELECT username FROM user WHERE is_admin=1 AND is_disabled=0 ORDER BY username").fetchall()]

def _uuid(value: str, label: str) -> str:
    try: return str(uuid.UUID(str(value)))
    except (ValueError,TypeError,AttributeError) as exc: raise ValueError(f"Ungültige {label}") from exc

def _authenticated_payload(kind: str, max_bytes: int):
    payload=request.get_data(cache=True)
    if len(payload)>max_bytes: raise ValueError("Chat-Payload ist zu groß")
    peer,proof=authenticate_request(_federation(),request.headers,payload)
    if proof.kind != kind: raise ValueError("Chat-Request-Typ passt nicht zum Endpunkt")
    return peer,proof,payload

@bp.post("/actions/preflight")
def action_preflight():
    try:
        peer,proof,payload=_authenticated_payload("policy",MAX_POLICY_BYTES); data=json.loads(payload.decode("utf-8"))
        if not isinstance(data,dict) or data.get("schema") != 1: raise ValueError("Nicht unterstützter Policy-Preflight")
        room_data=data.get("room") if isinstance(data.get("room"),dict) else {}
        room_id=_uuid(room_data.get("room_id","") ,"Chat-ID"); message_id=_uuid(data.get("message_id","") ,"Nachrichten-ID")
        if proof.resource_id != message_id: raise ValueError("Signierte Ressourcen-ID passt nicht zur Aktion")
        action=action_for_message_type(str(data.get("message_type") or ""))
        if not action: raise ValueError("Preflight ist nur für strukturierte Chat-Aktionen zulässig")
        targets=_active_users(data.get("target_users") if isinstance(data.get("target_users"),list) else [])
        sources=[str(x or "").strip() for x in (data.get("source_users") or []) if str(x or "").strip()]
        sender=str(data.get("sender_username") or "").strip()
        if not targets or not sources or sender not in sources: raise ValueError("Federierter Chat enthält ungültige Teilnehmer")
        chat=_chat(); chat.upsert_remote_room(room_id,str(room_data.get("title") or "Chat"),peer["peer_id"],targets,sources)
        if not action_allowed(peer,action,"receive"):
            record_policy_notice(chat,room_id,message_id,action,"receiver",peer_id=peer["peer_id"],source_username=sender)
            _federation().record_event("chat_action_policy_denied",peer_id=peer["peer_id"],detail={"message_id":message_id,"action":action,"direction":"receive"})
            return jsonify({"ok":False,"allowed":False,"reason_code":"admin_policy","action":action}),403
        return jsonify({"ok":True,"allowed":True,"action":action}),200
    except (ValueError,PermissionError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        return jsonify({"error":"invalid_chat_policy_preflight","detail":str(exc)[:500]}),400

@bp.post("/events")
def receive_event():
    try:
        peer,proof,payload=_authenticated_payload("event",MAX_EVENT_BYTES); data=json.loads(payload.decode("utf-8"))
        if not isinstance(data,dict) or data.get("schema") != 1: raise ValueError("Nicht unterstütztes Chat-Event")
        room_data=data.get("room") if isinstance(data.get("room"),dict) else {}; msg=data.get("message") if isinstance(data.get("message"),dict) else {}
        room_id=_uuid(room_data.get("room_id","") ,"Chat-ID"); message_id=_uuid(msg.get("message_id","") ,"Nachrichten-ID")
        if proof.resource_id != message_id: raise ValueError("Signierte Ressourcen-ID passt nicht zur Nachricht")
        targets=_active_users(data.get("target_users") if isinstance(data.get("target_users"),list) else []); sources=[str(x or "").strip() for x in (data.get("source_users") or []) if str(x or "").strip()]; sender=str(msg.get("sender_username") or "").strip()
        if not targets or not sources or sender not in sources: raise ValueError("Federierter Chat enthält ungültige Teilnehmer")
        message_type=str(msg.get("message_type") or "text").casefold()
        action=action_for_message_type(message_type)
        if action and not action_allowed(peer,action,"receive"):
            chat=_chat(); chat.upsert_remote_room(room_id,str(room_data.get("title") or "Chat"),peer["peer_id"],targets,sources)
            record_policy_notice(chat,room_id,message_id,action,"receiver",peer_id=peer["peer_id"],source_username=sender)
            return jsonify({"error":"chat_action_denied","allowed":False,"reason_code":"admin_policy","action":action}),403
        chat=_chat(); chat.upsert_remote_room(room_id,str(room_data.get("title") or "Chat"),peer["peer_id"],targets,sources); chat.upsert_remote_message(room_id,peer["peer_id"],sender,message_id,str(msg.get("body") or ""),message_type=message_type,payload=msg.get("payload") if isinstance(msg.get("payload"),dict) else {},created_at=int(msg.get("created_at") or 0) or None)
        attachments=data.get("attachments") if isinstance(data.get("attachments"),list) else []
        if len(attachments)>50: raise ValueError("Zu viele Anhänge in einer Chat-Nachricht")
        for item in attachments:
            if not isinstance(item,dict): raise ValueError("Ungültige Anhang-Metadaten")
            chat.register_attachment(message_id,_uuid(item.get("attachment_id","") ,"Anhang-ID"),str(item.get("filename") or "datei"),str(item.get("mime_type") or "application/octet-stream"),int(item.get("size") or 0),str(item.get("sha256") or ""),str(item.get("visibility") or "chat"),state="pending")
        _federation().record_event("chat_message_received",peer_id=peer["peer_id"],detail={"message_id":message_id,"attachments":len(attachments)})
        return jsonify({"ok":True,"room_id":room_id,"message_id":message_id}),201
    except (ValueError,PermissionError,UnicodeDecodeError,json.JSONDecodeError) as exc: return jsonify({"error":"invalid_chat_event","detail":str(exc)[:500]}),400

@bp.post("/attachments/<attachment_id>")
def receive_attachment(attachment_id: str):
    try:
        attachment_id=_uuid(attachment_id,"Anhang-ID"); peer,proof,payload=_authenticated_payload("attachment",int(current_app.config.get("MAX_CONTENT_LENGTH",512*1024*1024)))
        if proof.resource_id != attachment_id: raise ValueError("Signierte Ressourcen-ID passt nicht zum Anhang")
        chat=_chat(); item=chat.attachment(attachment_id); room=chat.room(item["room_id"])
        if room["remote_peer_id"] != peer["peer_id"]: raise PermissionError("Anhang stammt nicht vom gebundenen Federation-Peer")
        if proof.payload_size != item["size"] or proof.payload_sha256 != item["sha256"]: raise ValueError("Anhang weicht von den angekündigten Metadaten ab")
        if item.get("document_id") and item.get("state")=="ready": return jsonify({"ok":True,"attachment_id":attachment_id,"document_id":item["document_id"]}),200
        document=save_attachment(_root(),payload,item["filename"],f"federation:{peer['peer_id']}",room_id=room["room_id"],attachment_id=attachment_id,local_users=chat.local_users(room["room_id"]),admin_users=_admins(),visibility=item["visibility"],source_peer=peer["peer_id"],max_bytes=int(current_app.config.get("MAX_CONTENT_LENGTH",512*1024*1024)))
        chat.set_attachment_document(attachment_id,document["document_id"]); _federation().record_event("chat_attachment_received",peer_id=peer["peer_id"],detail={"attachment_id":attachment_id,"document_id":document["document_id"]})
        return jsonify({"ok":True,"attachment_id":attachment_id,"document_id":document["document_id"]}),201
    except (ValueError,PermissionError,OSError,RuntimeError) as exc: return jsonify({"error":"invalid_chat_attachment","detail":str(exc)[:500]}),400

@bp.get("/health")
def health(): return Response("chat federation endpoint\n",200,{"Content-Type":"text/plain; charset=utf-8","Cache-Control":"no-store"})
