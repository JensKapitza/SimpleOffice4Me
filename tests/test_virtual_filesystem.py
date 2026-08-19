import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore
from app.virtual_filesystem import VirtualFileSystem


class VirtualFileSystemTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "team" / "private").mkdir(parents=True)
        (self.root / "team" / "public.txt").write_bytes(b"public")
        (self.root / "team" / "private" / "secret.txt").write_bytes(b"secret")
        self.store = DocumentStore(self.root)
        self.store.scan()
        self.vfs = VirtualFileSystem(self.root, {"admin"})

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_tree_stays_writable_until_acl_is_explicitly_enabled(self):
        self.assertEqual("write", self.vfs.role("alice", "team/public.txt"))
        self.vfs.write_bytes("alice", "team/public.txt", b"changed")
        self.assertEqual(b"changed", (self.root / "team" / "public.txt").read_bytes())

    def test_inherited_roles_hide_and_protect_resources(self):
        self.vfs.set_grants(".", {"alice": "manage", "bob": "read"}, "admin")

        self.assertEqual(["team"], [item.name for item in self.vfs.entries("bob")])
        self.assertEqual(b"public", self.vfs.read_bytes("bob", "team/public.txt"))
        with self.assertRaises(PermissionError):
            self.vfs.write_bytes("bob", "team/public.txt", b"denied")
        with self.assertRaises(PermissionError):
            self.vfs.set_grants("team", {"bob": "manage"}, "bob")

    def test_non_inheriting_child_can_revoke_parent_and_grant_another_user(self):
        self.vfs.set_grants(".", {"alice": "manage", "bob": "read"}, "admin")
        self.vfs.set_grants("team/private", {"carol": "write", "alice": "manage"}, "admin", inherit=False)

        self.assertFalse(self.vfs.allows("bob", "team/private/secret.txt", "read"))
        self.assertNotIn("private", [item.name for item in self.vfs.entries("bob", "team")])
        self.assertTrue(self.vfs.allows("carol", "team/private/secret.txt", "write"))
        self.vfs.write_bytes("carol", "team/private/secret.txt", b"updated")
        self.assertEqual(b"updated", (self.root / "team" / "private" / "secret.txt").read_bytes())

    def test_path_escape_symlink_and_reserved_metadata_are_rejected(self):
        with self.assertRaises(ValueError):
            self.vfs.resolve("../outside")
        with self.assertRaises(ValueError):
            self.vfs.resolve(".simpleoffice-meta/credentials.json")
        link = self.root / "escape"
        try:
            link.symlink_to(Path(self.temp.name).parent, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        with self.assertRaises(ValueError):
            self.vfs.resolve("escape/file")

    def test_policy_update_is_audited_without_file_content(self):
        self.vfs.set_grants("team", {"alice": "manage", "bob": "read"}, "admin")
        events = [event for event in self.store.logbook() if event.get("type") == "folder_access_updated"]
        self.assertTrue(events)
        self.assertEqual("team", events[-1]["folder"])
        self.assertNotIn("secret", str(events[-1]))


if __name__ == "__main__":
    unittest.main()
