"""Cross-capability release checks against a migrated fixture, not external acceptance."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import app, db
from app.calendar_collections import CalendarCollections
from app.contact_store import ContactStore
from app.document_store import DocumentStore
from app.s3_overlay import credentials
from app.settings_store import SettingsStore
from app.todo_store import TodoStore
from app.v2.cutover import activate_v2, prepare_shadow
from app.v2.migration import create_migration_backup, transfer_legacy_documents
from app.v3_automation import AutomationEngine, AutomationStore, default_action_registry
from app.v3_capabilities import snapshot
from app.v3_jobs import JobStore
from app.webdav import activate
from test_s3_overlay import signed_headers


class V3ReleaseGateMatrixTests(unittest.TestCase):
    def test_migrated_domains_survive_capability_combinations_and_rollback(self):
        saved = dict(app.config)
        self.addCleanup(lambda: app.config.update(saved))
        flags = {"SIMPLEOFFICE_" + r["key"].replace(".", "_").upper() + "_ENABLED": "0" for r in snapshot({})}
        profiles = [
            ("all-off", []),
            ("relations-events", ["relations", "activity"]),
            ("ui-no-automation", ["search", "entity_context", "workboard", "crm", "finance", "health"]),
            ("automation-no-jobs", ["automation"]),
            ("automation-queued-jobs", ["automation", "jobs"]),
            ("s3-on", []),
            ("legacy-and-v3-federation", ["federation"]),
            ("android-without-grant", ["android_offline"]),
            ("optional-services-absent", ["health", "extensions"]),
        ]
        all_keys = [r["key"].split(".", 1)[1] for r in snapshot({})]
        profiles += [("all-on", all_keys)]
        profiles += [("disable-" + key, [value for value in all_keys if value != key]) for key in all_keys]
        profiles += [("rollback-all-off", [])]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "documents"
            root.mkdir()
            source = root / "legacy.txt"
            source.write_bytes(b"legacy-release-matrix-content")
            app.config.update(
                TESTING=True,
                DOCUMENT_ROOT=str(root),
                DATABASE=str(Path(tmp) / "users.sqlite"),
                WEBDAV_UPLOAD_SCAN=False,
            )
            with app.app_context():
                db.ensure_auth_database()
                conn = db.get_db()
                conn.execute("INSERT INTO user(username,password,is_admin) VALUES('admin','unused',1)")
                conn.execute("INSERT INTO user(username,password,is_admin) VALUES('limited','unused',0)")
                conn.execute(
                    "INSERT INTO user_permission(user_id,feature,enabled,updated_at) SELECT id,'documents',0,CURRENT_TIMESTAMP FROM user WHERE username='limited'"
                )
                conn.execute(
                    "INSERT INTO user_permission(user_id,feature,enabled,updated_at) SELECT id,'projects',0,CURRENT_TIMESTAMP FROM user WHERE username='limited'"
                )
                conn.commit()
                admin = conn.execute("SELECT id FROM user WHERE username='admin'").fetchone()[0]
                limited = conn.execute("SELECT id FROM user WHERE username='limited'").fetchone()[0]
            store = DocumentStore(root)
            store.scan()
            doc = store.get_document(source)
            with app.test_request_context():
                password = activate("admin", "admin", label="Release matrix")
            auth = {"Authorization": "Basic " + base64.b64encode(("admin:" + password).encode()).decode()}
            ContactStore(root).activate_carddav("admin", "matrix-dav-password", "admin")
            CalendarCollections(root).activate("admin", "matrix-dav-password", "admin")
            dav = {"Authorization": "Basic " + base64.b64encode(b"admin:matrix-dav-password").decode()}
            client = app.test_client()
            with app.app_context():
                keypair = credentials.create("admin", "Release matrix", ["read"], "", 7)
            with client.session_transaction() as session:
                session["user_id"] = admin
            vcard = "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Matrix Contact\r\nEND:VCARD\r\n"
            event = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Matrix//EN\r\nBEGIN:VEVENT\r\nUID:matrix-event\r\nDTSTAMP:20261002T120000Z\r\nDTSTART:20261003T120000Z\r\nDTEND:20261003T130000Z\r\nSUMMARY:Matrix Event\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
            task = (
                event.replace("VEVENT", "VTODO")
                .replace("DTSTART:20261003T120000Z\r\nDTEND:20261003T130000Z", "DUE:20261003T130000Z")
                .replace("matrix-event", "matrix-task")
            )
            urls = [
                ("/carddav/addressbooks/admin/default/matrix.vcf", vcard),
                ("/caldav/calendars/admin/default/matrix.ics", event),
                ("/caldav/calendars/admin/tasks/matrix.ics", task),
            ]
            for url, data in urls:
                response = client.put(url, data=data, headers=dav)
                self.assertTrue(response.status_code == 201, (url, response.status_code))
            standards = {}
            for url, _data in urls:
                response = client.get(url, headers=dav)
                self.assertEqual(200, response.status_code)
                standards[url] = (response.data, response.headers["ETag"])
            backup = Path(tmp) / "backup"
            create_migration_backup(root, backup)
            transfer_legacy_documents(root, backup)
            prepare_shadow(root, apply=True, acknowledge_local_plaintext=True)
            activate_v2(root, apply=True, acknowledge_local_plaintext=True)
            rule = AutomationStore(root).create(
                "admin",
                {
                    "name": "Matrix Automation",
                    "enabled": True,
                    "scope": {},
                    "trigger": {"type": "event", "name": "matrix.event"},
                    "conditions": [],
                    "actions": [{"type": "task.create", "payload": {"title": "Matrix Automation Task"}}],
                },
            )
            job = JobStore(root).enqueue("matrix", {}, "admin", idempotency_key="matrix")
            for name, active in profiles:
                env = dict(
                    flags,
                    SIMPLEOFFICE_FEDERATION_TOKEN="matrix-federation-token",
                    SIMPLEOFFICE_MAIL_CASE_FEDERATION_WORKER="0",
                )
                for key in active:
                    env["SIMPLEOFFICE_V3_" + key.upper() + "_ENABLED"] = "1"
                settings_store = SettingsStore(root)
                settings = settings_store.settings()
                settings["s3"]["enabled"] = name == "s3-on"
                settings_store.save(settings, "release-matrix")
                with patch.dict(os.environ, env), self.subTest(profile=name):
                    with app.app_context():
                        db.ensure_auth_database()
                        db.ensure_auth_database()
                    for url, _data in urls:
                        response = client.get(url, headers=dav)
                        self.assertTrue(response.status_code == 200, (name, url, response.status_code))
                        self.assertEqual(standards[url], (response.data, response.headers["ETag"]), (name, url))
                    response = client.get("/webdav/files/admin/legacy.txt", headers=auth)
                    self.assertTrue(response.status_code == 200, (name, "webdav", response.status_code))
                    self.assertTrue(response.data == b"legacy-release-matrix-content")
                    response.close()
                    response = client.get("/documents/" + doc["document_id"])
                    self.assertTrue(response.status_code == 200, (name, "document", response.status_code))
                    s3_path = f"/s3/simpleoffice/documents/{doc['document_id']}/original/legacy.txt"
                    response = client.get(
                        s3_path, headers=signed_headers("GET", s3_path, keypair["access_key"], keypair["secret_key"])
                    )
                    self.assertEqual(200 if name == "s3-on" else 404, response.status_code, name)
                    if name == "s3-on":
                        self.assertEqual(b"legacy-release-matrix-content", response.data)
                    response.close()
                    for url in ["/automation-v3", "/workboard-v3", "/admin/v3/jobs", "/admin/v3/health"]:
                        response = client.get(url)
                        self.assertTrue(response.status_code == 200, (name, url, response.status_code))
                    if "automation" not in active:
                        self.assertEqual(404, client.post("/automation-v3", data={"name": "blocked"}).status_code)
                    if "jobs" not in active:
                        self.assertEqual(404, client.post(f"/admin/v3/jobs/{job.job_id}/retry").status_code)
                    response = client.get("/api/v3/search?q=legacy")
                    self.assertTrue(
                        response.status_code == (200 if "search" in active else 404),
                        (name, "search", response.status_code),
                    )
                    if "search" in active:
                        self.assertIn(doc["document_id"], [row["ref_id"] for row in response.json["results"]])
                    response = client.get("/v3/entity/document/" + doc["document_id"])
                    self.assertTrue(
                        response.status_code == (200 if "entity_context" in active else 404),
                        (name, "context", response.status_code),
                    )
                    response = client.get(
                        "/federation/v1/capabilities", headers={"Authorization": "Bearer matrix-federation-token"}
                    )
                    self.assertTrue(response.status_code == 200, (name, "legacy federation", response.status_code))
                    response = client.get(
                        "/federation/v3/capabilities", headers={"Authorization": "Bearer matrix-federation-token"}
                    )
                    self.assertTrue(
                        response.status_code == (200 if "federation" in active else 404),
                        (name, "v3 federation", response.status_code),
                    )
                    with client.session_transaction() as session:
                        session["user_id"] = limited
                    response = client.get("/documents/" + doc["document_id"])
                    self.assertTrue(response.status_code == 403, (name, "document denial", response.status_code))
                    response = client.get("/v3/entity/document/" + doc["document_id"])
                    self.assertEqual(404, response.status_code)
                    if "search" in active:
                        response = client.get("/api/v3/search?q=legacy")
                        self.assertEqual(200, response.status_code)
                        self.assertNotIn(doc["document_id"], [row["ref_id"] for row in response.json["results"]])
                    response = client.get("/api/v3/android-offline/policy")
                    self.assertTrue(
                        response.status_code == (403 if "android_offline" in active else 404),
                        (name, "offline denial", response.status_code),
                    )
                    with client.session_transaction() as session:
                        session["user_id"] = admin
                    if name == "automation-no-jobs":
                        result = AutomationEngine(AutomationStore(root), default_action_registry(root)).execute(
                            rule, {"name": "matrix.event"}, principal="admin", correlation_id="matrix.event"
                        )
                        self.assertTrue(result.status == "succeeded", result)
                    self.assertTrue(JobStore(root).get(job.job_id).state == "queued")
                    self.assertTrue(AutomationStore(root).get(rule.rule_id, "admin").rule_id == rule.rule_id)
                    self.assertTrue(source.read_bytes() == b"legacy-release-matrix-content")
            self.assertEqual(2, len(TodoStore(root).items("admin")))


if __name__ == "__main__":
    unittest.main()
