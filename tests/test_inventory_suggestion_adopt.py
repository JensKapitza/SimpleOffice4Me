"""Shared suggestion adoption: note-as-description and tags, never autosave.

Single-photo adopt and series transfer both route through
applySuggestionToForm, which fills name/type plus the suggestion note
(only into an empty description) and merges suggested tags
deduplicated. Prices stay with the real marketplace lookup; the form is
never submitted automatically.

Static source assertions on static/js/inventory.js and
templates/inventory/index.html (same convention as
test_inventory_series_isolation.py; no JS harness exists for that file).
"""

from __future__ import annotations

import unittest
from pathlib import Path


def _read(relative: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / relative).read_text(encoding="utf-8")


def _region(source: str, start: str, end: str) -> str:
    head = source.index(start)
    tail = source.index(end, head + len(start))
    return source[head:tail]


class SuggestionAdoptTest(unittest.TestCase):
    def test_shared_applier_fills_description_and_tags(self) -> None:
        body = _region(
            _read("static/js/inventory.js"),
            "const applySuggestionToForm = (suggestion) => {",
            "const adoptSuggestion = () => {",
        )
        self.assertIn("title.value = cleanText(suggestion.object_name)", body)
        self.assertIn("suggestionDescription(description.value, descriptionParts.filter(Boolean).join(' · '))", body)
        self.assertIn("suggestion.authors", body)
        self.assertIn("mergeTags(tags.value, suggestion.tags", body)
        self.assertIn("description.value = ", body)
        self.assertIn("tags.value = ", body)
        self.assertNotIn("market-price", body)
        self.assertNotIn("price-source", body)
        self.assertNotIn("fetch(", body)
        self.assertNotIn("submit(", body)
        self.assertNotIn("requestSubmit", body)

    def test_single_photo_adopt_uses_shared_applier_without_saving(self) -> None:
        body = _region(
            _read("static/js/inventory.js"),
            "const adoptSuggestion = () => {",
            "const checkIsbnInTitle = () => {",
        )
        self.assertIn("applySuggestionToForm(pendingSuggestion)", body)
        self.assertNotIn("fetch(", body)
        self.assertNotIn("submit(", body)
        self.assertNotIn("requestSubmit", body)

    def test_series_transfer_uses_shared_applier_without_saving(self) -> None:
        body = _region(
            _read("static/js/inventory.js"),
            "const seriesTransfer = (id) => {",
            "const seriesAdoptIsbn = (id) => {",
        )
        self.assertIn("applySuggestionToForm(item.suggestion)", body)
        self.assertNotIn("submit(", body)
        self.assertNotIn("requestSubmit", body)

    def test_suggestion_boxes_show_tags_for_review(self) -> None:
        source = _read("static/js/inventory.js")
        box = _region(
            source,
            "const showObjectSuggestion = (payload) => {",
            "const analyzePhoto = async () => {",
        )
        self.assertIn("formatSuggestionTags(suggestion.tags)", box)
        self.assertIn("suggestedTags", box)
        card = _region(
            source,
            "const seriesRenderItem = (item) => {",
            "const seriesProcessNext = async () => {",
        )
        self.assertIn("formatSuggestionTags(item.suggestion.tags)", card)
        self.assertIn("tagLine", card)

    def test_template_loads_helpers_before_inventory_and_shows_tags(self) -> None:
        template = _read("templates/inventory/index.html")
        helpers = template.index("js/suggestion_fields.js")
        main = template.index("js/inventory.js")
        self.assertLess(helpers, main)
        self.assertIn('id="suggested-tags"', template)
        self.assertIn('id="adopt-suggestion"', template)


if __name__ == "__main__":
    unittest.main()
