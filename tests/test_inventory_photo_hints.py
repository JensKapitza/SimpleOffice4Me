"""Photo hints for inventory objects (no writes)."""

from __future__ import annotations

import unittest

from app.inventory_photo_hints import suggest_missing_photos


def _row(object_id: str, name: str = "", photos=None):
    return {
        "object_id": object_id,
        "name": name,
        "inventory": {"photos": photos if photos is not None else []},
    }


class InventoryPhotoHintsTest(unittest.TestCase):
    def test_flags_photo_less_rows(self) -> None:
        rows = [
            _row("a", "Bohrer", photos=[{"filename": "x.jpg"}]),
            _row("b", "Hammer"),
        ]
        proposals = suggest_missing_photos(rows)
        self.assertEqual([row["object_id"] for row in proposals], ["b"])
        self.assertIn("kein Inventarfoto", proposals[0]["reason"])

    def test_skips_garbage(self) -> None:
        self.assertEqual(suggest_missing_photos([]), [])
        self.assertEqual(suggest_missing_photos([None, "x"]), [])
        self.assertEqual(len(suggest_missing_photos([{"object_id": "a"}])), 1)

    def test_missing_name_falls_back(self) -> None:
        proposals = suggest_missing_photos([_row("a")])
        self.assertEqual(proposals[0]["name"], "ohne Namen")

    def test_limits_and_sorts(self) -> None:
        rows = [_row(str(i), f"Teil {6 - i}") for i in range(7)]
        proposals = suggest_missing_photos(rows)
        self.assertEqual(len(proposals), 5)
        self.assertEqual(proposals[0]["name"], "Teil 0")


if __name__ == "__main__":
    unittest.main()
