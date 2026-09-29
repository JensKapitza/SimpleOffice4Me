from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.v3_relations import EntityRef, EntityRegistry, RelationStore, RelationType, default_relation_types


class RelationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = EntityRegistry()
        self.objects = {
            "contact": {"c1": {"name": "A"}, "c2": {"name": "B"}},
            "project": {"p1": {"title": "P1"}, "p2": {"title": "P2"}},
            "document": {"d1": {"title": "D1"}},
        }
        self.denied = set()

        for entity_type in self.objects:
            def resolver(ref, entity_type=entity_type):
                return self.objects[entity_type].get(ref.id)

            def authorizer(principal, action, ref):
                return (principal, ref.key) not in self.denied

            self.registry.register(entity_type, resolver, authorizer)

        self.store = RelationStore(Path(self.temp.name) / "relations.sqlite3", self.registry)
        for item in default_relation_types():
            self.store.register_type(item)

    def tearDown(self):
        self.temp.cleanup()

    def test_three_existing_domain_types_are_referenceable(self):
        self.assertTrue(self.registry.exists(EntityRef("contact", "c1")))
        self.assertTrue(self.registry.exists(EntityRef("project", "p1")))
        self.assertTrue(self.registry.exists(EntityRef("document", "d1")))

    def test_contact_project_and_document_project_relations_roundtrip(self):
        contact = self.store.add("alice", "contact_for", EntityRef("contact", "c1"), EntityRef("project", "p1"))
        document = self.store.add("alice", "document_for", EntityRef("document", "d1"), EntityRef("project", "p1"))
        rows = self.store.list_for("alice", EntityRef("project", "p1"))
        self.assertEqual({contact.relation_id, document.relation_id}, {row.relation_id for row in rows})
        self.assertTrue(self.store.remove("alice", contact.relation_id))
        self.assertIsNone(self.store.get("alice", contact.relation_id))

    def test_unknown_or_missing_entities_fail_before_persistence(self):
        with self.assertRaises(ValueError):
            self.store.add("alice", "related_to", EntityRef("unknown", "x"), EntityRef("project", "p1"))
        with self.assertRaises(LookupError):
            self.store.add("alice", "related_to", EntityRef("contact", "missing"), EntityRef("project", "p1"))

    def test_permissions_are_checked_on_both_sides_and_list_does_not_leak(self):
        relation = self.store.add("alice", "related_to", EntityRef("contact", "c1"), EntityRef("project", "p1"))
        self.denied.add(("bob", EntityRef("project", "p1").key))
        self.assertIsNone(self.store.get("bob", relation.relation_id))
        self.assertEqual([], self.store.list_for("bob", EntityRef("contact", "c1")))
        with self.assertRaises(PermissionError):
            self.store.remove("bob", relation.relation_id)

    def test_deleted_target_is_reported_as_orphan_instead_of_crashing(self):
        relation = self.store.add("alice", "related_to", EntityRef("contact", "c1"), EntityRef("project", "p1"))
        del self.objects["project"]["p1"]
        row = self.store.get("alice", relation.relation_id)
        self.assertIsNotNone(row)
        self.assertFalse(row.target_exists)

    def test_undirected_relation_is_canonical_and_duplicate_safe(self):
        first = self.store.add("alice", "related_to", EntityRef("contact", "c1"), EntityRef("contact", "c2"))
        with self.assertRaises(ValueError):
            self.store.add("alice", "related_to", EntityRef("contact", "c2"), EntityRef("contact", "c1"))
        self.assertIsNotNone(self.store.get("alice", first.relation_id))

    def test_cycle_rule_for_directed_relation(self):
        self.store.register_type(RelationType("depends_on", directed=True, allow_cycles=False))
        self.store.add("alice", "depends_on", EntityRef("project", "p1"), EntityRef("project", "p2"))
        with self.assertRaises(ValueError):
            self.store.add("alice", "depends_on", EntityRef("project", "p2"), EntityRef("project", "p1"))


if __name__ == "__main__":
    unittest.main()
