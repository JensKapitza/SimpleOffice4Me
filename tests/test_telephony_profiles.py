from __future__ import annotations

import sqlite3
import tempfile
import unittest

from app.telephony_profiles import TelephonyProfileStore
from app.telephony_profiles import sip_digest_ha1, sip_digest_verifiers
from simpleoffice_sip_crypto import decrypt_sip_digest_verifiers, encrypt_sip_profile_secret

TEST_KEY = "test-installation-key-that-is-long-enough"


class TelephonyProfileStoreTests(unittest.TestCase):
    def test_settings_and_profile_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TelephonyProfileStore(directory, TEST_KEY)
            settings = store.save_settings(
                registrar_host="pbx.home.arpa",
                registrar_port=5060,
                transport="udp",
                realm="simpleoffice.local",
                stun_server="",
            )
            self.assertEqual(settings["registrar_host"], "pbx.home.arpa")
            self.assertEqual(settings["registrar_port"], "5060")

            profile = store.create_profile("101", "Jens PC", "enc:v1:dummy", device_kind="softphone")
            self.assertEqual(profile["extension"], "101")
            self.assertNotIn("secret_enc", profile)

            setup = store.setup_values("101")
            self.assertEqual(setup["sip_uri"], "sip:101@pbx.home.arpa")
            self.assertEqual(setup["transport"], "udp")
            self.assertFalse(setup["runtime_ready"])

    def test_secret_is_hidden_and_can_be_rotated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TelephonyProfileStore(directory, TEST_KEY)
            store.create_profile("102", "Handy", "enc:v1:first")
            self.assertEqual(store.encrypted_secret("102"), "enc:v1:first")
            self.assertNotIn("secret_enc", store.profile("102"))
            store.rotate_secret("102", "enc:v1:second")
            self.assertEqual(store.encrypted_secret("102"), "enc:v1:second")

    def test_duplicate_and_invalid_extensions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TelephonyProfileStore(directory, TEST_KEY)
            store.create_profile("110", "Desk", "enc:v1:one", device_kind="deskphone")
            with self.assertRaises(sqlite3.IntegrityError):
                store.create_profile("110", "Duplicate", "enc:v1:two")
            with self.assertRaises(ValueError):
                store.create_profile("abc", "Bad", "enc:v1:three")
            with self.assertRaises(ValueError):
                store.create_profile("111", "Bad secret", "plaintext")

    def test_sip_hosts_reject_urls_credentials_paths_and_embedded_ports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TelephonyProfileStore(directory, TEST_KEY)
            for host in (
                "https://pbx.example.test",
                "user@pbx.example.test",
                "pbx.example.test/path",
                "pbx.example.test:5060",
            ):
                with self.subTest(host=host), self.assertRaises(ValueError):
                    store.save_settings(
                        registrar_host=host,
                        registrar_port=5060,
                        transport="udp",
                        realm="simpleoffice.local",
                    )

    def test_ipv6_host_is_formatted_safely_in_sip_uri(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TelephonyProfileStore(directory, TEST_KEY)
            store.save_settings(
                registrar_host="2001:db8::10",
                registrar_port=5061,
                transport="tls",
                realm="simpleoffice.local",
            )
            store.create_profile("120", "IPv6", "enc:v1:ipv6")
            self.assertEqual(store.setup_values("120")["sip_uri"], "sip:120@[2001:db8::10]")

    def test_delete_missing_profile_fails_instead_of_reporting_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TelephonyProfileStore(directory, TEST_KEY)
            with self.assertRaises(KeyError):
                store.delete_profile("999")

    def test_legacy_md5_verifier_is_verified_and_migrated_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            password = "legacy-profile-password"
            store = TelephonyProfileStore(directory, TEST_KEY)
            store.save_settings(
                registrar_host="", registrar_port=5060, transport="udp",
                realm="simpleoffice.local",
            )
            store.create_profile(
                "121", "Legacy", encrypt_sip_profile_secret(password, "121", TEST_KEY),
                digest_ha1=sip_digest_ha1("121", "simpleoffice.local", password),
            )
            legacy_md5 = sip_digest_ha1("121", "simpleoffice.local", password)
            with sqlite3.connect(store.path) as db:
                db.execute("UPDATE telephony_profile SET digest_ha1=? WHERE extension='121'", (legacy_md5,))

            migrated = TelephonyProfileStore(directory, TEST_KEY)
            with sqlite3.connect(migrated.path) as db:
                stored = db.execute("SELECT digest_ha1 FROM telephony_profile WHERE extension='121'").fetchone()[0]
            self.assertTrue(stored.startswith("enc:sip-ha1:v1:"))
            self.assertEqual(
                sip_digest_verifiers("121", "simpleoffice.local", password),
                decrypt_sip_digest_verifiers("121", stored, TEST_KEY),
            )

    def test_legacy_migration_with_wrong_key_preserves_database_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            password = "legacy-profile-password"
            store = TelephonyProfileStore(directory, TEST_KEY)
            store.save_settings(
                registrar_host="", registrar_port=5060, transport="udp",
                realm="simpleoffice.local",
            )
            store.create_profile(
                "122", "Legacy", encrypt_sip_profile_secret(password, "122", TEST_KEY),
                digest_ha1=sip_digest_ha1("122", "simpleoffice.local", password),
            )
            legacy_md5 = sip_digest_ha1("122", "simpleoffice.local", password)
            with sqlite3.connect(store.path) as db:
                db.execute("UPDATE telephony_profile SET digest_ha1=? WHERE extension='122'", (legacy_md5,))

            with self.assertRaises(ValueError):
                TelephonyProfileStore(directory, "different-installation-key-long-enough")
            with sqlite3.connect(store.path) as db:
                stored = db.execute("SELECT digest_ha1 FROM telephony_profile WHERE extension='122'").fetchone()[0]
            self.assertEqual(legacy_md5, stored)


if __name__ == "__main__":
    unittest.main()
