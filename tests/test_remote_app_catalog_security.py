from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app.companion_agent import install, status
from app.password_security import hash_password
from app.remote_app_catalog import RemoteAppCatalog


class RemoteAppCatalogSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.executable = base / "trusted.exe"
        self.executable.write_bytes(b"synthetic executable fixture")
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            cursor = db.execute(
                "INSERT INTO user(username,password,display_name,is_admin,is_disabled,"
                "auth_version,created_at,updated_at) VALUES(?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("ordinary-user", hash_password("Remote-App-Test-Passphrase-42"), "Ordinary User"),
            )
            self.user_id = cursor.lastrowid
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "ordinary-user", "password": "Remote-App-Test-Passphrase-42"},
        )
        self.assertIn(response.status_code, {200, 302})

    def tearDown(self) -> None:
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_non_admin_cannot_publish_an_executable_path(self) -> None:
        response = self.client.post(
            "/admin/remote/apps",
            data={"label": "Synthetic App", "executable": str(self.executable)},
        )

        self.assertEqual(403, response.status_code)
        self.assertEqual([], RemoteAppCatalog(self.root).all())

    def test_catalog_launch_resolves_only_the_published_unchanged_executable(self) -> None:
        catalog = RemoteAppCatalog(self.root)
        published = catalog.publish(label="Synthetic App", executable=str(self.executable))

        self.assertEqual(str(self.executable.resolve()), catalog.launch_spec(published["app_id"])["executable"])
        self.executable.write_bytes(b"changed executable fixture")
        with self.assertRaisesRegex(ValueError, "muss erneut veröffentlicht werden"):
            catalog.launch_spec(published["app_id"])

    def test_catalog_rejects_shell_and_relative_executable_paths(self) -> None:
        catalog = RemoteAppCatalog(self.root)
        shell = self.root / "cmd.exe"
        shell.parent.mkdir(parents=True, exist_ok=True)
        shell.write_bytes(b"synthetic shell fixture")

        with self.assertRaises(ValueError):
            catalog.publish(label="Shell", executable=str(shell))
        with self.assertRaises(ValueError):
            catalog.publish(label="Relative", executable="trusted.exe")
        self.assertEqual([], catalog.all())

    def test_user_catalog_requires_login_and_has_no_mutation_route(self) -> None:
        anonymous = app.test_client().get("/remote/apps")
        self.assertEqual(302, anonymous.status_code)
        self.assertTrue(anonymous.location.endswith("/auth/login"))
        catalog_routes = list(app.url_map.iter_rules("remote_apps.index"))
        self.assertEqual({"GET", "HEAD", "OPTIONS"}, catalog_routes[0].methods)
        self.assertIn(self.client.post("/remote/apps").status_code, {404, 405})

    def test_catalog_renders_only_explicit_grants_with_safe_display_fields(self) -> None:
        catalog = RemoteAppCatalog(self.root)
        visible = catalog.publish(
            label="Shared <b>Editor</b>", executable=str(self.executable),
            description='<img src="x">', allowed_user_ids=[self.user_id], favorite=True,
        )
        catalog.publish(label="Another account only", executable=str(self.executable), allowed_user_ids=[self.user_id + 500])
        catalog.publish(label="Administration only", executable=str(self.executable))
        disabled = catalog.publish(label="Disabled editor", executable=str(self.executable), allowed_user_ids=[self.user_id])
        catalog.update(disabled["app_id"], enabled=False)
        bad_description = {
            "app_id": "malformed", "label": "Another shared editor", "allowed_user_ids": [self.user_id],
            "description": {"private": "private-malformed-description"},
        }
        data = json.loads(catalog.path.read_text(encoding="utf-8"))
        data["apps"].append(bad_description)
        catalog.path.write_text(json.dumps(data), encoding="utf-8")

        # These query parameters do not affect the signed-in user's access.
        response = self.client.get("/remote/apps", query_string={"user_id": self.user_id + 500, "is_admin": 1})
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.cache_control.private)
        self.assertTrue(response.cache_control.no_store)
        body = response.get_data(as_text=True)
        self.assertIn('href="/remote/apps"', body)
        self.assertIn("Shared &lt;b&gt;Editor&lt;/b&gt;", body)
        self.assertIn("&lt;img src=", body)
        self.assertIn("Another shared editor", body)
        for hidden in ("Another account only", "Administration only", "Disabled editor",
                       str(self.executable), visible["sha256"], visible["app_id"], '<img src="x">',
                       "private-malformed-description"):
            self.assertNotIn(hidden, body)

        catalog.update(visible["app_id"], allowed_user_ids=[])
        catalog.remove("malformed")
        with self.client.session_transaction() as session:
            session["simpleoffice_language"] = "de"
        after_revocation = self.client.get("/remote/apps").get_data(as_text=True)
        self.assertNotIn("Shared &lt;b&gt;Editor", after_revocation)
        self.assertIn("Für dein Konto sind keine Anwendungen freigegeben", after_revocation)

    def test_catalog_handles_all_agent_availability_states(self) -> None:
        RemoteAppCatalog(self.root).publish(
            label="Shared synthetic editor", executable=str(self.executable), allowed_user_ids=[self.user_id],
        )
        active = {"platform": "windows", "installed": True, "running": True, "stop_pending": False,
                  "pid": 93014, "started_at": "hidden-operational-start-time"}
        cases = {
            "active": active,
            "offline": {**active, "running": False},
            "missing": {**active, "installed": False, "running": False},
            "stopping": {**active, "stop_pending": True},
            "unsupported": {**active, "platform": "linux"},
            "unknown": OSError("private runtime probe details"),
        }
        for expected, agent_state in cases.items():
            with self.subTest(expected=expected):
                if isinstance(agent_state, Exception):
                    mocked_status = patch("app.remote_apps.status", side_effect=agent_state)
                else:
                    mocked_status = patch("app.remote_apps.status", return_value=agent_state)
                with mocked_status:
                    response = self.client.get("/remote/apps")
                self.assertEqual(200, response.status_code)
                body = response.get_data(as_text=True)
                self.assertIn(f'data-agent-status="{expected}"', body)
                self.assertIn("Shared synthetic editor", body)
                self.assertNotIn("hidden-operational-start-time", body)
                self.assertNotIn("private runtime probe details", body)

    def test_admin_catalog_and_user_view_preserve_distinct_scope(self) -> None:
        catalog = RemoteAppCatalog(self.root)
        catalog.publish(label="Administration only", executable=str(self.executable))
        with app.app_context():
            db = database.get_db()
            db.execute("UPDATE user SET is_admin=1 WHERE id=?", (self.user_id,))
            db.commit()
        self.assertNotIn("Administration only", self.client.get("/remote/apps").get_data(as_text=True))
        self.assertIn("Administration only", self.client.get("/admin/remote").get_data(as_text=True))

    def test_reading_missing_or_malformed_catalog_does_not_grant_access(self) -> None:
        catalog = RemoteAppCatalog(self.root / "absent")
        self.assertEqual([], catalog.visible_for(self.user_id))
        self.assertFalse(catalog.control.exists())
        catalog.control.mkdir(parents=True)
        malformed = {"apps": [None, {"label": "Malformed grant", "allowed_user_ids": str(self.user_id)}]}
        catalog.path.write_text(json.dumps(malformed), encoding="utf-8")
        self.assertEqual([], catalog.visible_for(self.user_id))

    def test_agent_status_read_does_not_create_state(self) -> None:
        root = self.root / "agent-state"
        self.assertFalse(status(root)["installed"])
        self.assertFalse(root.exists())
        self.assertTrue(install(root)["installed"])
        self.assertTrue((root / ".simpleoffice-meta" / "companion-agent.json").is_file())

    def test_catalog_uses_heartbeat_without_running_a_process_probe(self) -> None:
        control = self.root / ".simpleoffice-meta"
        control.mkdir(parents=True, exist_ok=True)
        state = {"installed": True, "pid": 12345, "instance_token": "synthetic-instance"}
        heartbeat_path = control / "companion-agent-heartbeat.json"
        (control / "companion-agent.json").write_text(json.dumps(state), encoding="utf-8")
        with patch("app.companion_agent.platform_name", return_value="windows"), patch(
            "app.companion_agent._pid_alive", side_effect=AssertionError("user view must not probe the process"),
        ):
            for expected, age in (("active", 0), ("offline", 60)):
                with self.subTest(expected=expected):
                    heartbeat = {**state, "heartbeat_at": int(time.time()) - age}
                    heartbeat_path.write_text(json.dumps(heartbeat), encoding="utf-8")
                    body = self.client.get("/remote/apps").get_data(as_text=True)
                    self.assertIn(f'data-agent-status="{expected}"', body)
                    self.assertNotIn("synthetic-instance", body)


if __name__ == "__main__":
    unittest.main()
