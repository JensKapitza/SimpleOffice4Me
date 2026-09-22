from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_project_policy import policy_errors


VALID_AGENTS = """
Sicherheit und Datenintegrität
Root Cause vor Symptom-Fix
Best-of-all
Definition of Done
python -m compileall -q app tools
python -m unittest discover -s tests -v
pip-audit
tools/cra_check.py
SBOM
"""

VALID_CI = """
python-version: ["3.10", "3.14"]
python tools/check_project_policy.py .
python tools/check_file_size.py . --limit 1000
python tools/check_function_size.py app tools --limit 300
python tools/check_secret_leaks.py .
python -m compileall -q app tools
python -m unittest discover -s tests -v
python -m pip_audit
python tools/cra_check.py
python tools/generate_sbom.py
"""


class ProjectPolicyTest(unittest.TestCase):
    def make_repo(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / "AGENTS.md").write_text(VALID_AGENTS, encoding="utf-8")
        (root / ".github" / "copilot-instructions.md").write_text(
            "Follow AGENTS.md before modifying this repository.\n", encoding="utf-8"
        )
        (root / ".github" / "workflows" / "ci.yml").write_text(VALID_CI, encoding="utf-8")
        (root / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.10"\n', encoding="utf-8"
        )
        return root

    def test_valid_python_repository_has_no_policy_errors(self) -> None:
        root = self.make_repo()
        self.assertEqual([], policy_errors(root))

    def test_missing_ci_gate_is_reported(self) -> None:
        root = self.make_repo()
        ci_path = root / ".github" / "workflows" / "ci.yml"
        ci_path.write_text(VALID_CI.replace("python -m pip_audit\n", ""), encoding="utf-8")

        errors = policy_errors(root)

        self.assertTrue(any("python -m pip_audit" in error for error in errors), errors)

    def test_python_baseline_cannot_be_raised_silently(self) -> None:
        root = self.make_repo()
        (root / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12"\n', encoding="utf-8"
        )

        errors = policy_errors(root)

        self.assertTrue(any("Python >=3.10" in error for error in errors), errors)

    def test_csharp_references_are_only_required_when_csharp_exists(self) -> None:
        root = self.make_repo()
        self.assertEqual([], policy_errors(root))

        (root / "MetaBridge.csproj").write_text("<Project />\n", encoding="utf-8")
        errors = policy_errors(root)

        self.assertTrue(any("instruction.md" in error for error in errors), errors)
        self.assertTrue(any("BrabenderCodeAnalysis.ruleset" in error for error in errors), errors)

    def test_csharp_policy_is_checked_when_project_is_present(self) -> None:
        root = self.make_repo()
        (root / "MetaBridge.csproj").write_text("<Project />\n", encoding="utf-8")
        (root / "instruction.md").write_text("instructions\n", encoding="utf-8")
        (root / "BrabenderCodeAnalysis.ruleset").write_text("<RuleSet />\n", encoding="utf-8")
        (root / "AGENTS.md").write_text(
            VALID_AGENTS
            + "\nMetaBridge\nBrabenderCodeAnalysis.ruleset\nCA1823\nC6259\nSX1101\n",
            encoding="utf-8",
        )

        self.assertEqual([], policy_errors(root))


if __name__ == "__main__":
    unittest.main()
