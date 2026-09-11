import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    @mock.patch.dict(
        os.environ,
        {
            "SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN": "legacy-token",
            "SIMPLEOFFICE_GITHUB_ARTIFACT_REPOSITORY": "Legacy/Repo",
        },
        clear=False,
    )
    def test_admin_configuration_becomes_authoritative_after_save(self):
        with tempfile.TemporaryDirectory() as temp:
            config = SoftwareArtifactConfiguration(Path(temp))
            app = self._app()
            with app.app_context():
                self.assertEqual("legacy-token", config.token())
                self.assertEqual("Legacy/Repo", config.repository())
                result = config.save("New/Repo", clear_token=True, actor="admin")
                self.assertTrue(result["managed"])
                self.assertFalse(result["configured"])
                self.assertEqual("", config.token())
                self.assertEqual("New/Repo", config.repository())

    @mock.patch.dict(
        os.environ,
        {"SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN": "legacy-token"},
        clear=False,
    )
    def test_first_admin_save_migrates_existing_environment_token(self):
        with tempfile.TemporaryDirectory() as temp:
            config = SoftwareArtifactConfiguration(Path(temp))
            app = self._app()
            with app.app_context():
                result = config.save("JensKapitza/SimpleOffice4Me", actor="admin")
                self.assertTrue(result["configured"])
                self.assertEqual("legacy-token", config.token())
                self.assertNotIn("legacy-token", config.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
