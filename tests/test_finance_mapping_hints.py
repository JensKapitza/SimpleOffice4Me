"""Mapping hints for bank connections (no writes)."""

from __future__ import annotations

import unittest

from app.finance_mapping_hints import suggest_unmapped_connections


def _connection(connection_id: str, label: str = ""):
    return {"connection_id": connection_id, "label": label or connection_id}


class FinanceMappingHintsTest(unittest.TestCase):
    def test_flags_unmapped(self) -> None:
        rows = [_connection("a", "Bank A"), _connection("b", "Bank B")]
        mappings = {"a": [{"account_id": "x"}]}
        proposals = suggest_unmapped_connections(rows, mappings)
        self.assertEqual([row["connection_id"] for row in proposals], ["b"])
        self.assertIn("Kontenzuordnung", proposals[0]["reason"])

    def test_skips_mapped_and_empty(self) -> None:
        self.assertEqual(suggest_unmapped_connections([], {}), [])
        self.assertEqual(
            suggest_unmapped_connections(
                [_connection("a")], {"a": [{"account_id": "x"}]}), [])

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_unmapped_connections([None, "x"], {}), [])
        self.assertEqual(
            suggest_unmapped_connections([{"label": "Ohne ID"}], {}), [])

    def test_limits_items(self) -> None:
        rows = [_connection(str(i), f"B{i:02d}") for i in range(7)]
        proposals = suggest_unmapped_connections(rows, {})
        self.assertEqual(len(proposals), 5)
        self.assertEqual(proposals[0]["connection_id"], "0")


if __name__ == "__main__":
    unittest.main()
