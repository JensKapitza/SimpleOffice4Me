"""Public workspace filter controls expose every supported selection in HTML."""
import unittest
from pathlib import Path

from bs4 import BeautifulSoup


class RepositoryWorkspaceMarkupTests(unittest.TestCase):
    def test_all_priority_type_and_status_options_are_real_select_options(self):
        template = Path(__file__).resolve().parents[1] / "templates" / "repository_workspace.html"
        document = BeautifulSoup(template.read_text(encoding="utf-8"), "html.parser")
        expected = {
            "rw-priority": ["", "P0", "P1", "P2", "P3"],
            "rw-platform": ["", "Windows", "Linux", "macOS", "Android", "unknown"],
            "rw-type": ["", "pr", "issue", "ci", "security", "review"],
            "rw-status": ["", "open", "review", "blocked", "missing_evidence", "ready", "done"],
        }
        for identifier, values in expected.items():
            with self.subTest(control=identifier):
                select = document.find("select", id=identifier)
                self.assertIsNotNone(select)
                actual = [option.get("value", option.get_text(strip=True)) for option in select.find_all("option", recursive=False)]
                self.assertEqual(values, actual)

    def test_board_aria_label_has_supported_region_role(self):
        template = Path(__file__).resolve().parents[1] / "templates" / "repository_workspace.html"
        document = BeautifulSoup(template.read_text(encoding="utf-8"), "html.parser")
        board = document.find(id="rw-board")
        self.assertIsNotNone(board)
        self.assertEqual("Statusspalten", board.get("aria-label"))
        self.assertEqual("region", board.get("role"))


if __name__ == "__main__":
    unittest.main()
