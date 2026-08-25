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

    def test_english_boolean_operators_are_supported(self):
        query = compile_query("tag:rechnung AND (name:angebot OR NOT text:liefertermin)")
        self.assertIn(" AND ", query.fts)
        self.assertIn(" OR ", query.fts)
        self.assertIn("NOT", query.where)
        self.assertTrue(query.requires_sql)

    def test_bang_is_alias_for_not(self):
        direct = compile_query("tag:rechnung AND !text:entwurf")
        grouped = compile_query("!(tag:privat OR status:gelöscht)")
        self.assertTrue(direct.requires_sql)
        self.assertIn("NOT", direct.where)
        self.assertTrue(grouped.requires_sql)
        self.assertIn("NOT", grouped.where)

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

    def test_not_xor_and_nor_use_exact_sql_evaluation(self):
        excluded = compile_query("c UND NICHT (a ODER b)")
        self.assertTrue(excluded.requires_sql)
        self.assertIn("NOT", excluded.where)
        exclusive = compile_query("tag:a XOR tag:b")
        self.assertTrue(exclusive.requires_sql)
        self.assertIn("CASE WHEN", exclusive.where)
        neither = compile_query("tag:a NOR tag:b")
        self.assertTrue(neither.requires_sql)
        self.assertIn("NOT", neither.where)

    def test_contains_operator_supports_field_and_all_fields(self):
        field = compile_query('name ~ "Teil vom Namen"')
        self.assertTrue(field.requires_sql)
        self.assertEqual(field.parameters, ("%Teil vom Namen%",))
        all_fields = compile_query("~fragment")
        self.assertTrue(all_fields.requires_sql)
        self.assertEqual(len(all_fields.parameters), 6)

    def test_missing_contains_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Nach ~ fehlt"):
            compile_query("tag ~")


if __name__ == "__main__":
    unittest.main()
