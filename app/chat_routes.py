"""Authenticated stage-1 chat UI: local/federated text messages and document attachments."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .chat_documents import guessed_mime, save_attachment
from .chat_federation import send_message
from .chat_store import ChatStore
from .db import get_db
from .document_store import DocumentStore
from .federation_store import FederationStore
from .safe_paths import resolve_file_under

bp = Blueprint("chat", __name__, url_prefix="/chat")
MAX_FILES_PER_MESSAGE = 20

def _root(): return current_app.config["DOCUMENT_ROOT"]
def _store(): return ChatStore(_root())
def _actor(): return str(g.user["username"])
def _is_admin(): return bool(g.user["is_admin"] and not g.user["is_disabled"])

def _active_users() -> list[dict]:
    rows=get_db().execute("SELECT username,display_name,is_admin FROM user WHERE is_disabled=0 ORDER BY COALESCE(display_name,username) COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]

def _admin_users() -> list[str]: return [x["username"] for x in _active_users() if x["is_admin"]]

def _send_peers() -> list[dict]:
    result=[]
    for peer in FederationStore(_root()).list_peers():
        policy=peer.get("policy") if isinstance(peer.get("policy"),dict) else {}; chat=policy.get("chat") if isinstance(policy,dict) else None
        if peer.get("enabled") and isinstance(chat,dict) and chat.get("send") is True: result.append(peer)
    return result

def _require_room(room_id: str) -> dict:
    store=_store()
    try: room=store.room(room_id)
    except ValueError: abort(404)
    if not store.can_access(room_id,_actor(),is_admin=_is_admin()): abort(404)
    return room

def _display_messages(room_id: str) -> list[dict]:
    result=_store().messages(room_id)
    for message in result: message["created_display"]=datetime.fromtimestamp(message["created_at"]).astimezone().strftime("%d.%m.%Y %H:%M")
    return result

def _file_limit() -> int:
    try: configured=int(os.environ.get("SIMPLEOFFICE_CHAT_ATTACHMENT_MAX_BYTES","") or current_app.config.get("MAX_CONTENT_LENGTH",512*1024*1024))
    except ValueError: configured=int(current_app.config.get("MAX_CONTENT_LENGTH",512*1024*1024))
    return max(1,min(configured,int(current_app.config.get("MAX_CONTENT_LENGTH",configured))))

@bp.get("")
@bp.get("/")
@login_required
def index():
    store=_store(); rooms=store.rooms_for(_actor(),is_admin=_is_admin())
    for room in rooms: room["participants"]=store.participants(room["room_id"])
    return render_template("chat/index.html",rooms=rooms,users=_active_users(),peers=_send_peers())

@bp.post("/rooms")
@login_required
def create_room():
    active={x["username"] for x in _active_users()}; selected=[x for x in request.form.getlist("local_users") if x in active]; peer_id=str(request.form.get("remote_peer_id") or "").strip()
    if peer_id and peer_id not in {x["peer_id"] for x in _send_peers()}:
        flash("Der gewählte Federation-Peer ist für Chat-Versand nicht freigegeben."); return redirect(url_for("chat.index"))
    remote_users=sorted({x.strip() for x in str(request.form.get("remote_users") or "").split(",") if x.strip()})
    try:
        room=_store().create_room(request.form.get("title","Chat"),_actor(),selected,remote_peer_id=peer_id,remote_users=remote_users)
        return redirect(url_for("chat.room",room_id=room["room_id"]))
    except ValueError as exc: flash(str(exc)); return redirect(url_for("chat.index"))

@bp.get("/rooms/<room_id>")
@login_required
def room(room_id: str):
    room_data=_require_room(room_id); store=_store()
    return render_template("chat/room.html",room=room_data,participants=store.participants(room_id),messages=_display_messages(room_id),may_send=store.is_participant(room_id,_actor()))

@bp.get("/rooms/<room_id>/messages-fragment")
@login_required
def messages_fragment(room_id: str):
    room_data=_require_room(room_id)
    return render_template("chat/_messages.html",room=room_data,messages=_display_messages(room_id),may_send=_store().is_participant(room_id,_actor()))

@bp.post("/rooms/<room_id>/messages")
@login_required
def post_message(room_id: str):
    room_data=_require_room(room_id); store=_store()
    if not store.is_participant(room_id,_actor()): abort(403)
    body=str(request.form.get("body") or "").strip(); files=[x for x in request.files.getlist("attachments") if x and x.filename]
    if len(files)>MAX_FILES_PER_MESSAGE: flash(f"Maximal {MAX_FILES_PER_MESSAGE} Anhänge pro Nachricht."); return redirect(url_for("chat.room",room_id=room_id))
    if not body and not files: flash("Nachricht oder Anhang fehlt."); return redirect(url_for("chat.room",room_id=room_id))
    visibility="chat" if request.form.get("private_files")=="1" else "documents"; message=store.add_message(room_id,_actor(),body); limit=_file_limit()
    try:
        for upload in files:
            data=upload.read(limit+1)
            if len(data)>limit: raise ValueError(f"Anhang {upload.filename} überschreitet das Chat-Upload-Limit")
            aid=str(uuid.uuid4()); digest=hashlib.sha256(data).hexdigest(); document=save_attachment(_root(),data,upload.filename,_actor(),room_id=room_id,attachment_id=aid,local_users=store.local_users(room_id),admin_users=_admin_users(),visibility=visibility,max_bytes=limit)
            store.register_attachment(message["message_id"],aid,upload.filename,upload.mimetype or guessed_mime(upload.filename),len(data),digest,visibility,document_id=document["document_id"],state="ready")
        if room_data.get("remote_peer_id"):
            try: send_message(_root(),message["message_id"])
            except Exception as exc: flash(f"Nachricht lokal gespeichert; Federation fehlgeschlagen: {exc}")
        return redirect(url_for("chat.room",room_id=room_id)+"#latest")
    except (ValueError,OSError,RuntimeError) as exc:
        flash(f"Nachricht wurde gespeichert, aber ein Anhang konnte nicht verarbeitet werden: {exc}"); return redirect(url_for("chat.room",room_id=room_id))

@bp.post("/messages/<message_id>/retry")
@login_required
def retry_message(message_id: str):
    try:
        message=_store().message(message_id); _require_room(message["room_id"])
        if not _store().is_participant(message["room_id"],_actor()): abort(403)
        send_message(_root(),message_id); flash("Federation-Zustellung abgeschlossen."); return redirect(url_for("chat.room",room_id=message["room_id"]))
    except (ValueError,PermissionError,OSError) as exc: flash(f"Erneute Zustellung fehlgeschlagen: {exc}"); return redirect(url_for("chat.index"))

@bp.get("/attachments/<attachment_id>")
@login_required
def attachment(attachment_id: str):
    store=_store()
    try: item=store.attachment(attachment_id)
    except ValueError: abort(404)
    _require_room(item["room_id"])
    if not item.get("document_id") or item.get("state")!="ready": abort(404)
    try:
        documents=DocumentStore(_root()); document=documents.get_document(item["document_id"]); path=resolve_file_under(documents.root,str(document.get("last_path","")))
    except (ValueError,OSError): abort(404)
    response=send_file(path,as_attachment=True,download_name=item["filename"],mimetype=item["mime_type"],conditional=True); response.headers["Cache-Control"]="private, no-store"; return response

@bp.get("/rooms/<room_id>/state.json")
@login_required
def room_state(room_id: str):
    room_data=_require_room(room_id); messages=_display_messages(room_id)
    return jsonify({"room_id":room_data["room_id"],"message_count":len(messages),"last_message_id":messages[-1]["message_id"] if messages else ""})
