import unittest

from app.search_query import compile_query


class SearchQueryTest(unittest.TestCase):
    def test_german_boolean_operators_and_fields_are_compiled(self):
        query = compile_query("tag:rechnung UND (name:angebot ODER text:liefertermin)")
        self.assertIn("tags :", query.fts)
        self.assertIn("path :", query.fts)
        self.assertIn("content :", query.fts)
        self.assertIn(" AND ", query.fts)
        self.assertIn(" OR ", query.fts)

    def test_adjacent_terms_mean_and_and_prefix_is_supported(self):
        query = compile_query("name:ange* tag:rech*")
        self.assertIn("AND", query.fts)
        self.assertIn('"ange"*', query.fts)
        self.assertIn('"rech"*', query.fts)

    def test_quoted_unicode_phrase_is_preserved(self):
        query = compile_query('text: "Übergabe nächste Woche"')
        self.assertIn("Übergabe nächste Woche", query.fts)

    def test_unknown_field_and_missing_term_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unbekanntes Suchfeld"):
            compile_query("kunde:muster")
        with self.assertRaisesRegex(ValueError, "fehlt ein Suchbegriff"):
            compile_query("tag:")

    def test_query_complexity_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "zu viele Teile"):
            compile_query(" ".join(["wort"] * 101))


if __name__ == "__main__":
    unittest.main()
