"""Series-save form reset clears per-candidate fields but keeps the shelf.

Static source assertions on static/js/inventory.js (same convention as
test_inventory_series_isolation.py; no JS harness exists).
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


class SeriesResetTest(unittest.TestCase):
    def test_series_reset_clears_price_and_candidate_fields(self) -> None:
        body = _region(
            _source(),
            "const seriesResetForm = () => {",
            "const seriesSave = async (id) => {",
        )
        for field in (
            "'market-price'", "'price-source'", "'description'", "'location'",
            "'tags'", "'manufacturer'", "'model'", "'serial-number'",
            "'purchase-date'", "'authors'", "'publisher'", "'book-language'",
            "'metadata-source'", "'metadata-checked-at'",
        ):
            with self.subTest(field=field):
                self.assertIn(field, body)
        self.assertIn("allow.checked = false", body)

    def test_series_reset_retains_series_shelf_and_eur_default(self) -> None:
        body = _region(
            _source(),
            "const seriesResetForm = () => {",
            "const seriesSave = async (id) => {",
        )
        self.assertNotIn("seriesShelf", body)
        self.assertNotIn("series-shelf", body)
        self.assertIn("currency.value = 'EUR'", body)


if __name__ == "__main__":
    unittest.main()
