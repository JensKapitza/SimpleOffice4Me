import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.mail_client import MailStore
from app.sieve_sync import (
    ManageSieveSyncClient,
    activate_server_script,
    sanitize_local_name,
    server_state,
    sync_from_server,
)


class ReadWriteBuffer:
    def __init__(self, replies: bytes):
        self.replies = io.BytesIO(replies)
        self.written = bytearray()

    def readline(self):
        return self.replies.readline()

    def read(self, size=-1):
        return self.replies.read(size)

    def write(self, value):
        self.written.extend(value)
        return len(value)

    def flush(self):
        pass


class SieveSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "documents"
        self.store = MailStore(self.root, b"test-master-key-long-enough")
        self.account = self.store.save_account(
            "alice",
            {
                "host": "imap.example.test",
                "port": 993,
                "security": "tls",
                "username": "alice@example.test",
                "folder": "INBOX",
                "sieve_host": "sieve.example.test",
                "sieve_port": 4190,
            },
            "secret-password",
            True,
        )
        self.resolved = self.store.account("alice", self.account["id"])

    def tearDown(self):
        self.temp.cleanup()

    def test_listscripts_marks_active_script(self):
        client = ManageSieveSyncClient("sieve.example.test")
        client.file = ReadWriteBuffer(b'"main" ACTIVE\r\n"vacation"\r\nOK done\r\n')
        rows = client.list_scripts()
        self.assertEqual(
            [{"name": "main", "active": True}, {"name": "vacation", "active": False}],
            rows,
        )
        self.assertEqual(b"LISTSCRIPTS\r\n", bytes(client.file.written))

    def test_getscript_reads_exact_literal(self):
        client = ManageSieveSyncClient("sieve.example.test")
        client.file = ReadWriteBuffer(b"{6}\r\nkeep;\n\r\nOK done\r\n")
        self.assertEqual("keep;\n", client.get_script("main"))
        self.assertEqual(b'GETSCRIPT "main"\r\n', bytes(client.file.written))

    def test_sync_downloads_all_server_scripts_and_persists_active_state(self):
        contents = {"main": "keep;\n", "vacation": 'vacation "weg";\n'}
        with patch.object(ManageSieveSyncClient, "connect"), \
             patch.object(ManageSieveSyncClient, "close"), \
             patch.object(ManageSieveSyncClient, "list_scripts_detailed", return_value={
                 "scripts": [
                     {"name": "main", "active": True},
                     {"name": "vacation", "active": False},
                 ],
                 "skipped": [],
             }), \
             patch.object(ManageSieveSyncClient, "get_script", side_effect=lambda name: contents[name]):
            result = sync_from_server(self.store, "alice", self.resolved)

        self.assertEqual("main", result["active"])
        self.assertEqual("keep;\n", self.store.script("alice", self.account["id"], "main"))
        self.assertEqual('vacation "weg";\n', self.store.script("alice", self.account["id"], "vacation"))
        persisted = server_state(self.store, "alice", self.account["id"])
        self.assertEqual("main", persisted["active"])
        self.assertEqual({"main", "vacation"}, {row["name"] for row in persisted["scripts"]})

    def test_sync_preserves_changed_local_body_before_server_replacement(self):
        local_content = 'if header :contains "subject" "local" { keep; }\n'
        remote_content = 'if header :contains "subject" "server" { discard; }\n'
        self.store.save_script("alice", self.account["id"], "main", local_content)

        with patch.object(ManageSieveSyncClient, "connect"), \
             patch.object(ManageSieveSyncClient, "close"), \
             patch.object(ManageSieveSyncClient, "list_scripts_detailed", return_value={
                 "scripts": [{"name": "main", "active": True}], "skipped": []}), \
             patch.object(ManageSieveSyncClient, "get_script", return_value=remote_content):
            sync_from_server(self.store, "alice", self.resolved)

        self.assertEqual(remote_content, self.store.script("alice", self.account["id"], "main"))
        snapshots = self.root / ".simpleoffice-history" / "snapshots" / "sieve-revisions"
        revisions = [json.loads(path.read_text(encoding="utf-8")) for path in snapshots.glob("*.json")]
        self.assertEqual(1, len(revisions))
        self.assertEqual(local_content, revisions[0]["content"])
        self.assertEqual("local", revisions[0]["source"])

    def test_activation_backs_up_server_before_setactive(self):
        with patch("app.sieve_sync.sync_from_server", return_value={
                 "scripts": [{"name": "main", "active": True}, {"name": "vacation", "active": False}],
                 "active": "main", "updated_at": "now",
             }) as sync, \
             patch.object(ManageSieveSyncClient, "connect"), \
             patch.object(ManageSieveSyncClient, "close"), \
             patch.object(ManageSieveSyncClient, "set_active") as set_active:
            state = activate_server_script(self.store, "alice", self.resolved, "vacation")

        sync.assert_called_once()
        set_active.assert_called_once_with("vacation")
        self.assertEqual("vacation", state["active"])
        self.assertTrue(next(row for row in state["scripts"] if row["name"] == "vacation")["active"])

    def test_listscripts_keeps_names_with_spaces_and_reports_garbage(self):
        client = ManageSieveSyncClient("sieve.example.test")
        client.file = ReadWriteBuffer(
            '"Weiterleitung Arbeit" ACTIVE\r\n"Umlaute äöü"\r\n"unclosed-quote\r\nOK done\r\n'.encode("utf-8"))
        inventory = client.list_scripts_detailed()
        self.assertEqual(
            [{"name": "Weiterleitung Arbeit", "active": True}, {"name": "Umlaute äöü", "active": False}],
            inventory["scripts"],
        )
        self.assertEqual(['"unclosed-quote'], inventory["skipped"])
        # Compatibility wrapper keeps working.
        client.file = ReadWriteBuffer(b'"main" ACTIVE\r\nOK done\r\n')
        self.assertEqual([{"name": "main", "active": True}], client.list_scripts())

    def test_sanitize_local_name_is_stable_and_collision_free(self):
        self.assertEqual("main", sanitize_local_name("main", set()))
        first = sanitize_local_name("Weiterleitung Arbeit", set())
        self.assertRegex(first, r"^[A-Za-z0-9_.-]{1,128}$")
        self.assertEqual(first, sanitize_local_name("Weiterleitung Arbeit", set()))
        other = sanitize_local_name("Weiterleitung Arbeit", {first})
        self.assertNotEqual(first, other)
        self.assertRegex(other, r"^[A-Za-z0-9_.-]{1,128}$")

    def test_sync_downloads_spaced_names_and_reports_mapping(self):
        contents = {"Weiterleitung Arbeit": "keep;\n"}
        with patch.object(ManageSieveSyncClient, "connect"), \
             patch.object(ManageSieveSyncClient, "close"), \
             patch.object(ManageSieveSyncClient, "list_scripts_detailed", return_value={
                 "scripts": [{"name": "Weiterleitung Arbeit", "active": True}],
                 "skipped": [],
             }), \
             patch.object(ManageSieveSyncClient, "get_script", side_effect=lambda name: contents[name]):
            result = sync_from_server(self.store, "alice", self.resolved)

        self.assertEqual(1, len(result["scripts"]))
        row = result["scripts"][0]
        self.assertEqual("Weiterleitung Arbeit", row["server_name"])
        self.assertRegex(row["name"], r"^[A-Za-z0-9_.-]{1,128}$")
        self.assertEqual("keep;\n", self.store.script("alice", self.account["id"], row["name"]))
        self.assertEqual([{"server": "Weiterleitung Arbeit", "local": row["name"]}], result["renamed"])
        self.assertEqual([], result["skipped"])
        self.assertEqual(row["name"], result["active"])

    def test_activation_uses_server_name_for_spaced_scripts(self):
        with patch("app.sieve_sync.sync_from_server", return_value={
                 "scripts": [{"name": "Weiterleitung_Arbeit-a1b2c3d4", "server_name": "Weiterleitung Arbeit",
                              "active": False}],
                 "active": "", "updated_at": "now",
             }) as sync, \
             patch.object(ManageSieveSyncClient, "connect"), \
             patch.object(ManageSieveSyncClient, "close"), \
             patch.object(ManageSieveSyncClient, "set_active") as set_active:
            state = activate_server_script(self.store, "alice", self.resolved, "Weiterleitung Arbeit")

        sync.assert_called_once()
        set_active.assert_called_once_with("Weiterleitung Arbeit")
        self.assertEqual("Weiterleitung_Arbeit-a1b2c3d4", state["active"])


if __name__ == "__main__":
    unittest.main()
