"""Permission-aware Streamable HTTP MCP server for SimpleOffice4Me."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, url_for

from .access_control import FEATURES, audit, has_feature, permissions_for
from .auth import login_required
from .calendar_store import CalendarStore
from .contact_store import ContactStore
from .document_store import DocumentStore
from .db import get_db
from .mcp_auth import authenticate_token, create_token, operation_log, revoke_token, tokens_for_user
from .project_store import ProjectStore
from .virtual_filesystem import VirtualFileSystem

bp = Blueprint("mcp", __name__)
PROTOCOL = "2025-06-18"
MAX_ARGUMENT_TEXT = 100_000


def _schema(properties=None, required=None):
    return {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}


TOOLS = [
    {"name":"get_capabilities","description":"List the SimpleOffice functions enabled for the current user.","inputSchema":_schema(),"annotations":{"readOnlyHint":True,"openWorldHint":False}},
    {"name":"search","description":"Search document metadata and indexed text visible to the user.","inputSchema":_schema({"query":{"type":"string","minLength":1,"maxLength":500},"limit":{"type":"integer","minimum":1,"maximum":50}},["query"]),"annotations":{"readOnlyHint":True,"openWorldHint":False}},
    {"name":"fetch","description":"Fetch metadata for one visible document by its stable document ID.","inputSchema":_schema({"document_id":{"type":"string","minLength":1,"maxLength":100}},["document_id"]),"annotations":{"readOnlyHint":True,"openWorldHint":False}},
    {"name":"tag_document","description":"Add tags to a visible, writable document. This never deletes a file.","inputSchema":_schema({"document_id":{"type":"string","minLength":1,"maxLength":100},"tags":{"type":"array","items":{"type":"string","minLength":1,"maxLength":80},"minItems":1,"maxItems":20}},["document_id","tags"]),"annotations":{"readOnlyHint":False,"destructiveHint":False,"openWorldHint":False}},
    {"name":"mark_document_deletion_candidate","description":"Mark, but never delete, a document as an AI deletion candidate and append the mandatory immutable rationale note.","inputSchema":_schema({"document_id":{"type":"string","minLength":1,"maxLength":100},"reason":{"type":"string","minLength":10,"maxLength":2000}},["document_id","reason"]),"annotations":{"readOnlyHint":False,"destructiveHint":False,"openWorldHint":False}},
    {"name":"list_calendar_events","description":"List calendar events visible to the user.","inputSchema":_schema({"calendar_id":{"type":"string","maxLength":100},"limit":{"type":"integer","minimum":1,"maximum":200}}),"annotations":{"readOnlyHint":True,"openWorldHint":False}},
    {"name":"create_calendar_event","description":"Create a calendar event owned by the user.","inputSchema":_schema({"title":{"type":"string","minLength":1,"maxLength":300},"description":{"type":"string","maxLength":20000},"start":{"type":"string"},"end":{"type":"string"},"visibility":{"type":"string","enum":["private","family","external"]},"calendar_id":{"type":"string","maxLength":100}},["title","start","end"]),"annotations":{"readOnlyHint":False,"destructiveHint":False,"openWorldHint":False}},
    {"name":"search_contacts","description":"Search contacts visible to the user.","inputSchema":_schema({"query":{"type":"string","maxLength":300},"limit":{"type":"integer","minimum":1,"maximum":100}}),"annotations":{"readOnlyHint":True,"openWorldHint":False}},
    {"name":"upsert_contact","description":"Create or update a contact visible to the user.","inputSchema":_schema({"contact_id":{"type":"string","maxLength":100},"fields":{"type":"object","additionalProperties":{"type":"string"}}},["fields"]),"annotations":{"readOnlyHint":False,"destructiveHint":False,"openWorldHint":False}},
    {"name":"list_projects","description":"List projects.","inputSchema":_schema({"limit":{"type":"integer","minimum":1,"maximum":100}}),"annotations":{"readOnlyHint":True,"openWorldHint":False}},
    {"name":"create_project","description":"Create a project.","inputSchema":_schema({"title":{"type":"string","minLength":1,"maxLength":300},"description":{"type":"string","maxLength":20000},"status":{"type":"string","enum":["open","active","waiting","completed","cancelled"]}},["title"]),"annotations":{"readOnlyHint":False,"destructiveHint":False,"openWorldHint":False}},
    {"name":"add_project_task","description":"Add a task to a project.","inputSchema":_schema({"project_id":{"type":"string"},"title":{"type":"string","minLength":1,"maxLength":300},"description":{"type":"string","maxLength":20000}},["project_id","title"]),"annotations":{"readOnlyHint":False,"destructiveHint":False,"openWorldHint":False}},
    {"name":"book_project_time","description":"Book project task time in exact minutes.","inputSchema":_schema({"project_id":{"type":"string"},"task_id":{"type":"string"},"date":{"type":"string"},"minutes":{"type":"integer","minimum":1,"maximum":1440},"note":{"type":"string","maxLength":2000}},["project_id","task_id","date","minutes"]),"annotations":{"readOnlyHint":False,"destructiveHint":False,"openWorldHint":False}},
]
TOOL_FEATURE = {"search":"documents","fetch":"documents","tag_document":"documents","mark_document_deletion_candidate":"documents","list_calendar_events":"calendar","create_calendar_event":"calendar","search_contacts":"contacts","upsert_contact":"contacts","list_projects":"projects","create_project":"projects","add_project_task":"projects","book_project_time":"projects"}
WRITE_TOOLS = {"tag_document","mark_document_deletion_candidate","create_calendar_event","upsert_contact","create_project","add_project_task","book_project_time"}


def _root(): return current_app.config["DOCUMENT_ROOT"]
def _actor(): return str(g.user["username"])
def _bounded(value, default, upper): return max(1, min(int(value or default), upper))


def _authenticate():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    identity = authenticate_token(header[7:].strip())
    if identity is not None:
        g.user = identity
        g.mcp_identity = identity
    return identity


def _result(value):
    return {"content":[{"type":"text","text":__import__("json").dumps(value, ensure_ascii=False)}],"structuredContent":value}


def _visible_document(document_id):
    store = DocumentStore(_root()); item = store.get_document(document_id)
    path = item.get("last_path", "")
    if not path or not VirtualFileSystem.from_environment(_root()).allows(_actor(), path, "read"):
        raise PermissionError("document access denied")
    return item


def _call(name, args):
    feature = TOOL_FEATURE.get(name)
    if feature and not has_feature(g.user, feature): raise PermissionError("function disabled for this user")
    if name in WRITE_TOOLS and not g.mcp_identity["can_write"]: raise PermissionError("MCP token is read-only")
    if name == "get_capabilities":
        enabled = permissions_for(g.user["id"]); return {"features":{FEATURES[k]:v for k,v in enabled.items()},"tools":[t["name"] for t in TOOLS if not TOOL_FEATURE.get(t["name"]) or enabled[TOOL_FEATURE[t["name"]]]],"write_enabled":bool(g.mcp_identity["can_write"])}
    if name == "search":
        rows=DocumentStore(_root()).search(str(args["query"]),_bounded(args.get("limit"),20,50)); out=[]
        for row in rows:
            try:
                item=_visible_document(row["document_id"]); out.append({"id":item["document_id"],"title":item.get("title") or item.get("original_name") or row.get("path"),"path":item.get("last_path"),"state":item.get("state"),"tags":item.get("tags",[])})
            except (ValueError,PermissionError): pass
        return {"results":out}
    if name == "fetch":
        item=_visible_document(str(args["document_id"])); allowed={k:item.get(k) for k in ("document_id","title","original_name","last_path","mime_type","size","state","tags","created_at","last_seen_at","text_extraction")}; return allowed
    if name in {"tag_document","mark_document_deletion_candidate"}:
        item=_visible_document(str(args["document_id"])); vfs=VirtualFileSystem.from_environment(_root())
        if not vfs.allows(_actor(),item["last_path"],"write"): raise PermissionError("document write access denied")
        store=DocumentStore(_root()); tags=[str(value).strip() for value in item.get("tags",[]) if str(value).strip()]
        if name=="tag_document": additions=[str(value).strip() for value in args["tags"] if str(value).strip()]
        else:
            additions=["ai-delete-candidate"]
            store.add_note(item["document_id"],"KI-Löschvorschlag (keine Löschung ausgeführt): "+str(args["reason"]).strip(),_actor())
        return store.set_tags(item["document_id"],list(dict.fromkeys([*tags,*additions])),_actor())
    if name == "list_calendar_events": return {"events":CalendarStore(_root()).events(_actor(),str(args.get("calendar_id","")))[:_bounded(args.get("limit"),100,200)]}
    if name == "create_calendar_event": return CalendarStore(_root()).add(str(args["title"]),str(args.get("description","")),str(args["start"]),str(args["end"]),"",_actor(),str(args.get("visibility","private")),"",[],calendar_id=str(args.get("calendar_id","default")))
    if name == "search_contacts": return {"contacts":ContactStore(_root()).search(str(args.get("query","")),_actor())[:_bounded(args.get("limit"),50,100)]}
    if name == "upsert_contact": return ContactStore(_root()).upsert({str(k):str(v) for k,v in args["fields"].items()},_actor(),str(args.get("contact_id","")))
    if name == "list_projects": return {"projects":ProjectStore(_root()).projects()[:_bounded(args.get("limit"),50,100)]}
    if name == "create_project": return ProjectStore(_root()).create_project(args,_actor())
    if name == "add_project_task": return ProjectStore(_root()).add_task(str(args["project_id"]),args,_actor())
    if name == "book_project_time":
        minutes=int(args["minutes"]); return ProjectStore(_root()).book_time(str(args["project_id"]),str(args["task_id"]),str(args["date"]),minutes//60,str(args.get("note","")),_actor(),minutes%60)
    raise ValueError("unknown tool")


@bp.post("/mcp")
def endpoint():
    if current_app.config.get("MCP_ENABLED", True) is not True: abort(404)
    if not (request.is_secure or current_app.testing or request.remote_addr in {"127.0.0.1", "::1"}):
        return jsonify({"jsonrpc":"2.0","error":{"code":-32002,"message":"HTTPS required"},"id":None}),403
    identity=_authenticate()
    if identity is None: return jsonify({"jsonrpc":"2.0","error":{"code":-32001,"message":"Unauthorized"},"id":None}),401
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict): return jsonify({"jsonrpc":"2.0","error":{"code":-32700,"message":"Parse error"},"id":None}),400
    rpc_id=payload.get("id"); method=payload.get("method")
    if method=="initialize": data={"protocolVersion":PROTOCOL,"capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"SimpleOffice4Me","version":"1.0"}}
    elif method=="ping": data={}
    elif method=="tools/list": data={"tools":[t for t in TOOLS if not TOOL_FEATURE.get(t["name"]) or has_feature(g.user,TOOL_FEATURE[t["name"]])]}
    elif method=="tools/call":
        params=payload.get("params",{}); name=str(params.get("name","")); args=params.get("arguments",{})
        if not isinstance(args,dict) or len(str(args))>MAX_ARGUMENT_TEXT: return jsonify({"jsonrpc":"2.0","error":{"code":-32602,"message":"Invalid params"},"id":rpc_id}),400
        try:
            value=_call(name,args); data=_result(value); audit("mcp_tool_call","mcp_tool",name,detail={"token_id":identity["token_id"],"request_id":g.request_id})
            get_db().execute("INSERT INTO mcp_operation(request_id,occurred_at,actor_id,token_id,tool,target_id,outcome) VALUES (?,datetime('now'),?,?,?,?,?)",(g.request_id,g.user["id"],identity["token_id"],name,str(args.get("document_id") or args.get("project_id") or ""),"success")); get_db().commit()
        except (KeyError,TypeError,ValueError,PermissionError) as exc:
            audit("mcp_tool_call","mcp_tool",name,outcome="denied",detail={"token_id":identity["token_id"],"request_id":g.request_id}); get_db().execute("INSERT INTO mcp_operation(request_id,occurred_at,actor_id,token_id,tool,target_id,outcome,error_type) VALUES (?,datetime('now'),?,?,?,?,?,?)",(g.request_id,g.user["id"],identity["token_id"],name,str(args.get("document_id") or args.get("project_id") or ""),"denied",type(exc).__name__)); get_db().commit(); data={"content":[{"type":"text","text":str(exc)}],"isError":True}
    elif method and method.startswith("notifications/"): return ("",202)
    else: return jsonify({"jsonrpc":"2.0","error":{"code":-32601,"message":"Method not found"},"id":rpc_id})
    return jsonify({"jsonrpc":"2.0","result":data,"id":rpc_id})


@bp.route("/settings/mcp",methods=["GET","POST"])
@login_required
def settings():
    secret=None
    if request.method=="POST":
        secret,_=create_token(g.user["id"],request.form.get("name",""),request.form.get("can_write")=="1",request.form.get("days",30)); audit("mcp_token_created","mcp_token",detail={"write":request.form.get("can_write")=="1"})
    return render_template("mcp/settings.html",tokens=tokens_for_user(g.user["id"]),operations=operation_log(g.user["id"],bool(g.user["is_admin"])),secret=secret)


@bp.post("/settings/mcp/<int:token_id>/revoke")
@login_required
def revoke(token_id):
    if revoke_token(g.user["id"],token_id): audit("mcp_token_revoked","mcp_token",str(token_id))
    return redirect(url_for("mcp.settings"))
