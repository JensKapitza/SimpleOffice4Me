"""Guard the S3 data-source inventory when persistent stores are added."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from app.s3_overlay.coverage import PERSISTENCE_COVERAGE


class S3OverlayCoverageTests(unittest.TestCase):
    def test_persistent_store_modules_are_registered_or_explicitly_excluded(self):
        app_root = Path(__file__).resolve().parents[1] / "app"
        discovered: set[str] = {"business_document_generation"}
        for path in app_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            has_store_class = any(
                isinstance(node, ast.ClassDef)
                and node.name.casefold().endswith(("store", "repository", "ledger"))
                for node in tree.body
            )
            if has_store_class or "CREATE TABLE" in source.upper():
                discovered.add(path.relative_to(app_root).with_suffix("").as_posix().replace("/", "."))

        self.assertEqual(
            discovered,
            set(PERSISTENCE_COVERAGE),
            "Update S3 persistence coverage with a provider or explicit security/ownership exclusion.",
        )
        for module, classification in PERSISTENCE_COVERAGE.items():
            self.assertTrue(classification.startswith(("provider:", "admin-provider:", "excluded:")), module)
            if classification.startswith(("admin-provider:", "excluded:")):
                self.assertGreater(len(classification.split(":", 1)[1].strip()), 12, module)


if __name__ == "__main__":
    unittest.main()
