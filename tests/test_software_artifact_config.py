import json
import tempfile
import unittest
from pathlib import Path

from flask import Flask

from app.software_artifact_config import (
    SoftwareArtifactConfiguration,
    normalize_github_repository,
)


class SoftwareArtifactConfigurationTests(unittest.TestCase):
    def _app(self) -> Flask:
        app = Flask(__name__)
        app.config.update(SECRET_KEY="test-installation-secret", TESTING=True)
        return app

    def test_repository_accepts_https_url_and_owner_repo(self):
        self.assertEqual(
            "JensKapitza/SimpleOffice4Me",
            normalize_github_repository("https://github.com/JensKapitza/SimpleOffice4Me"),
        )
        self.assertEqual(
            "JensKapitza/SimpleOffice4Me",
            normalize_github_repository("JensKapitza/SimpleOffice4Me"),
        )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            normalize_github_repository("http://github.com/JensKapitza/SimpleOffice4Me")

    def test_token_is_encrypted_and_not_returned_by_info(self):
        with tempfile.TemporaryDirectory() as temp:
            config = SoftwareArtifactConfiguration(Path(temp))
            app = self._app()
            with app.app_context():
                result = config.save(
                    "https://github.com/JensKapitza/SimpleOffice4Me",
                    token="github_pat_super_secret_test_value",
                    actor="admin",
                )
                self.assertTrue(result["configured"])
                self.assertEqual("admin", result["source"])
                self.assertNotIn("token", result)
                self.assertEqual("github_pat_super_secret_test_value", config.token())
                raw = config.path.read_text(encoding="utf-8")
                self.assertNotIn("github_pat_super_secret_test_value", raw)
                state = json.loads(raw)
                self.assertTrue(str(state["token"]).startswith("enc:v1:"))

    def test_unmanaged_configuration_uses_default_repository_and_empty_token(self):
        with tempfile.TemporaryDirectory() as temp:
            config = SoftwareArtifactConfiguration(Path(temp))
            app = self._app()
            with app.app_context():
                self.assertEqual("JensKapitza/SimpleOffice4Me", config.repository())
                self.assertEqual("", config.token())
                self.assertFalse(config.info()["managed"])
                self.assertFalse(config.info()["configured"])

    def test_admin_configuration_becomes_authoritative_after_save(self):
        with tempfile.TemporaryDirectory() as temp:
            config = SoftwareArtifactConfiguration(Path(temp))
            app = self._app()
            with app.app_context():
                result = config.save("New/Repo", token="dummy-artifact-token", actor="admin")
                self.assertTrue(result["managed"])
                self.assertTrue(result["configured"])
                self.assertEqual("dummy-artifact-token", config.token())
                self.assertEqual("New/Repo", config.repository())
                cleared = config.save("New/Repo", clear_token=True, actor="admin")
                self.assertTrue(cleared["managed"])
                self.assertFalse(cleared["configured"])
                self.assertEqual("", config.token())
                self.assertEqual("New/Repo", config.repository())

    def test_save_without_token_leaves_configuration_unconfigured(self):
        with tempfile.TemporaryDirectory() as temp:
            config = SoftwareArtifactConfiguration(Path(temp))
            app = self._app()
            with app.app_context():
                result = config.save("JensKapitza/SimpleOffice4Me", actor="admin")
                self.assertTrue(result["managed"])
                self.assertFalse(result["configured"])
                self.assertEqual("", config.token())
                self.assertNotIn("token", result)


if __name__ == "__main__":
    unittest.main()
