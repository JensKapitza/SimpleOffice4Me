"""Blackbox: object accessory and set relations (Issue #1111, P2)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import app
from app import db as database


class ObjectRelationHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION")}
        app.config.update(
            TESTING=True, TEST_CSRF_PROTECTION=False,
            DATABASE=str(base / "users.sqlite"), DOCUMENT_ROOT=str(base / "documents"),
        )
        with app.app_context():
            database.ensure_auth_database()
        self.client = app.test_client()
        self.client.post("/auth/register", data={"username": "linker", "password": "linker-password-123"})
        self.client.post("/auth/login", data={"username": "linker", "password": "linker-password-123"})
        self.laptop = self.create_item("Laptop")
        self.adapter = self.create_item("Netzteil")

    def tearDown(self):
        app.config.update(self.saved)

    def create_item(self, title):
        response = self.client.post("/inventory/items", data={
            "response_format": "json", "title": title, "item_type": "object",
        })
        self.assertEqual(200, response.status_code)
        return response.get_json()["object_id"]

    def test_accessory_links_both_directions(self):
        added = self.client.post(
            f"/inventory/{self.laptop}/relations",
            json={"kind": "accessory", "target_id": self.adapter, "note": "65W"},
        )
        self.assertEqual(201, added.status_code)
        links = self.client.get(f"/inventory/{self.laptop}/relations").get_json()
        self.assertEqual(1, len(links["outgoing"]))
        self.assertEqual("Netzteil", links["outgoing"][0]["target"]["name"])
        backlinks = self.client.get(f"/inventory/{self.adapter}/relations").get_json()
        self.assertEqual(1, len(backlinks["incoming"]))
        self.assertEqual("Laptop", backlinks["incoming"][0]["source"]["name"])
        detail = self.client.get(f"/inventory/{self.laptop}").get_data(as_text=True)
        self.assertIn("Netzteil", detail)
        self.assertIn("65W", detail)
        adapter_detail = self.client.get(f"/inventory/{self.adapter}").get_data(as_text=True)
        self.assertIn("Verwendet von", adapter_detail)
        self.assertIn("Laptop", adapter_detail)

    def test_identical_links_are_idempotent(self):
        for _ in range(2):
            response = self.client.post(
                f"/inventory/{self.laptop}/relations",
                json={"kind": "member", "target_id": self.adapter},
            )
            self.assertEqual(201, response.status_code)
        links = self.client.get(f"/inventory/{self.laptop}/relations").get_json()
        self.assertEqual(1, len(links["outgoing"]))

    def test_relations_are_validated(self):
        bad_kind = self.client.post(
            f"/inventory/{self.laptop}/relations",
            json={"kind": "friend", "target_id": self.adapter})
        self.assertEqual(400, bad_kind.status_code)
        unknown_target = self.client.post(
            f"/inventory/{self.laptop}/relations",
            json={"kind": "accessory", "target_id": "00000000-0000-0000-0000-000000000000"})
        self.assertEqual(400, unknown_target.status_code)
        self_link = self.client.post(
            f"/inventory/{self.laptop}/relations",
            json={"kind": "accessory", "target_id": self.laptop})
        self.assertEqual(400, self_link.status_code)

    def test_relations_can_be_removed(self):
        added = self.client.post(
            f"/inventory/{self.laptop}/relations",
            json={"kind": "accessory", "target_id": self.adapter})
        relation_id = added.get_json()["relations"][0]["relation_id"]
        removed = self.client.post(
            f"/inventory/{self.laptop}/relations/remove",
            json={"relation_id": relation_id})
        self.assertEqual(200, removed.status_code)
        links = self.client.get(f"/inventory/{self.laptop}/relations").get_json()
        self.assertEqual([], links["outgoing"])
        missing = self.client.post(
            f"/inventory/{self.laptop}/relations/remove",
            json={"relation_id": relation_id})
        self.assertEqual(400, missing.status_code)

    def test_relation_endpoints_require_login_and_form_is_visible(self):
        anonymous = app.test_client()
        denied = anonymous.post(f"/inventory/{self.laptop}/relations",
                                json={"kind": "accessory", "target_id": self.adapter},
                                follow_redirects=False)
        self.assertIn(denied.status_code, (302, 303))
        html = self.client.get(f"/inventory/{self.laptop}").get_data(as_text=True)
        self.assertIn("data-object-relations", html)
        self.assertIn("Zubehör und Sets", html)


if __name__ == "__main__":
    unittest.main()
