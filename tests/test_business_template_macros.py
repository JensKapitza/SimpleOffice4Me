from __future__ import annotations

import unittest

from app.business_template_macros import (
    MacroSyntaxError,
    macro_diagnostics,
    macro_tokens,
    resolve_tokens,
    rich_text_runs,
)
from app.business_template_editor import default_visual_layout, validate_visual_layout


class BusinessTemplateMacroTests(unittest.TestCase):
    def test_resolve_tokens_replaces_known_values_and_keeps_unknown(self):
        text = "{{firma.name}} / {{rechnung.nummer}} / {{unknown.value}}"
        rendered = resolve_tokens(
            text,
            {"firma.name": "Muster GmbH", "rechnung.nummer": "RE-2026-0001"},
        )
        self.assertEqual("Muster GmbH / RE-2026-0001 / {{unknown.value}}", rendered)
        self.assertEqual({"firma.name", "rechnung.nummer", "unknown.value"}, macro_tokens(text))

    def test_rich_text_runs_support_nested_safe_formatting(self):
        runs = rich_text_runs(
            "[B]Hallo [COLOR=#1e88e5]{{firma.name}}[/COLOR][/B]",
            {"firma.name": "Muster GmbH"},
        )
        self.assertEqual("Hallo ", runs[0]["text"])
        self.assertTrue(runs[0]["bold"])
        self.assertEqual("Muster GmbH", runs[1]["text"])
        self.assertTrue(runs[1]["bold"])
        self.assertEqual("#1E88E5", runs[1]["color"])

    def test_size_alignment_italic_and_underline_are_parsed(self):
        runs = rich_text_runs(
            "[ALIGN=center][SIZE=12][I][U]Text[/U][/I][/SIZE][/ALIGN]"
        )
        self.assertEqual(1, len(runs))
        self.assertEqual("Text", runs[0]["text"])
        self.assertEqual("center", runs[0]["align"])
        self.assertEqual(12.0, runs[0]["font_size"])
        self.assertTrue(runs[0]["italic"])
        self.assertTrue(runs[0]["underline"])

    def test_invalid_supported_markup_is_rejected(self):
        cases = (
            "[SIZE=99]Text[/SIZE]",
            "[COLOR=red]Text[/COLOR]",
            "[ALIGN=middle]Text[/ALIGN]",
            "[B]Text[/I]",
            "[B]Text",
            "[B=x]Text[/B]",
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(MacroSyntaxError):
                rich_text_runs(value)

    def test_diagnostics_report_unknown_tokens_and_syntax(self):
        result = macro_diagnostics("[B]{{firma.name}} {{foo.bar}}", {"firma.name"})
        self.assertEqual(["foo.bar"], result["unknown_tokens"])
        self.assertIn("not closed", result["syntax_error"])

    def test_visual_layout_rejects_unknown_tokens_and_invalid_macros(self):
        layout = default_visual_layout("invoice")
        layout["elements"][0]["text"] = "{{custom.unsafe}}"
        with self.assertRaisesRegex(ValueError, "unknown template token"):
            validate_visual_layout(layout)

        layout = default_visual_layout("invoice")
        layout["elements"][0]["text"] = "[COLOR=red]Text[/COLOR]"
        with self.assertRaisesRegex(ValueError, "invalid template macro"):
            validate_visual_layout(layout)


if __name__ == "__main__":
    unittest.main()
