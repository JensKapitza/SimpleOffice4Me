import unittest
from pathlib import Path


class DockerPublishWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/docker-build.yml").read_text(encoding="utf-8")

    def test_publish_is_limited_to_main_hackathon_and_version_refs(self):
        for expected in (
            "github.event_name == 'push' || github.event_name == 'workflow_dispatch'",
            "github.ref == 'refs/heads/main'",
            "github.ref == 'refs/heads/hackathon/contextflow-mvp'",
            "startsWith(github.ref, 'refs/tags/v')",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.workflow)

    def test_hackathon_build_has_isolated_tag_and_cannot_replace_stable_tags(self):
        self.assertIn(
            "type=raw,value=hackathon-contextflow-mvp,enable=${{ github.ref == 'refs/heads/hackathon/contextflow-mvp' }}",
            self.workflow,
        )
        self.assertIn("type=raw,value=latest,enable={{is_default_branch}}", self.workflow)
        for stable_tag in ("steps.version.outputs.version", "steps.version.outputs.release_version"):
            self.assertIn(
                f"type=raw,value=${{{{ {stable_tag} }}}},enable=${{{{ github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v') }}}}",
                self.workflow,
            )

    def test_publish_permission_stays_scoped_to_publish_job(self):
        publish_job = self.workflow.split("\n  publish:", 1)[1]
        self.assertIn("packages: write", publish_job)
        other_jobs = self.workflow.split("\njobs:", 1)[1].split("\n  publish:", 1)[0]
        self.assertNotIn("packages: write", other_jobs)


if __name__ == "__main__":
    unittest.main()
