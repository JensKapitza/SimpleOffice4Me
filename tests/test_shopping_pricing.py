import unittest
from decimal import Decimal

from app.shopping_pricing import display_eur, estimated_total, parse_decimal


class ShoppingPricingTests(unittest.TestCase):
    def test_parse_decimal_accepts_german_and_international_amounts(self):
        self.assertEqual(Decimal("1.49"), parse_decimal("1,49 €"))
        self.assertEqual(Decimal("1234.56"), parse_decimal("1.234,56 EUR"))
        self.assertEqual(Decimal("1234.56"), parse_decimal("1,234.56"))

    def test_parse_decimal_rejects_negative_and_malformed_values(self):
        for value in ("-1,00", "1,2,3", "zwölf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_decimal(value)

    def test_total_multiplies_open_quantity_and_skips_bought_items(self):
        result = estimated_total([
            {"status": "open", "price": "1,49", "quantity": "2"},
            {"status": "open", "price": "0,50", "quantity": "3"},
            {"status": "open", "price": "", "quantity": "1"},
            {"status": "bought", "price": "99,99", "quantity": "1"},
        ])
        self.assertEqual(Decimal("4.48"), result["amount"])
        self.assertEqual(2, result["priced_items"])
        self.assertEqual(1, result["missing_prices"])
        self.assertEqual("1.234,56 €", display_eur(Decimal("1234.56")))


if __name__ == "__main__":
    unittest.main()
