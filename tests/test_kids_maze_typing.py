"""Maze typing mode: markers, styles and viewport fit (behavior is covered in Node and browser tests)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

try:
    from tests.http_api_fixture import PublicHttpTestCase
except ImportError:  # `unittest discover -s tests` puts tests/ on sys.path
    from http_api_fixture import PublicHttpTestCase

from app import app


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "kids" / "learning.html"
SCRIPT = ROOT / "static" / "js" / "kids_learning.js"
CONTENT = ROOT / "static" / "js" / "kids_learning_content.js"
STYLES = ROOT / "static" / "css" / "kids_learning.css"


class KidsMazeTypingTests(PublicHttpTestCase):
    def test_maze_offers_typing_mode_toggle_and_letter_field(self):
        html = self.client.get("/kids/lernen/").get_data(as_text=True)
        self.assertIn("data-maze-typing", html)
        self.assertIn("data-maze-letters", html)
        self.assertIn("data-maze-controls", html)

    def test_typing_mode_disables_arrows_and_uses_letters(self):
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("mazeTypingMode", script)
        self.assertIn("answerMazeLetter", script)
        self.assertIn("mazeWrongStep", script)
        self.assertIn("mazeCorrectStep", script)
        self.assertIn("mazeNextExpected", script)
        self.assertIn("dataset.mazeExpected", script)

    def test_typing_helpers_are_pure_and_exported(self):
        content = CONTENT.read_text(encoding="utf-8")
        for helper in ("mazeDistanceTo", "mazeRouteTo", "mazeLetterNode",
                       "mazeNextExpected", "mazeCorrectStep", "mazeWrongStep"):
            self.assertIn(f"function {helper}(", content)
        self.assertIn("mazeTyping", content)

    def test_letter_buttons_stay_above_44px(self):
        css = STYLES.read_text(encoding="utf-8")
        match = re.search(r"\.kids-maze-letter\s*\{[^}]*min-height:\s*(\d+)px", css)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 44)

    def test_maze_map_scales_with_the_viewport(self):
        css = STYLES.read_text(encoding="utf-8")
        match = re.search(r"\.kids-maze-map\s*\{[^}]*\}", css)
        self.assertIsNotNone(match)
        self.assertIn("vmin", match.group(0))

    def test_maze_grid_fits_its_viewbox(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('viewBox="0 0 400 400"', html)
        # Nodes live on a 4x4 grid at 50 + index * 100, so 350 stays inside.
        content = CONTENT.read_text(encoding="utf-8")
        self.assertIn("next.col < 4 && next.row >= 0 && next.row < 4", content)


if __name__ == "__main__":
    unittest.main()
