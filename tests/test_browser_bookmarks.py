from __future__ import annotations

import unittest

from app.browser_bookmarks import _search_pattern


class BookmarkSearchPatternTests(unittest.TestCase):
    def test_search_pattern_escapes_like_metacharacters_and_escape_character(self):
        self.assertEqual("%100!%%", _search_pattern("100%"))
        self.assertEqual("%file!_part%", _search_pattern("file_part"))
        self.assertEqual("%wow!!%", _search_pattern("wow!"))
        self.assertEqual("%!%!_!!%", _search_pattern("%_!"))
