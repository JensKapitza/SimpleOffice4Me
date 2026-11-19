import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app.chat_store import ChatStore
from app import chat_routes
from app.document_store import DocumentStore, atomic_json_write
from app.v2.storage_runtime import create_document


class ChatDocumentTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "documents"
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "alice", "password": "secure-password-123"})
        self.client.post("/auth/login", data={"username": "alice", "password": "secure-password-123"})
        with app.app_context():
            db = database.get_db()
            db.execute(
                """INSERT INTO user(username,password,display_name,is_admin,is_disabled,auth_version,created_at,updated_at)
                   VALUES(?,?,?,0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                ("bob", generate_password_hash("bob-password-123"), "Bob"),
            )
            db.commit()
        self.room = ChatStore(self.root).create_room("Alice and Bob", "alice", ["bob"])
        (self.root / "projects").mkdir(parents=True, exist_ok=True)
        self.document = create_document(self.root, "alice", "projects/brief.txt", b"brief", max_bytes=1024)

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def test_path_mode_sends_canonical_logical_path_as_text_only(self):
        response = self.client.post(
            f"/chat/rooms/{self.room['room_id']}/share",
            data={"kind": "document", "mode": "path", "document_id": self.document["document_id"]},
        )
        self.assertEqual(302, response.status_code)
        message = ChatStore(self.root).messages(self.room["room_id"])[0]
        self.assertEqual("projects/brief.txt", message["body"])
        self.assertEqual([], message["attachments"])

    def test_document_transfer_page_offers_only_actor_writable_rooms(self):
        response = self.client.get(f"/documents/{self.document['document_id']}/transfer")
        self.assertEqual(200, response.status_code)
        page = response.get_data(as_text=True)
        self.assertIn("/chat/rooms/" + self.room["room_id"] + "/share", page)
        self.assertIn('name="mode" value="path"', page)
        self.assertIn('name="mode" value="file"', page)
        self.assertIn("projects/brief.txt", page)

    def test_path_mode_rejects_unregistered_paths(self):
        response = self.client.post(
            f"/chat/rooms/{self.room['room_id']}/share",
            data={"kind": "document", "mode": "path", "path": "../../etc/passwd"},
            follow_redirects=True,
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual([], ChatStore(self.root).messages(self.room["room_id"]))

    def test_file_mode_creates_downloadable_chat_attachment(self):
        response = self.client.post(
            f"/chat/rooms/{self.room['room_id']}/share",
            data={"kind": "document", "mode": "file", "document_id": self.document["document_id"]},
        )
        self.assertEqual(302, response.status_code)
        message = ChatStore(self.root).messages(self.room["room_id"])[0]
        self.assertEqual(1, len(message["attachments"]))
        attachment = message["attachments"][0]
        self.assertEqual(self.document["document_id"], attachment["document_id"])
        download = self.client.get(f"/chat/attachments/{attachment['attachment_id']}")
        self.assertEqual(200, download.status_code)
        self.assertEqual(b"brief", download.data)
        download.close()

    def test_shared_attachment_checks_read_access_again_at_download(self):
        self.client.post(
            f"/chat/rooms/{self.room['room_id']}/share",
            data={"kind": "document", "mode": "file", "document_id": self.document["document_id"]},
        )
        attachment = ChatStore(self.root).messages(self.room["room_id"])[0]["attachments"][0]
        store = DocumentStore(self.root)
        folder = store.ensure_folder_policy(self.root / "projects")
        policy = store._read_json(folder, {})
        policy.update(access_enabled=True, inherit=False, grants=[])
        atomic_json_write(folder, policy)
        self.assertEqual(404, self.client.get(f"/chat/attachments/{attachment['attachment_id']}").status_code)

    def test_path_and_file_modes_reject_room_members_without_document_read_access(self):
        store = DocumentStore(self.root)
        folder_policy = store.ensure_folder_policy(self.root / "projects")
        policy = store._read_json(folder_policy, {})
        policy.update({"access_enabled": True, "inherit": False, "grants": [{"principal": "alice", "role": "read"}]})
        atomic_json_write(folder_policy, policy)
        for mode in ("path", "file"):
            with self.subTest(mode=mode):
                response = self.client.post(
                    f"/chat/rooms/{self.room['room_id']}/share",
                    data={"kind": "document", "mode": mode, "document_id": self.document["document_id"]},
                    follow_redirects=True,
                )
                self.assertEqual(200, response.status_code)
                self.assertEqual([], ChatStore(self.root).messages(self.room["room_id"]))

    def test_path_mode_attempts_delivery_to_remote_room(self):
        room = ChatStore(self.root).create_room(
            "Federated", "alice", [], remote_peer_id="peer-1", remote_users=["remote-alice"],
        )
        with patch.object(chat_routes, "send_message") as deliver:
            response = self.client.post(
                f"/chat/rooms/{room['room_id']}/share",
                data={"kind": "document", "mode": "path", "document_id": self.document["document_id"]},
            )
        self.assertEqual(302, response.status_code)
        self.assertEqual(1, deliver.call_count)


if __name__ == "__main__":
    unittest.main()
