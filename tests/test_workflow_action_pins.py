"""Regression tests for immutable GitHub Action references."""

from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

from tools.check_workflow_action_pins import unpinned_action_references


class WorkflowActionPinTests(unittest.TestCase):
    def test_repository_workflow_actions_are_sha_pinned(self):
        workflow_root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        self.assertEqual([], unpinned_action_references(workflow_root))

    def test_mutable_external_refs_are_reported_but_local_reusable_workflows_are_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            workflow_root = Path(temporary)
            (workflow_root / "sample.yml").write_text(
                "steps:\n"
                "  - uses: vendor/action@v1\n"
                "  - uses: ./.github/workflows/reusable.yml\n"
                "  - uses: vendor/pinned@0123456789abcdef0123456789abcdef01234567\n",
                encoding="utf-8",
            )
            self.assertEqual(
                [f"{workflow_root / 'sample.yml'}:2: vendor/action@v1"],
                unpinned_action_references(workflow_root),
            )

    def test_scorecard_docker_base_images_are_digest_pinned(self):
        root = Path(__file__).resolve().parents[1]
        image_reference = re.compile(r"@sha256:[0-9a-f]{64}$")
        for relative_path in (Path("Dockerfile"), Path(".oss-scanner/Dockerfile")):
            with self.subTest(path=relative_path):
                lines = (root / relative_path).read_text(encoding="utf-8").splitlines()
                bases = [line.split()[1] for line in lines if line.strip().startswith("FROM ")]
                self.assertTrue(bases, f"No base image found in {relative_path}")
                self.assertTrue(all(image_reference.search(base) for base in bases), bases)

    def test_scorecard_upload_workflow_has_only_step_scoped_environment(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (
            root
            / ".github"
            / "workflows"
            / "scorecard.yml"
        ).read_text(encoding="utf-8")
        scanner_ignore = (root / ".oss-scanner" / "Dockerfile.dockerignore").read_text(
            encoding="utf-8"
        )
        self.assertIn("!.github/workflows/scorecard.yml", scanner_ignore)
        self.assertNotRegex(workflow, r"(?m)^(?:env|defaults):")
        self.assertNotRegex(workflow, r"(?m)^  (?:env|defaults):")
        lines = workflow.splitlines()
        runtime_settings = [
            line for line in lines if "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24:" in line
        ]
        action_steps = [line for line in lines if re.match(r"^\s+uses:", line)]
        self.assertTrue(action_steps)
        self.assertEqual(len(action_steps), len(runtime_settings))
        self.assertTrue(all(len(line) - len(line.lstrip()) >= 10 for line in runtime_settings))


if __name__ == "__main__":
    unittest.main()
