from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from app.fritzbox_contacts import (
    FritzBoxSecretBox,
    FritzBoxStore,
    _as_bool,
    _contact_numbers,
    _is_local_host,
    _safe_base_url,
    contact_entry_xml,
)


class FritzBoxContactTests(unittest.TestCase):
    def test_local_http_and_https_urls_are_supported(self):
        self.assertEqual(_safe_base_url("http://fritz.box:49000/"), "http://fritz.box:49000")
        self.assertEqual(_safe_base_url("https://fritz.box:49443/"), "https://fritz.box:49443")
        self.assertEqual(_safe_base_url("http://192.168.178.1:49000"), "http://192.168.178.1:49000")
        with self.assertRaises(ValueError):
            _safe_base_url("http://example.com:49000")
        with self.assertRaises(ValueError):
            _safe_base_url("https://user:secret@fritz.box:49443")
        with self.assertRaises(ValueError):
            _safe_base_url("https://fritz.box:49443/path")

    def test_local_host_detection(self):
        self.assertTrue(_is_local_host("fritz.box"))
        self.assertTrue(_is_local_host("192.168.178.1"))
        self.assertTrue(_is_local_host("fd00::1"))
        self.assertFalse(_is_local_host("example.com"))
        self.assertFalse(_is_local_host("8.8.8.8"))

    def test_boolean_parser(self):
        self.assertIs(_as_bool("1"), True)
        self.assertIs(_as_bool("false"), False)
        self.assertIsNone(_as_bool("unknown"))

    def test_secret_box_roundtrip_is_not_plaintext(self):
        box = FritzBoxSecretBox(b"installation-key-long-enough-for-tests")
        encrypted = box.encrypt("very-secret-password")
        self.assertTrue(encrypted.startswith("enc:v1:"))
        self.assertNotIn("very-secret-password", encrypted)
        self.assertEqual(box.decrypt(encrypted), "very-secret-password")

    def test_contact_xml_maps_name_three_unique_numbers_and_email(self):
        contact = {
            "contact_id": "c1",
            "fields": {
                "display_name": "Ada Lovelace",
                "mobile": "+49 170 123",
                "phone_private": "0203 1234",
                "phone_business": "0203 5678",
                "phone": "+49 170 123",
                "email": "ada@example.test",
            },
        }
        numbers = _contact_numbers(contact)
        self.assertEqual(len(numbers), 3)
        xml = contact_entry_xml(contact, 42)
        root = ET.fromstring(xml)
        self.assertEqual(root.findtext("uniqueid"), "42")
        self.assertEqual(root.findtext("person/realName"), "Ada Lovelace")
        nodes = root.findall("telephony/number")
        self.assertEqual([node.get("type") for node in nodes], ["mobile", "home", "work"])
        self.assertEqual(root.findtext("services/email"), "ada@example.test")

    def test_contact_without_phone_is_not_syncable(self):
        contact = {"fields": {"display_name": "No Phone", "email": "x@example.test"}}
        self.assertEqual(_contact_numbers(contact), [])
        with self.assertRaises(ValueError):
            contact_entry_xml(contact)

    def test_store_encrypts_saved_password_and_keeps_uid_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FritzBoxStore(Path(directory), b"installation-key-long-enough-for-tests")
            store.save_config(
                "alice",
                {"url": "http://fritz.box:49000", "username": "alice", "verify_tls": True, "phonebook_id": "0"},
                "secret-password",
                True,
            )
            raw = store.path.read_text(encoding="utf-8")
            self.assertNotIn("secret-password", raw)
            payload = json.loads(raw)
            self.assertTrue(payload["connections"][0]["password"].startswith("enc:v1:"))
            self.assertEqual(store.credentials("alice")["plain_password"], "secret-password")
            store.save_mapping("alice", 0, "contact-1", 17)
            self.assertEqual(store.mapping("alice", 0, "contact-1"), 17)
            self.assertIsNone(store.mapping("alice", 1, "contact-1"))

    def test_old_disabled_tls_local_config_migrates_to_local_tr064_http(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FritzBoxStore(Path(directory), b"installation-key-long-enough-for-tests")
            store.control.mkdir(parents=True, exist_ok=True)
            store.path.write_text(json.dumps({
                "version": 1,
                "connections": [{
                    "id": "old",
                    "owner": "alice",
                    "url": "https://fritz.box:49443",
                    "username": "alice",
                    "password": store.secrets.encrypt("secret-password"),
                    "verify_tls": False,
                    "phonebook_id": 0,
                    "mappings": {},
                }],
            }), encoding="utf-8")
            self.assertEqual(store.config("alice")["url"], "http://fritz.box:49000")
            self.assertEqual(store.credentials("alice")["url"], "http://fritz.box:49000")
            self.assertTrue(store.credentials("alice")["verify_tls"])

    def test_old_disabled_tls_private_ip_config_migrates_to_local_tr064_http(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FritzBoxStore(Path(directory), b"installation-key-long-enough-for-tests")
            store.control.mkdir(parents=True, exist_ok=True)
            store.path.write_text(json.dumps({
                "version": 1,
                "connections": [{
                    "id": "old-ip",
                    "owner": "alice",
                    "url": "https://192.168.178.1:49443",
                    "username": "alice",
                    "password": store.secrets.encrypt("secret-password"),
                    "verify_tls": False,
                    "phonebook_id": 0,
                    "mappings": {},
                }],
            }), encoding="utf-8")
            self.assertEqual(store.config("alice")["url"], "http://192.168.178.1:49000")
            self.assertEqual(store.credentials("alice")["url"], "http://192.168.178.1:49000")

    def test_safe_config_never_exposes_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FritzBoxStore(Path(directory), b"installation-key-long-enough-for-tests")
            config = store.save_config(
                "alice",
                {"url": "http://fritz.box:49000", "username": "alice", "verify_tls": False, "phonebook_id": "2"},
                "secret-password",
                True,
            )
            self.assertNotIn("password", config)
            self.assertTrue(config["password_saved"])
            self.assertTrue(config["verify_tls"])
            self.assertEqual(config["phonebook_id"], 2)


if __name__ == "__main__":
    unittest.main()
