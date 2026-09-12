from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
CONFLICT = re.compile(r"^(?:<<<<<<<|=======|>>>>>>>)", re.MULTILINE)
STATIC_ID = re.compile(r"\bid\s*=\s*[\"']([^\"'{}]+)[\"']", re.IGNORECASE)
BLANK_LINK = re.compile(r"<a\b[^>]*\btarget\s*=\s*[\"']_blank[\"'][^>]*>", re.IGNORECASE)


class UiMarkupContractTests(unittest.TestCase):
    @staticmethod
    def _templates():
        return sorted(TEMPLATES.rglob("*.html"))

    def test_templates_have_no_merge_conflict_markers(self):
        broken = []
        for path in self._templates():
            if CONFLICT.search(path.read_text(encoding="utf-8")):
                broken.append(str(path.relative_to(ROOT)))
        self.assertEqual([], broken, f"Merge-Konfliktmarker in UI-Templates: {broken}")

    def test_static_html_ids_are_unique_per_template(self):
        broken = []
        for path in self._templates():
            text = path.read_text(encoding="utf-8")
            ids = STATIC_ID.findall(text)
            duplicates = sorted({value for value in ids if ids.count(value) > 1})
            if duplicates:
                broken.append(f"{path.relative_to(ROOT)}: {', '.join(duplicates)}")
        self.assertEqual([], broken, "Doppelte statische HTML-IDs:\n" + "\n".join(broken))

    def test_blank_links_protect_opener(self):
        broken = []
        for path in self._templates():
            text = path.read_text(encoding="utf-8")
            for match in BLANK_LINK.finditer(text):
                tag = match.group(0).casefold()
                if "rel=\"noopener" not in tag and "rel='noopener" not in tag:
                    line = text.count("\n", 0, match.start()) + 1
                    broken.append(f"{path.relative_to(ROOT)}:{line}")
        self.assertEqual([], broken, "target=_blank ohne rel=noopener:\n" + "\n".join(broken))


if __name__ == "__main__":
    unittest.main()
