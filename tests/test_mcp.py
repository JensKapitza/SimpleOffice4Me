import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.mcp_auth import create_token
from app.document_store import DocumentStore


class McpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "MCP_ENABLED")}
        app.config.update(TESTING=True, MCP_ENABLED=True, DATABASE=str(Path(self.temp.name) / "mcp.sqlite"), DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"))
        with app.app_context():
            database.ensure_auth_database(); db=database.get_db()
            db.execute("INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",("mcp-user",generate_password_hash("secure-password"))); db.commit()
            self.user_id=db.execute("SELECT id FROM user WHERE username='mcp-user'").fetchone()[0]
            self.read_token,_=create_token(self.user_id,"read",False,30)
            self.write_token,_=create_token(self.user_id,"write",True,30)
        self.client=app.test_client()

    def tearDown(self):
        app.config.update(self.saved); self.temp.cleanup()

    def rpc(self, token, method, params=None):
        return self.client.post("/mcp",json={"jsonrpc":"2.0","id":1,"method":method,**({"params":params} if params is not None else {})},headers={"Authorization":f"Bearer {token}"})

    def test_initialize_and_tool_catalog_have_no_delete_operation(self):
        initialized=self.rpc(self.read_token,"initialize",{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1"}})
        self.assertEqual(200,initialized.status_code); self.assertEqual("2025-06-18",initialized.json["result"]["protocolVersion"])
        listed=self.rpc(self.read_token,"tools/list").json["result"]["tools"]
        names={tool["name"] for tool in listed}
        self.assertIn("mark_document_deletion_candidate",names)
        self.assertFalse(any("delete" in name or "remove" in name for name in names))
        marker=next(tool for tool in listed if tool["name"]=="mark_document_deletion_candidate")
        self.assertFalse(marker["annotations"]["destructiveHint"])

    def test_missing_revoked_and_disabled_credentials_are_denied(self):
        self.assertEqual(401,self.rpc("invalid","tools/list").status_code)
        with app.app_context():
            database.get_db().execute("UPDATE mcp_token SET revoked_at=CURRENT_TIMESTAMP WHERE token_hash=(SELECT token_hash FROM mcp_token WHERE name='read')"); database.get_db().commit()
        self.assertEqual(401,self.rpc(self.read_token,"tools/list").status_code)
        with app.app_context():
            database.get_db().execute("UPDATE user SET is_disabled=1 WHERE id=?",(self.user_id,)); database.get_db().commit()
        self.assertEqual(401,self.rpc(self.write_token,"tools/list").status_code)

    def test_read_token_cannot_write_and_operation_log_contains_no_arguments(self):
        response=self.rpc(self.read_token,"tools/call",{"name":"create_project","arguments":{"title":"Sensitive title","description":"password=do-not-log"}})
        self.assertTrue(response.json["result"]["isError"])
        with app.app_context():
            row=database.get_db().execute("SELECT tool,outcome,error_type,target_id FROM mcp_operation ORDER BY id DESC").fetchone()
            serialized=" ".join(str(value) for value in row)
        self.assertEqual(("create_project","denied","PermissionError",""),tuple(row))
        self.assertNotIn("Sensitive",serialized); self.assertNotIn("do-not-log",serialized)

    def test_feature_denial_hides_tool_and_rejects_direct_call(self):
        with app.app_context():
            database.get_db().execute("INSERT INTO user_permission(user_id,feature,enabled,updated_at) VALUES (?,?,0,CURRENT_TIMESTAMP)",(self.user_id,"projects")); database.get_db().commit()
        names={tool["name"] for tool in self.rpc(self.write_token,"tools/list").json["result"]["tools"]}
        self.assertNotIn("create_project",names)
        result=self.rpc(self.write_token,"tools/call",{"name":"create_project","arguments":{"title":"Denied"}}).json["result"]
        self.assertTrue(result["isError"])

    def test_write_token_creates_project_and_audits_success(self):
        response=self.rpc(self.write_token,"tools/call",{"name":"create_project","arguments":{"title":"MCP project","description":"Created safely"}})
        self.assertFalse(response.json["result"].get("isError",False))
        project=response.json["result"]["structuredContent"]
        self.assertEqual("MCP project",project["title"])
        with app.app_context():
            operation=database.get_db().execute("SELECT tool,outcome,target_id FROM mcp_operation ORDER BY id DESC").fetchone()
            event=database.get_db().execute("SELECT action,target_id FROM security_event WHERE action='mcp_tool_call' ORDER BY id DESC").fetchone()
        self.assertEqual(("create_project","success",""),tuple(operation))
        self.assertEqual(("mcp_tool_call","create_project"),tuple(event))

    def test_token_settings_show_secret_once_and_revoke_only_own_token(self):
        self.client.post("/auth/login",data={"username":"mcp-user","password":"secure-password"})
        created=self.client.post("/settings/mcp",data={"name":"ChatGPT","days":"7","can_write":"1"})
        self.assertEqual(200,created.status_code); self.assertIn("so_mcp_",created.get_data(as_text=True))
        listing=self.client.get("/settings/mcp").get_data(as_text=True)
        self.assertNotIn(self.write_token,listing); self.assertIn("ChatGPT",listing); self.assertIn("Verarbeitungsjournal",listing)

    def test_ai_deletion_candidate_only_tags_and_notes_existing_file(self):
        with app.app_context():
            document=DocumentStore(app.config["DOCUMENT_ROOT"]).import_upload(BytesIO(b"keep me"),"keep.txt","mcp-user")
        response=self.rpc(self.write_token,"tools/call",{"name":"mark_document_deletion_candidate","arguments":{"document_id":document["document_id"],"reason":"Der Inhalt wirkt wie ein manuell zu prüfendes Duplikat."}})
        self.assertFalse(response.json["result"].get("isError",False))
        with app.app_context(): updated=DocumentStore(app.config["DOCUMENT_ROOT"]).get_document(document["document_id"])
        self.assertIn("ai-delete-candidate",updated["tags"])
        self.assertIn("keine Löschung ausgeführt",updated["notes"][-1]["text"])
        self.assertTrue((Path(app.config["DOCUMENT_ROOT"])/updated["last_path"]).exists())


if __name__ == "__main__": unittest.main()
