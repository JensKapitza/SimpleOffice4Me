from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.federation_credentials import make_password_change_envelope, open_password_change_envelope
from app.federation_store import FederationStore
from app.linux_users import LinuxUserLinkStore, create_linux_user, set_linux_password, validate_username
from app.script_library import ScriptLibrary


class ScriptLibraryTests(unittest.TestCase):
    def test_versioned_export_import_roundtrip(self):
        with tempfile.TemporaryDirectory() as source_temp, tempfile.TemporaryDirectory() as target_temp:
            source = ScriptLibrary(source_temp)
            source.save(script_id="a" * 16, name="Backup", language="sh", content="echo one\n", actor="admin")
            source.save(script_id="a" * 16, name="Backup", language="sh", content="echo two\n", actor="admin")
            bundle = source.export_bundle()
            target = ScriptLibrary(target_temp)
            result = target.import_bundle(bundle, actor="peer")
            self.assertEqual(result["imported"], 1)
            self.assertEqual(target.get("a" * 16)["revision"], 2)
            self.assertEqual(target.read("a" * 16), "echo two\n")
            self.assertEqual(len(target.history("a" * 16)), 2)

    def test_import_does_not_execute_script(self):
        with tempfile.TemporaryDirectory() as source_temp, tempfile.TemporaryDirectory() as target_temp:
            source = ScriptLibrary(source_temp)
            source.save(script_id="b" * 16, name="Danger", language="sh", content="touch /tmp/should-not-run\n")
            bundle = source.export_bundle()
            with patch("subprocess.run") as run:
                ScriptLibrary(target_temp).import_bundle(bundle)
                run.assert_not_called()


class LinuxUserTests(unittest.TestCase):
    def test_contact_link_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LinuxUserLinkStore(temp)
            self.assertEqual(store.set_contact("jens", "contact-42"), {"contact_id": "contact-42"})
            self.assertEqual(store.all()["jens"]["contact_id"], "contact-42")

    def test_username_rejects_shell_injection(self):
        with self.assertRaises(ValueError):
            validate_username("jens;id")

    @patch("app.linux_users._run")
    def test_new_user_is_locked(self, run):
        run.return_value = {"ok": True, "missing": False, "returncode": 0, "stdout": "", "stderr": ""}
        create_linux_user("testuser")
        self.assertEqual(run.call_args_list[0].args[0][0], "useradd")
        self.assertEqual(run.call_args_list[1].args[0], ["usermod", "--lock", "testuser"])

    @patch("app.linux_users._run")
    def test_password_is_sent_via_stdin_not_argv(self, run):
        run.return_value = {"ok": True, "missing": False, "returncode": 0, "stdout": "", "stderr": ""}
        set_linux_password("testuser", "VeryLongPassword!123")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["chpasswd"])
        self.assertNotIn("VeryLongPassword!123", repr(args[0]))
        self.assertIn(b"VeryLongPassword!123", kwargs["stdin"])


class CredentialFederationTests(unittest.TestCase):
    def test_password_change_is_encrypted_and_replay_protected(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FederationStore(temp)
            envelope = make_password_change_envelope(
                shared_secret="x" * 48,
                sender_peer="office-a",
                receiver_peer="office-b",
                username="testuser",
                password="VeryLongPassword!123",
                contact_id="contact-42",
            )
            self.assertNotIn("VeryLongPassword!123", str(envelope))
            opened = open_password_change_envelope(
                envelope,
                shared_secret="x" * 48,
                local_peer="office-b",
                store=store,
            )
            self.assertEqual(opened["password"], "VeryLongPassword!123")
            self.assertEqual(opened["contact_id"], "contact-42")
            with self.assertRaisesRegex(ValueError, "replay"):
                open_password_change_envelope(
                    envelope,
                    shared_secret="x" * 48,
                    local_peer="office-b",
                    store=store,
                )

    def test_wrong_peer_secret_cannot_decrypt(self):
        with tempfile.TemporaryDirectory() as temp:
            envelope = make_password_change_envelope(
                shared_secret="x" * 48,
                sender_peer="office-a",
                receiver_peer="office-b",
                username="testuser",
                password="VeryLongPassword!123",
            )
            with self.assertRaises(ValueError):
                open_password_change_envelope(
                    envelope,
                    shared_secret="y" * 48,
                    local_peer="office-b",
                    store=FederationStore(temp),
                )


if __name__ == "__main__":
    unittest.main()
