"""Per-candidate identifier isolation in the photo-series queue.

Codex read-back finding: series requests must never copy the main form's
global barcode/ISBN into every candidate (a prior single scan would
contaminate the whole batch). Each queue card carries its own optional
identifier; transfer validates ISBN before touching fields and triggers
the existing metadata lookup without saving anything.

Static source assertions on static/js/inventory.js (same convention as
the static checks in test_qa_736_camera_policy.py; no JS harness exists).
"""

from __future__ import annotations

import unittest
from pathlib import Path


def _source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "static" / "js" / "inventory.js").read_text(encoding="utf-8")


def _region(source: str, start: str, end: str) -> str:
    head = source.index(start)
    tail = source.index(end, head + len(start))
    return source[head:tail]


class SeriesIsolationTest(unittest.TestCase):
    def test_series_requests_use_only_item_local_identifier(self) -> None:
        body = _region(
            _source(),
            "const seriesProcessNext = async () => {",
            "const seriesAddFiles = (files) => {",
        )
        self.assertIn("item.identifier", body)
        self.assertIn("normalizeIsbn", body)
        self.assertNotIn("barcode?.value", body)
        self.assertNotIn("isbn?.value", body)
        self.assertIn("data.append(ownIsbn ? 'isbn' : 'barcode'", body)
        self.assertNotIn("barcode.value = ", body)
        self.assertNotIn("isbn.value = ", body)

    def test_series_cards_carry_their_own_identifier_field(self) -> None:
        source = _source()
        self.assertIn('data-part="identifier"', source)
        self.assertIn("item.identifier = event.target.value", source)
        self.assertIn("identifier: ''", source)

    def test_series_transfer_validates_isbn_before_using_fields(self) -> None:
        body = _region(
            _source(),
            "const seriesTransfer = (id) => {",
            "seriesInput?.addEventListener('change'",
        )
        self.assertIn("normalizeIsbn", body)
        self.assertLess(
            body.index("normalizeIsbn"), body.index("isbn.value = ")
        )
        self.assertIn("item.identifier", body)
        self.assertNotIn("barcode?.value", body)
        self.assertNotIn("isbn?.value", body)
        self.assertIn("isbn.value = '';", body)
        self.assertIn("barcode.value = '';", body)
        self.assertLess(
            body.index("isbn.value = '';"), body.index("isbn.value = ownIsbn")
        )
        self.assertLess(
            body.index("barcode.value = '';"), body.index("barcode.value = ownCode")
        )
        self.assertIn("if (ownIsbn && isbn)", body)
        self.assertIn("else if (ownCode && barcode)", body)
        self.assertIn("barcode.value = ", body)
        self.assertIn("lookup()", body)
        self.assertNotIn("submit(", body)
        self.assertNotIn("requestSubmit", body)

    def test_series_isbn_adopt_is_card_local_and_explicit(self) -> None:
        source = _source()
        self.assertIn('data-part="adopt-isbn"', source)
        self.assertIn('data-part="isbn-row"', source)
        self.assertIn("ISBN übernehmen", source)
        body = _region(
            source,
            "const seriesAdoptIsbn = (id) => {",
            "const seriesCheck = async (id) => {",
        )
        self.assertIn("item.identifier = isbn", body)
        self.assertIn('data-part="identifier"', body)
        self.assertNotIn("barcode.value", body)
        self.assertNotIn("isbn.value", body)
        self.assertNotIn("seriesCheck(", body)
        self.assertNotIn("fetch(", body)
        self.assertNotIn("submit(", body)


if __name__ == "__main__":
    unittest.main()
