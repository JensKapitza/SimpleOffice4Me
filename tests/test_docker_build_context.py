import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DockerBuildContextSecurityTests(unittest.TestCase):
    def test_main_image_excludes_local_dotenv_and_compose_secrets(self):
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "ubuntu-26-compat.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("COPY . /opt/simpleoffice4me", dockerfile)
        self.assertIn("      - '.dockerignore'", workflow)
        self.assertIn("Create disposable local-secret sentinels for image-context test", workflow)
        self.assertIn("Verify local secret files are absent from the image", workflow)
        self.assertIn("test ! -e /opt/simpleoffice4me/.env", workflow)
        for pattern in (
            "**/.env",
            "**/.env.*",
            "!deploy/docker/.env.example",
            ".runtime-tools/",
            "env/",
            "venv/",
            "ENV/",
            "env.bak/",
            "venv.bak/",
            "deploy/docker/secrets/",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignore)
        for pattern in (
            ".env",
            ".env.*",
            "!deploy/docker/.env.example",
            "deploy/docker/secrets/",
        ):
            with self.subTest(gitignore_pattern=pattern):
                self.assertIn(pattern, gitignore)


if __name__ == "__main__":
    unittest.main()
