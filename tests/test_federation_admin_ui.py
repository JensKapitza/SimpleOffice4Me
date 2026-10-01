import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.federation_store import FederationStore
from app.mail_case_store import MailCaseStore
from app.v3_federation import FederationContractStore


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
            db.execute(
                "INSERT INTO user(username,password,is_admin,is_disabled,created_at,updated_at) "
                "VALUES (?,?,0,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("mail-user", generate_password_hash("unused")),
            )
            db.execute(
                "INSERT INTO user(username,password,is_admin,is_disabled,created_at,updated_at) "
                "VALUES (?,?,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                ("disabled-mail-user", generate_password_hash("unused")),
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

    def test_mail_case_policy_and_explicit_user_mapping(self):
        response = self.client.post(
            "/admin/federation/peers",
            data={
                "peer_id": "mail-peer",
                "label": "Mail Peer",
                "base_url": "http://10.0.0.3:8080",
                "enabled": "1",
                "_mail_cases_policy_form": "1",
                "mail_cases_send": "1",
                "mail_cases_receive": "1",
                "mail_cases_auto_accept": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        peer = FederationStore(self.root).get_peer("mail-peer")
        self.assertEqual(
            {"send": True, "receive": True, "auto_accept": True},
            peer["policy"]["data_classes"]["mail_cases"],
        )

        response = self.client.post(
            "/admin/federation/mail-user-mappings",
            data={
                "peer_id": "mail-peer",
                "remote_user_id": "remote-user-42",
                "local_username": "mail-user",
            },
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        mapping = FederationContractStore(self.root).mail_user_mapping(
            "mail-peer", "remote-user-42"
        )
        self.assertEqual("mail-user", mapping["local_username"])

        page = self.client.get("/admin/federation?view=peers")
        body = page.get_data(as_text=True)
        self.assertIn("remote-user-42", body)
        self.assertIn("mail-user", body)

    def test_disabled_user_cannot_be_mapped(self):
        FederationStore(self.root).save_peer(
            "mail-peer",
            "Mail Peer",
            "http://10.0.0.3:8080",
            "",
            {"data_classes": {"mail_cases": {"receive": True}}},
            True,
        )
        response = self.client.post(
            "/admin/federation/mail-user-mappings",
            data={
                "peer_id": "mail-peer",
                "remote_user_id": "remote-disabled",
                "local_username": "disabled-mail-user",
            },
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("Lokaler Benutzer ist nicht aktiv", response.get_data(as_text=True))
        self.assertIsNone(
            FederationContractStore(self.root).mail_user_mapping(
                "mail-peer", "remote-disabled"
            )
        )

    def test_mapping_revocation_removes_existing_mirror_access(self):
        FederationStore(self.root).save_peer(
            "mail-peer",
            "Mail Peer",
            "http://10.0.0.3:8080",
            "",
            {"data_classes": {"mail_cases": {"receive": True}}},
            True,
        )
        contract_store = FederationContractStore(self.root)
        contract_store.set_mail_user_mapping(
            "mail-peer", "remote-user-42", "mail-user", actor="test"
        )
        cases = MailCaseStore(self.root)
        cases.upsert_federated_snapshot(
            "mail-user",
            "mail-peer",
            "remote-user-42",
            {
                "case_id": "remote-case-1",
                "title": "Remote Vorgang",
                "status": "offen",
                "permissions": ["read"],
                "messages": [],
                "comments": [],
                "drafts": [],
            },
        )
        self.assertEqual(1, len(cases.list_cases("mail-user")))

        response = self.client.post(
            "/admin/federation/mail-user-mappings/delete",
            data={"peer_id": "mail-peer", "remote_user_id": "remote-user-42"},
            follow_redirects=False,
        )
        self.assertEqual(302, response.status_code)
        self.assertIsNone(
            contract_store.mail_user_mapping("mail-peer", "remote-user-42")
        )
        self.assertEqual([], cases.list_cases("mail-user"))

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
