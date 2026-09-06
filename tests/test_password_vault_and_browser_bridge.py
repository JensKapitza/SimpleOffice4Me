import json
import tempfile
import unittest
from pathlib import Path

from app.password_browser_bridge import (
    decode_native_message,
    encode_native_message,
    login_matches_url,
    matching_logins,
    native_host_manifest,
)
from app.password_vault import PasswordVault, generate_password


class PasswordVaultTests(unittest.TestCase):
    def test_vault_roundtrip_wrong_password_and_master_change(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = PasswordVault(temp)
            key = vault.create("7", "Correct Horse Battery Staple! 2026")
            saved = vault.put("7", key, {
                "type": "login",
                "name": "Example",
                "url": "https://example.test/login",
                "username": "jens@example.test",
                "password": "not-stored-in-plaintext-4711",
                "notes": "private note",
                "totp": "otpauth://totp/Example?secret=ABCDEF123456",
            })
            with self.assertRaises(ValueError):
                vault.unlock("7", "Definitely the wrong password")
            entries = vault.entries("7", vault.unlock("7", "Correct Horse Battery Staple! 2026"))
            self.assertEqual(saved["entry_id"], entries[0]["entry_id"])
            self.assertEqual("not-stored-in-plaintext-4711", entries[0]["data"]["password"])
            vault.change_master_password(
                "7", "Correct Horse Battery Staple! 2026", "New Master Password 2026 -- long enough!"
            )
            with self.assertRaises(ValueError):
                vault.unlock("7", "Correct Horse Battery Staple! 2026")
            changed = vault.entries("7", vault.unlock("7", "New Master Password 2026 -- long enough!"))
            self.assertEqual("jens@example.test", changed[0]["data"]["username"])

    def test_encrypted_backup_contains_no_secret_and_restores(self):
        with tempfile.TemporaryDirectory() as source_temp, tempfile.TemporaryDirectory() as target_temp:
            source = PasswordVault(source_temp)
            key = source.create("42", "Portable Vault Master Password 2026!")
            source.put("42", key, {
                "type": "login", "name": "Secret Service", "url": "https://secret.example",
                "username": "private-user", "password": "UltraSecret-Do-Not-Leak-123!",
            })
            backup = source.export_backup("42")
            self.assertNotIn(b"UltraSecret-Do-Not-Leak-123!", backup)
            self.assertNotIn(b"private-user", backup)
            payload = json.loads(backup)
            self.assertEqual("simpleoffice-password-vault", payload["payload"]["format"])

            restored = PasswordVault(target_temp)
            result = restored.import_backup(backup)
            self.assertEqual(1, result["entries"])
            restored_key = restored.unlock("42", "Portable Vault Master Password 2026!")
            self.assertEqual("UltraSecret-Do-Not-Leak-123!", restored.entries("42", restored_key)[0]["data"]["password"])

    def test_backup_checksum_rejects_modification(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as target:
            vault = PasswordVault(temp)
            vault.create("5", "Another Master Password 2026!")
            envelope = json.loads(vault.export_backup("5"))
            envelope["payload"]["exported_at"] += 1
            tampered = json.dumps(envelope).encode("utf-8")
            with self.assertRaises(ValueError):
                PasswordVault(target).import_backup(tampered)

    def test_generated_password_has_multiple_character_classes(self):
        value = generate_password(40)
        self.assertEqual(40, len(value))
        self.assertTrue(any(c.islower() for c in value))
        self.assertTrue(any(c.isupper() for c in value))
        self.assertTrue(any(c.isdigit() for c in value))
        self.assertTrue(any(not c.isalnum() for c in value))


class PasswordBrowserBridgeTests(unittest.TestCase):
    def test_native_message_frame_roundtrip(self):
        payload = {"action": "find", "url": "https://example.test/login", "request_id": "abc"}
        self.assertEqual(payload, decode_native_message(encode_native_message(payload)))

    def test_chrome_and_firefox_manifests_use_their_distinct_allowlists(self):
        executable = Path("/opt/simpleoffice4me/bin/password-native-host")
        chrome = native_host_manifest("chrome", "abcdefghijklmnopabcdefghijklmnop", executable)
        firefox = native_host_manifest("firefox", "simpleoffice-passwords@example.org", executable)
        self.assertEqual(["chrome-extension://abcdefghijklmnopabcdefghijklmnop/"], chrome["allowed_origins"])
        self.assertEqual(["simpleoffice-passwords@example.org"], firefox["allowed_extensions"])
        self.assertNotIn("allowed_extensions", chrome)
        self.assertNotIn("allowed_origins", firefox)

    def test_https_login_is_never_offered_to_http(self):
        login = {"type": "login", "url": "https://example.test/login"}
        self.assertTrue(login_matches_url(login, "https://example.test/other"))
        self.assertFalse(login_matches_url(login, "http://example.test/other"))
        self.assertFalse(login_matches_url(login, "https://sub.example.test/"))

    def test_matching_logins_returns_only_minimal_fields(self):
        rows = [{
            "entry_id": "one",
            "data": {
                "type": "login", "name": "Example", "url": "https://example.test/",
                "username": "alice", "password": "secret", "notes": "must not be returned",
            },
        }]
        matches = matching_logins(rows, "https://example.test/login")
        self.assertEqual(1, len(matches))
        self.assertEqual("secret", matches[0]["password"])
        self.assertNotIn("notes", matches[0])


if __name__ == "__main__":
    unittest.main()
