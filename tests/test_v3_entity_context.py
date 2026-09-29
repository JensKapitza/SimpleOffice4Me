from __future__ import annotations

import unittest
from pathlib import Path

from app.v3_entity_context import (
    EntityContext,
    EntityContextRegistry,
    EntityRequest,
)


class EntityContextRegistryTests(unittest.TestCase):
    def setUp(self):
        self.request = EntityRequest(
            Path("/tmp"),
            "alice",
            False,
            frozenset({"contacts"}),
        )

    def test_feature_denied_entity_is_hidden(self):
        registry = EntityContextRegistry()
        registry.register_entity(
            "project",
            lambda entity_id, request: EntityContext(
                "project",
                entity_id,
                "P",
                "",
                "/projects/1",
            ),
            feature="projects",
        )
        self.assertIsNone(registry.resolve("project", "1", self.request))

    def test_three_types_can_share_same_context_shape(self):
        registry = EntityContextRegistry()
        for entity_type in ("contact", "project", "document"):
            registry.register_entity(
                entity_type,
                lambda entity_id, request, entity_type=entity_type: EntityContext(
                    entity_type,
                    entity_id,
                    entity_type.title(),
                    "",
                    "/legacy/" + entity_id,
                ),
            )
        rows = [
            registry.resolve(entity_type, "1", self.request)
            for entity_type in ("contact", "project", "document")
        ]
        self.assertEqual(
            ["contact", "project", "document"],
            [row.entity_type for row in rows],
        )
        self.assertTrue(all(row.canonical_url.startswith("/legacy/") for row in rows))

    def test_broken_optional_section_is_hidden(self):
        registry = EntityContextRegistry()
        entity = EntityContext("contact", "1", "A", "", "/legacy/1")
        registry.register_section(
            "broken",
            lambda entity, request: (_ for _ in ()).throw(RuntimeError("down")),
        )
        registry.register_section(
            "ok",
            lambda entity, request: {"title": "OK", "items": []},
        )
        sections = registry.sections(entity, self.request)
        self.assertEqual(["OK"], [row["title"] for row in sections])


if __name__ == "__main__":
    unittest.main()
