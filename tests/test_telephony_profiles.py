from __future__ import annotations

import sqlite3
import tempfile
import unittest

from app.telephony_profiles import TelephonyProfileStore


class TelephonyProfileStoreTests(unittest.TestCase):
    def test_settings_and_profile_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TelephonyProfileStore(directory)
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
            store = TelephonyProfileStore(directory)
            store.create_profile("102", "Handy", "enc:v1:first")
            self.assertEqual(store.encrypted_secret("102"), "enc:v1:first")
            self.assertNotIn("secret_enc", store.profile("102"))
            store.rotate_secret("102", "enc:v1:second")
            self.assertEqual(store.encrypted_secret("102"), "enc:v1:second")

    def test_duplicate_and_invalid_extensions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TelephonyProfileStore(directory)
            store.create_profile("110", "Desk", "enc:v1:one", device_kind="deskphone")
            with self.assertRaises(sqlite3.IntegrityError):
                store.create_profile("110", "Duplicate", "enc:v1:two")
            with self.assertRaises(ValueError):
                store.create_profile("abc", "Bad", "enc:v1:three")
            with self.assertRaises(ValueError):
                store.create_profile("111", "Bad secret", "plaintext")

    def test_sip_hosts_reject_urls_credentials_paths_and_embedded_ports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TelephonyProfileStore(directory)
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
            store = TelephonyProfileStore(directory)
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
            store = TelephonyProfileStore(directory)
            with self.assertRaises(KeyError):
                store.delete_profile("999")


if __name__ == "__main__":
    unittest.main()
