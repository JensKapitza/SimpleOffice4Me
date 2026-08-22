import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app.db import ensure_auth_database
from app.mail_client import ImapArchive, MailStore, ManageSieveClient, SecretBox
from app.virtual_filesystem import VirtualFileSystem


MAIL = b"From: sender@example.test\r\nTo: user@example.test\r\nSubject: Archive\r\nMessage-ID: <one@example.test>\r\n\r\nBody\r\n"


class FakeImap:
    capabilities = (b"IMAP4rev1", b"UIDPLUS")

    def __init__(self):
        self.untagged_responses = {"UIDVALIDITY": [b"42"]}
        self.calls = []

    def select(self, folder, readonly=False):
        self.calls.append(("select", folder, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, args))
        if command == "search":
            return "OK", [b"7"]
        if command == "fetch":
            return "OK", [(b"7 (UID 7 RFC822.SIZE 110 BODY[] {110}", MAIL), b")"]
        raise AssertionError(command)

    def list(self): return "OK", [b'(\\HasNoChildren) "/" "INBOX"']
    def logout(self): self.calls.append(("logout",))


class DuplexBuffer:
    def __init__(self, replies: bytes):
        self.replies = io.BytesIO(replies)
        self.written = bytearray()
    def readline(self): return self.replies.readline()
    def write(self, value): self.written.extend(value); return len(value)
    def flush(self): pass


class MailClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"test-master-key-long-enough")
        self.account = self.store.save_account("alice", {"host": "imap.example.test", "port": 993, "security": "tls", "username": "alice@example.test", "folder": "INBOX", "sieve_port": 4190}, "secret-password", True)

    def tearDown(self): self.temp.cleanup()

    def test_password_is_encrypted_and_isolated_by_user(self):
        stored = self.store.accounts_path.read_text(encoding="utf-8")
        self.assertNotIn("secret-password", stored)
        self.assertEqual("secret-password", self.store.account("alice", self.account["id"])["plain_password"])
        with self.assertRaises(KeyError): self.store.account("bob", self.account["id"], "x")
        box = SecretBox(b"test-master-key-long-enough")
        self.assertEqual("value", box.decrypt(box.encrypt("value")))
        token = box.encrypt("value")
        damaged = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
        with self.assertRaises(Exception): box.decrypt(damaged)

    def test_managesieve_uses_synchronizing_literal_and_explicit_activation(self):
        client = ManageSieveClient("sieve.example.test")
        stream = DuplexBuffer(b"+ send literal\r\nOK stored\r\nOK active\r\n")
        client.file = stream
        client.put_script("main", "keep;\n", activate=True)
        self.assertEqual(b'PUTSCRIPT "main" {6}\r\nkeep;\n\r\nSETACTIVE "main"\r\n', bytes(stream.written))

    def test_sieve_script_is_owner_scoped_and_versioned(self):
        result = self.store.save_script("alice", self.account["id"], "main", 'require ["fileinto"];\nkeep;\n')
        self.assertEqual('require ["fileinto"];\nkeep;\n', self.store.script("alice", self.account["id"], "main"))
        self.assertTrue(result["revision"])
        self.assertEqual(1, len(self.store.scripts_for("alice", self.account["id"])))
        with self.assertRaises(KeyError): self.store.scripts_for("bob", self.account["id"])

    def test_archive_uses_examine_and_never_mutating_imap_commands(self):
        fake = FakeImap()
        account = self.store.account("alice", self.account["id"])
        with patch.object(ImapArchive, "_connect", return_value=fake):
            first = ImapArchive(self.store).archive("alice", account)
        self.assertEqual(1, first["archived"])
        self.assertTrue(any(call == ("select", "INBOX", True) for call in fake.calls))
        commands = [call[1].casefold() for call in fake.calls if call[0] == "uid"]
        self.assertEqual(["search", "fetch"], commands)
        self.assertFalse({"store", "copy", "move", "expunge"} & set(commands))
        owner_key = __import__("hashlib").sha256(b"alice").hexdigest()[:32]
        files = list((self.root / "email" / owner_key / self.account["id"]).rglob("*.eml"))
        self.assertEqual([MAIL], [path.read_bytes() for path in files])
        origin = next(iter(json.loads(path.read_text(encoding="utf-8")) for path in (self.root / ".simpleoffice-meta" / "documents").glob("*.json") if json.loads(path.read_text(encoding="utf-8")).get("last_path", "").endswith(".eml")))
        self.assertEqual("42", origin["attributes"]["email_origin"]["uidvalidity"])
        self.assertEqual("7", origin["attributes"]["email_origin"]["uid"])
        vfs = VirtualFileSystem(self.root)
        self.assertTrue(vfs.allows("alice", f"email/{owner_key}/{self.account['id']}", "write"))
        self.assertFalse(vfs.allows("bob", f"email/{owner_key}/{self.account['id']}", "read"))

    def test_web_tab_requires_login_and_does_not_render_saved_password(self):
        previous = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        try:
            app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "users.sqlite"), DOCUMENT_ROOT=str(self.root))
            with app.app_context(): ensure_auth_database()
            client = app.test_client()
            self.assertEqual(302, client.get("/documents/mail").status_code)
            client.post("/auth/register", data={"username": "alice", "password": "password-123"})
            client.post("/auth/login", data={"username": "alice", "password": "password-123"})
            body = client.get("/documents/mail").get_data(as_text=True)
            self.assertIn("IMAP-Client", body)
            self.assertNotIn("secret-password", body)
        finally:
            app.config.update(previous)


if __name__ == "__main__": unittest.main()
