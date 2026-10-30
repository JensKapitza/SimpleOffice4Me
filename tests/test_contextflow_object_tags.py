"""Grounded suggestion tags for inventory photo analysis.

The object suggestion schema carries a short list of tags with only
directly visible features; an empty list is correct, brands, models,
prices, or identifiers must never be guessed. Validation imports stay
on the ``vision`` compatibility path; the prompt lives in the extracted
``object_suggest`` module.
"""

from __future__ import annotations

import unittest

from app.contextflow.object_suggest import normalize_object_tags, object_instruction
from app.contextflow.vision import VisionError, validate_object_suggestion


class ObjectSuggestionTagsTest(unittest.TestCase):
    def test_tags_grounded_list_normalized(self) -> None:
        out = validate_object_suggestion(
            {"object_name": "Hammer",
             "tags": ["rot", "  Holzgriff ", "Metall", "rot", "ROT",
                      7, None, "", "x" * 41, "a", "b", "c", "d"]}
        )
        self.assertEqual(
            out["tags"], ["rot", "Holzgriff", "Metall", "a", "b", "c"]
        )

    def test_tags_missing_or_not_a_list_is_empty(self) -> None:
        self.assertEqual(
            validate_object_suggestion({"object_name": "X"})["tags"], []
        )
        for raw in ("rot, metall", 42, True, None, {"tag": "x"}):
            with self.subTest(raw=raw):
                out = validate_object_suggestion(
                    {"object_name": "X", "tags": raw}
                )
                self.assertEqual(out["tags"], [])

    def test_tags_normalizer_is_shared(self) -> None:
        self.assertEqual(normalize_object_tags(["A", "a ", 1]), ["A"])
        self.assertEqual(normalize_object_tags("keine-liste"), [])

    def test_instruction_asks_for_grounded_tags_only(self) -> None:
        instruction = object_instruction({})
        self.assertIn("tags", instruction)
        self.assertIn("leere Liste ist korrekt", instruction)
        self.assertIn("niemals Marken, Modelle, Preise", instruction)
        self.assertIn("niemals raten, ableiten oder ergänzen", instruction)

    def test_vision_reexport_stays_compatible(self) -> None:
        from app.contextflow import vision

        self.assertIs(vision.validate_object_suggestion, validate_object_suggestion)
        self.assertIs(vision.VisionError, VisionError)
        with self.assertRaises(VisionError):
            validate_object_suggestion({"object_name": ""})


if __name__ == "__main__":
    unittest.main()
