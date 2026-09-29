from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.contact_management import ContactManagement
from app.contact_store import ContactStore
from app.project_store import ProjectStore
from app.todo_store import TodoStore
from app.v3_crm import CRMRelationshipService, crm_workspace
from app.v3_relations import EntityRef


class V3CRMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contacts = ContactStore(self.root)
        self.person = self.contacts.upsert(
            {
                "display_name": "Anna Beispiel",
                "email": "anna@example.test",
            },
            "admin",
        )
        self.duplicate = self.contacts.upsert(
            {
                "display_name": "Anna B.",
                "phone": "+49 123 456",
            },
            "admin",
        )
        self.company = self.contacts.upsert(
            {
                "display_name": "Beispiel GmbH",
                "company": "Beispiel GmbH",
            },
            "admin",
        )
        self.service = CRMRelationshipService(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_multiple_historical_roles_can_exist_for_same_pair(self):
        former = self.service.add_role(
            "admin",
            EntityRef("contact", self.person["contact_id"]),
            EntityRef("company", self.company["contact_id"]),
            role="works_at",
            status="former",
            valid_from="2020-01-01",
            valid_to="2022-12-31",
            title="Technik",
        )
        current = self.service.add_role(
            "admin",
            EntityRef("contact", self.person["contact_id"]),
            EntityRef("company", self.company["contact_id"]),
            role="works_at",
            status="active",
            valid_from="2025-01-01",
            title="Projektleitung",
        )
        rows = self.service.roles_for(
            "admin",
            EntityRef("contact", self.person["contact_id"]),
        )
        self.assertEqual(
            {former.role_id, current.role_id},
            {row.role_id for row in rows},
        )
        self.assertEqual(
            {"former", "active"},
            {row.status for row in rows},
        )

    def test_relationship_does_not_change_vcard_roundtrip_data(self):
        before = self.contacts.vcard(self.person["contact_id"], "admin")
        self.service.add_role(
            "admin",
            EntityRef("contact", self.person["contact_id"]),
            EntityRef("company", self.company["contact_id"]),
            role="contact_for",
            title="Technischer Ansprechpartner",
        )
        after = self.contacts.vcard(self.person["contact_id"], "admin")
        self.assertEqual(before, after)

    def test_non_manager_cannot_create_contact_relationship(self):
        with self.assertRaises(PermissionError):
            self.service.add_role(
                "other",
                EntityRef("contact", self.person["contact_id"]),
                EntityRef("company", self.company["contact_id"]),
                role="works_at",
            )

    def test_contact_merge_preserves_relationship_roles(self):
        role = self.service.add_role(
            "admin",
            EntityRef("contact", self.duplicate["contact_id"]),
            EntityRef("company", self.company["contact_id"]),
            role="works_at",
            valid_from="2024-01-01",
        )

        merged = ContactManagement(self.root).merge(
            self.person["contact_id"],
            self.duplicate["contact_id"],
            "admin",
        )

        rows = CRMRelationshipService(self.root).roles_for(
            "admin",
            EntityRef("contact", merged["contact_id"]),
        )
        copied = next(row for row in rows if row.role_id == role.role_id)
        self.assertEqual(merged["contact_id"], copied.subject.id)
        self.assertEqual(self.company["contact_id"], copied.object.id)

    def test_workspace_respects_feature_boundaries(self):
        project = ProjectStore(self.root).create_project(
            {"title": "Kundenprojekt"},
            "admin",
        )
        self.service.add_role(
            "admin",
            EntityRef("contact", self.person["contact_id"]),
            EntityRef("project", project["project_id"]),
            role="contact_for",
        )
        TodoStore(self.root).add(
            "Kunde anrufen",
            "admin",
            {"contact_id": self.person["contact_id"]},
        )

        contacts_only = crm_workspace(
            self.root,
            self.person["contact_id"],
            "admin",
            features={"contacts"},
        )
        self.assertEqual([], contacts_only["projects"])
        self.assertEqual([], contacts_only["tasks"])
        self.assertEqual([], contacts_only["documents"])

        with_projects = crm_workspace(
            self.root,
            self.person["contact_id"],
            "admin",
            features={"contacts", "projects"},
        )
        self.assertEqual(
            [project["project_id"]],
            [row["project_id"] for row in with_projects["projects"]],
        )
        self.assertEqual(
            ["Kunde anrufen"],
            [row["title"] for row in with_projects["tasks"]],
        )

    def test_end_role_keeps_history(self):
        role = self.service.add_role(
            "admin",
            EntityRef("contact", self.person["contact_id"]),
            EntityRef("company", self.company["contact_id"]),
            role="employee",
            valid_from="2026-01-01",
        )
        ended = self.service.end_role(
            "admin",
            role.relation_id,
            role.role_id,
            valid_to="2026-09-30",
        )
        self.assertEqual("former", ended.status)
        self.assertEqual("2026-09-30", ended.valid_to)
        rows = self.service.roles_for(
            "admin",
            EntityRef("contact", self.person["contact_id"]),
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("former", rows[0].status)


if __name__ == "__main__":
    unittest.main()
