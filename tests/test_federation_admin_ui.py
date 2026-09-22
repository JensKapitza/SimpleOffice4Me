import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.federation_store import FederationStore


class FederationAdminUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {
            key: app.config.get(key)
            for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")
        }
        app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"),
            DOCUMENT_ROOT=str(self.root),
        )
        self.root.mkdir(parents=True, exist_ok=True)
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            db.execute(
                "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                "VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("federation-ui-admin", generate_password_hash("federation-ui-password")),
            )
            db.commit()
        self.client = app.test_client()
        response = self.client.post(
            "/auth/login",
            data={"username": "federation-ui-admin", "password": "federation-ui-password"},
        )
        self.assertLess(response.status_code, 400)

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_task_focused_views_render(self):
        expected = {
            "overview": "Was möchtest du tun?",
            "files": "Lokale Dateien",
            "peers": "Verbundene Peers",
            "transfers": "Download-Warteschlange",
        }
        for view, marker in expected.items():
            with self.subTest(view=view):
                response = self.client.get(f"/admin/federation?view={view}")
                self.assertEqual(200, response.status_code)
                self.assertIn(marker, response.get_data(as_text=True))

    def test_peer_form_maps_document_permissions_without_losing_advanced_policy(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "office-b",
                "label": "Office B",
                "base_url": "http://10.0.0.2:8080",
                "enabled": "1",
                "_documents_policy_form": "1",
                "documents_send": "1",
                "documents_seed": "1",
                "policy_json": '{"chat":{"send":true,"receive":false}}',
            },
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        self.assertIn("view=peers", response.headers["Location"])

        peer = FederationStore(self.root).get_peer("office-b")
        self.assertIsNotNone(peer)
        policy = peer["policy"]
        self.assertEqual(
            {"send": True, "receive": False, "seed": True},
            policy["documents"],
        )
        self.assertEqual({"send": True, "receive": False}, policy["chat"])

    def test_edit_peer_prefills_form(self):
        FederationStore(self.root).save_peer(
            "office-b",
            "Office B",
            "http://10.0.0.2:8080",
            "",
            {"documents": {"send": True, "receive": True, "seed": False}},
            True,
        )
        response = self.client.get("/admin/federation?view=peers&edit_peer=office-b")
        body = response.get_data(as_text=True)
        self.assertEqual(200, response.status_code)
        self.assertIn("Peer bearbeiten", body)
        self.assertIn('value="office-b"', body)
        self.assertIn('value="http://10.0.0.2:8080"', body)


if __name__ == "__main__":
    unittest.main()
