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

    def test_ci_packaging_helper_uses_shared_sha256_lock(self):
        root = Path(__file__).resolve().parents[1]
        lock_text = (root / "requirements-ci-packaging.txt").read_text(encoding="utf-8")
        self.assertIn("packaging==26.3", lock_text)
        self.assertEqual(
            {
                "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
                "94edc256424af38762eb31306eed28beb9f0efc50a8837492c9d6fd6004aed79",
            },
            set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock_text)),
        )
        workflow_root = root / ".github" / "workflows"
        lock_references = 0
        for path in workflow_root.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("packaging==26.3", text, path.name)
            for line in text.splitlines():
                if "requirements-ci-packaging.txt" in line:
                    lock_references += 1
                    self.assertIn("--require-hashes", line, f"{path}:{line}")
        self.assertGreater(lock_references, 0)


if __name__ == "__main__":
    unittest.main()
