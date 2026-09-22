"""Authenticated chat UI with rich SimpleOffice sharing and federation delivery."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from .auth import login_required
from .chat_calls import call_settings, sip_uri
from .chat_documents import guessed_mime, save_attachment
from .chat_features import ALLOWED_REACTIONS, ChatFeatureStore
from .chat_federation import send_interaction, send_message
from .chat_share import build_share_card, share_choices
from .chat_store import ChatStore
from .chat_status import ChatStatusStore
from .db import get_db
from .document_store import DocumentStore, sha256_file
from .federation_store import FederationStore
from .safe_paths import resolve_file_under

bp = Blueprint("chat", __name__, url_prefix="/chat")
MAX_FILES_PER_MESSAGE = 20
_PREVIEW_PREFIXES = ("image/", "audio/", "video/")


def _root(): return current_app.config["DOCUMENT_ROOT"]
def _store(): return ChatStore(_root())
def _features(): return ChatFeatureStore(_root())
def _statuses(): return ChatStatusStore(_root())
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
    result=_features().decorate(room_id,_store().messages(room_id))
    for message in result:
        message["created_display"]=datetime.fromtimestamp(message["created_at"]).astimezone().strftime("%d.%m.%Y %H:%M")
    return result


def _sync_interaction(room_id: str, action: str, **values) -> None:
    room=_store().room(room_id)
    if not room.get("remote_peer_id"):
        return
    try:
        send_interaction(_root(),room_id,_actor(),action,**values)
    except (ValueError,PermissionError,OSError):
        # Interaction delivery is best-effort; the local chat action must remain responsive.
        pass


def _mark_room_read(room_id: str, messages: list[dict]) -> None:
    store=_store()
    if not store.is_participant(room_id,_actor()):
        return
    last_message_id=messages[-1]["message_id"] if messages else ""
    if _features().mark_read(room_id,_actor(),last_message_id):
        _sync_interaction(room_id,"read",last_message_id=last_message_id)


def _file_limit() -> int:
    try: configured=int(os.environ.get("SIMPLEOFFICE_CHAT_ATTACHMENT_MAX_BYTES","") or current_app.config.get("MAX_CONTENT_LENGTH",512*1024*1024))
    except ValueError: configured=int(current_app.config.get("MAX_CONTENT_LENGTH",512*1024*1024))
    return max(1,min(configured,int(current_app.config.get("MAX_CONTENT_LENGTH",configured))))


def _deliver_if_remote(room: dict, message_id: str) -> None:
    if not room.get("remote_peer_id"):
        return
    try: send_message(_root(),message_id)
    except Exception as exc: flash(f"Nachricht lokal gespeichert; Federation fehlgeschlagen: {exc}")


def _reply_payload(room_id: str) -> dict:
    reply_to=str(request.form.get("reply_to") or "").strip()
    if not reply_to:
        return {}
    try: original=_store().message(reply_to)
    except ValueError: return {}
    if original["room_id"] != room_id: return {}
    return {"reply": {"message_id": original["message_id"], "sender": original["sender_username"], "body": str(original.get("body") or "")[:240]}}


def _call_targets(room_id: str) -> list[dict]:
    rows=[]
    for participant in _store().participants(room_id):
        username=str(participant.get("username") or "")
        if participant.get("participant_kind")=="local" and username==_actor():
            continue
        try: uri=sip_uri(_root(),username,"video")
        except ValueError: uri=""
        rows.append({**participant,"sip_uri":uri})
    return rows


def _forward_rooms(current_room_id: str) -> list[dict]:
    store=_store(); rows=[]
    for room in store.rooms_for(_actor(),is_admin=False):
        if room["room_id"]==current_room_id or not store.is_participant(room["room_id"],_actor()):
            continue
        rows.append(room)
    return rows


def _attachment_file(attachment_id: str):
    store=_store()
    try: item=store.attachment(attachment_id)
    except ValueError: abort(404)
    _require_room(item["room_id"])
    if not item.get("document_id") or item.get("state")!="ready": abort(404)
    try:
        documents=DocumentStore(_root()); document=documents.get_document(item["document_id"]); path=resolve_file_under(documents.root,str(document.get("last_path","")))
    except (ValueError,OSError): abort(404)
    return item,path


@bp.get("")
@bp.get("/")
@login_required
def index():
    store=_store(); rooms=store.rooms_for(_actor(),is_admin=_is_admin())
    for room in rooms: room["participants"]=store.participants(room["room_id"])
    return render_template("chat/index.html",rooms=rooms,users=_active_users(),peers=_send_peers())


@bp.get("/status")
@login_required
def status_page():
    users = [
        user for user in _active_users()
        if user["username"] != _actor()
    ]
    return render_template(
        "chat/status.html",
        visible_statuses=_statuses().visible_for(_actor()),
        own_statuses=_statuses().own(_actor()),
        users=users,
    )


@bp.post("/status")
@login_required
def publish_status():
    active = {user["username"] for user in _active_users() if user["username"] != _actor()}
    viewers = [value for value in request.form.getlist("viewers") if value in active]
    try:
        _statuses().publish(_actor(), request.form.get("body", ""), viewers=viewers)
        flash("Status veröffentlicht. Er läuft automatisch nach spätestens 24 Stunden ab.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("chat.status_page"))


@bp.post("/status/<status_id>/delete")
@login_required
def delete_status(status_id: str):
    try:
        _statuses().remove(status_id, _actor())
        flash("Status entfernt.")
    except ValueError as exc:
        flash(str(exc))
    return redirect(url_for("chat.status_page"))


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
    room_data=_require_room(room_id); store=_store(); messages=_display_messages(room_id); _mark_room_read(room_id,messages)
    return render_template(
        "chat/room.html",room=room_data,participants=store.participants(room_id),messages=messages,
        may_send=store.is_participant(room_id,_actor()),share_choices=share_choices(_root(),_actor()),
        reactions=sorted(ALLOWED_REACTIONS),call_settings=call_settings(_root()),call_targets=_call_targets(room_id),
        forward_rooms=_forward_rooms(room_id),
    )


@bp.get("/rooms/<room_id>/messages-fragment")
@login_required
def messages_fragment(room_id: str):
    room_data=_require_room(room_id); messages=_display_messages(room_id); _mark_room_read(room_id,messages)
    return render_template(
        "chat/_messages.html",room=room_data,messages=messages,may_send=_store().is_participant(room_id,_actor()),
        reactions=sorted(ALLOWED_REACTIONS),forward_rooms=_forward_rooms(room_id),
    )


@bp.post("/rooms/<room_id>/messages")
@login_required
def post_message(room_id: str):
    room_data=_require_room(room_id); store=_store()
    if not store.is_participant(room_id,_actor()): abort(403)
    body=str(request.form.get("body") or "").strip(); files=[x for x in request.files.getlist("attachments") if x and x.filename]
    if len(files)>MAX_FILES_PER_MESSAGE: flash(f"Maximal {MAX_FILES_PER_MESSAGE} Anhänge pro Nachricht."); return redirect(url_for("chat.room",room_id=room_id))
    if not body and not files: flash("Nachricht oder Anhang fehlt."); return redirect(url_for("chat.room",room_id=room_id))
    visibility="chat" if request.form.get("private_files")=="1" else "documents"; message=store.add_message(room_id,_actor(),body,payload=_reply_payload(room_id)); limit=_file_limit()
    try:
        for upload in files:
            data=upload.read(limit+1)
            if len(data)>limit: raise ValueError(f"Anhang {upload.filename} überschreitet das Chat-Upload-Limit")
            aid=str(uuid.uuid4()); digest=hashlib.sha256(data).hexdigest(); document=save_attachment(_root(),data,upload.filename,_actor(),room_id=room_id,attachment_id=aid,local_users=store.local_users(room_id),admin_users=_admin_users(),visibility=visibility,max_bytes=limit)
            store.register_attachment(message["message_id"],aid,upload.filename,upload.mimetype or guessed_mime(upload.filename),len(data),digest,visibility,document_id=document["document_id"],state="ready")
        _deliver_if_remote(room_data,message["message_id"])
        return redirect(url_for("chat.room",room_id=room_id)+"#latest")
    except (ValueError,OSError,RuntimeError) as exc:
        flash(f"Nachricht wurde gespeichert, aber ein Anhang konnte nicht verarbeitet werden: {exc}"); return redirect(url_for("chat.room",room_id=room_id))


@bp.post("/rooms/<room_id>/share")
@login_required
def share_object(room_id: str):
    room_data=_require_room(room_id); store=_store()
    if not store.is_participant(room_id,_actor()): abort(403)
    try:
        kind=str(request.form.get("kind") or "").strip().casefold(); card=build_share_card(_root(),kind,str(request.form.get("object_id") or "").strip(),_actor())
        message_type="contact" if kind=="contact" else "request"
        message=store.add_message(room_id,_actor(),str(request.form.get("comment") or "").strip(),message_type=message_type,payload={"share":card,**_reply_payload(room_id)})
        if kind=="document":
            documents=DocumentStore(_root()); document=documents.get_document(card["object_id"]); path=resolve_file_under(documents.root,str(document.get("last_path") or "")); data_size=path.stat().st_size
            digest=str(document.get("sha256") or "").casefold()
            if len(digest)!=64: digest=sha256_file(path)
            store.register_attachment(message["message_id"],str(uuid.uuid4()),path.name,guessed_mime(path.name),data_size,digest,"chat",document_id=card["object_id"],state="ready")
        _deliver_if_remote(room_data,message["message_id"])
        return redirect(url_for("chat.room",room_id=room_id)+"#latest")
    except (ValueError,OSError,RuntimeError) as exc:
        flash(f"Objekt konnte nicht geteilt werden: {exc}"); return redirect(url_for("chat.room",room_id=room_id))


@bp.post("/messages/<message_id>/reaction")
@login_required
def react(message_id: str):
    try:
        message=_store().message(message_id); _require_room(message["room_id"]); emoji=str(request.form.get("emoji") or ""); active=_features().toggle_reaction(message_id,_actor(),emoji)
        _sync_interaction(message["room_id"],"reaction",message_id=message_id,emoji=emoji,active=active)
        return redirect(url_for("chat.room",room_id=message["room_id"])+f"#message-{message_id}")
    except (ValueError,PermissionError) as exc:
        flash(str(exc)); return redirect(url_for("chat.index"))


@bp.post("/messages/<message_id>/retract")
@login_required
def retract_message(message_id: str):
    try:
        message=_store().message(message_id); _require_room(message["room_id"]); _features().retract(message_id,_actor(),is_admin=_is_admin()); _sync_interaction(message["room_id"],"retract",message_id=message_id)
        return redirect(url_for("chat.room",room_id=message["room_id"])+f"#message-{message_id}")
    except (ValueError,PermissionError) as exc:
        flash(str(exc)); return redirect(url_for("chat.index"))


@bp.post("/messages/<message_id>/forward")
@login_required
def forward_message(message_id: str):
    try:
        source=_store().message(message_id); _require_room(source["room_id"]); target_id=str(request.form.get("target_room_id") or "").strip(); target=_require_room(target_id); store=_store()
        if not store.is_participant(target_id,_actor()): abort(403)
        if message_id in _features().retracted_ids(source["room_id"]): raise ValueError("Zurückgezogene Nachrichten können nicht weitergeleitet werden")
        payload=dict(source.get("payload") or {}); payload.pop("reply",None); payload["forwarded_from"]={"message_id":source["message_id"],"sender":source["sender_username"]}
        forwarded=store.add_message(target_id,_actor(),source.get("body") or "",message_type=source.get("message_type") or "text",payload=payload)
        full=next((item for item in store.messages(source["room_id"],limit=1000) if item["message_id"]==source["message_id"]),source)
        for attachment in full.get("attachments",[]):
            if not attachment.get("document_id") or attachment.get("state")!="ready": continue
            store.register_attachment(forwarded["message_id"],str(uuid.uuid4()),attachment["filename"],attachment["mime_type"],attachment["size"],attachment["sha256"],"chat",document_id=attachment["document_id"],state="ready")
        _deliver_if_remote(target,forwarded["message_id"])
        return redirect(url_for("chat.room",room_id=target_id)+"#latest")
    except (ValueError,PermissionError,OSError,RuntimeError) as exc:
        flash(f"Nachricht konnte nicht weitergeleitet werden: {exc}"); return redirect(url_for("chat.index"))


@bp.post("/rooms/<room_id>/calls")
@login_required
def start_call(room_id: str):
    room_data=_require_room(room_id); store=_store()
    if not store.is_participant(room_id,_actor()): abort(403)
    call_type=str(request.form.get("call_type") or "audio").casefold(); target=str(request.form.get("target") or "").strip()
    try:
        allowed={str(row.get("username") or "") for row in store.participants(room_id) if not (row.get("participant_kind")=="local" and row.get("username")==_actor())}
        if target not in allowed: raise ValueError("Anrufziel ist kein Teilnehmer dieses Chats")
        uri=sip_uri(_root(),target,call_type)
        if not uri:
            raise ValueError("SIP-Server ist noch nicht konfiguriert. Unter Telefonie reicht zunächst Server + Standardport 5060/UDP.")
        message=store.add_message(room_id,_actor(),"",message_type="request",payload={"share":{"kind":"call","call_type":call_type,"target":target,"sip_uri":uri}})
        _deliver_if_remote(room_data,message["message_id"])
        return redirect(uri,code=303)
    except ValueError as exc:
        flash(str(exc)); return redirect(url_for("chat.room",room_id=room_id))


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
    item,path=_attachment_file(attachment_id)
    response=send_file(path,as_attachment=True,download_name=item["filename"],mimetype=item["mime_type"],conditional=True); response.headers["Cache-Control"]="private, no-store"; return response


@bp.get("/attachments/<attachment_id>/preview")
@login_required
def attachment_preview(attachment_id: str):
    item,path=_attachment_file(attachment_id); mime=str(item.get("mime_type") or "")
    if not mime.startswith(_PREVIEW_PREFIXES): abort(404)
    response=send_file(path,as_attachment=False,mimetype=mime,conditional=True); response.headers["Cache-Control"]="private, no-store"; response.headers["X-Content-Type-Options"]="nosniff"; return response


@bp.get("/rooms/<room_id>/state.json")
@login_required
def room_state(room_id: str):
    room_data=_require_room(room_id); messages=_display_messages(room_id); _mark_room_read(room_id,messages)
    return jsonify({"room_id":room_data["room_id"],"message_count":len(messages),"last_message_id":messages[-1]["message_id"] if messages else ""})
