"""Vision/ML admin page, MCP key/switch sections and ML error codes."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app
from app import db as database
from app import mcp_config, vision_config


class VisionAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("TESTING", "DATABASE", "DOCUMENT_ROOT", "TEST_CSRF_PROTECTION", "MCP_ENABLED")}
        app.config.update(TESTING=True, DATABASE=str(self.root / "db.sqlite"), DOCUMENT_ROOT=str(self.root / "documents"), TEST_CSRF_PROTECTION=False)
        for module in (vision_config, mcp_config):
            patcher = patch.object(module, "default_config_path", return_value=self.root / "mini.json")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = app.app_context()
        self.context.push()
        database.ensure_auth_database()
        db = database.get_db()
        for name, admin in [("vision-admin", 1), ("vision-user", 0)]:
            db.execute("INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                       (name, generate_password_hash("test-only-password"), admin))
        db.commit()
        self.client = app.test_client()

    def tearDown(self):
        self.context.pop()
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, name="vision-admin"):
        with self.client.session_transaction() as session:
            row = database.get_db().execute("SELECT id FROM user WHERE username=?", (name,)).fetchone()
            session["user_id"] = row[0]

    def test_page_needs_admin(self):
        self.login("vision-user")
        response = self.client.get("/admin/mini-services/vision")
        self.assertEqual(403, response.status_code)

    def test_page_renders_bilingual_without_key(self):
        self.login()
        body = self.client.get("/admin/mini-services/vision").get_data(as_text=True)
        self.assertIn("Vision", body)
        self.assertIn("Mini Services", body)

    def test_save_provider_and_consent(self):
        self.login()
        response = self.client.post("/admin/mini-services/vision", data={
            "enabled": "1", "provider": "meta", "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "", "ollama_timeout": "60", "meta_consent": "1",
            "meta_endpoint": "", "meta_model": "", "meta_api_key": "",
        })
        self.assertEqual(302, response.status_code)
        stored = vision_config.settings()
        self.assertEqual("meta", stored["provider"])
        self.assertTrue(stored["meta_consent"])
        self.assertTrue(stored["enabled"])

    def test_compatible_settings_persist_with_free_model_and_loopback_endpoint(self):
        self.login()
        response = self.client.post("/admin/mini-services/vision", data={
            "enabled": "1", "provider": "compatible", "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "mimo-v2.6", "ollama_timeout": "60",
            "openai_endpoint": "http://127.0.0.1:1234/v1", "openai_model": "Vendor/MiMo-v2.6",
        })
        self.assertEqual(302, response.status_code)
        stored = vision_config.settings()
        self.assertEqual("compatible", stored["provider"])
        self.assertEqual("Vendor/MiMo-v2.6", stored["openai_model"])
        self.assertEqual("mimo-v2.6", stored["ollama_model"])
        from flask import g, render_template
        with app.test_request_context():
            g.language = "de"
            rendered = render_template("contextflow/_provider_consent.html")
        self.assertIn("127.0.0.1:1234", rendered)
        self.assertIn("Vendor/MiMo-v2.6", rendered)
        self.assertNotIn("echte Bürodaten bleiben lokal", rendered)
        self.assertNotIn("Meta Contributor", rendered)

    def test_inventory_shows_consent_for_configured_external_provider(self):
        self.login()
        clean = vision_config.prepare({"provider": "compatible", "openai_endpoint": "https://example.com/api/v1",
                                       "openai_model": "Vendor/MiMo-v2.6", "openai_consent": True,
                                       "openai_api_key": "synthetic-test-key"})
        vision_config.persist(clean)
        with patch.dict(app.config, {"CONTEXTFLOW_ENABLED": True}):
            response = self.client.get("/inventory")
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('id="meta-photo-consent"', body)
        self.assertIn("example.com", body)
        self.assertIn("Vendor/MiMo-v2.6", body)
        self.assertNotIn("synthetic-test-key", body)
        self.assertNotIn("Dieses Foto für die Analyse an Meta Muse Spark senden", body)

    def test_changing_api_destination_cannot_reuse_old_secret_implicitly(self):
        clean = vision_config.prepare({"provider": "openai", "openai_api_key": "synthetic-test-key"})
        vision_config.persist(clean)
        with self.assertRaisesRegex(ValueError, "Anbieterwechsel"):
            vision_config.prepare({"provider": "compatible", "openai_endpoint": "https://example.com/v1"})
        clean = vision_config.prepare({"provider": "compatible", "openai_endpoint": "http://127.0.0.1:1234/v1", "openai_api_key_clear": True})
        self.assertEqual("", clean["openai_api_key_enc"])

    def test_key_stored_encrypted_never_cleartext(self):
        self.login()
        self.client.post("/admin/mini-services/vision", data={
            "enabled": "1", "provider": "meta", "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "", "ollama_timeout": "60", "meta_consent": "1",
            "meta_endpoint": "", "meta_model": "", "meta_api_key": "sk-test-only-key",
        })
        raw = vision_config.settings_path().read_text(encoding="utf-8")
        self.assertNotIn("sk-test-only-key", raw)
        self.assertIn("enc:v1:", raw)
        effective = vision_config.effective_settings({})
        self.assertEqual("sk-test-only-key", effective["meta_api_key"])
        body = self.client.get("/admin/mini-services/vision").get_data(as_text=True)
        self.assertNotIn("sk-test-only-key", body)
        self.assertIn("vorhanden", body)

    def test_environment_key_is_ignored(self):
        self.login()
        self.client.post("/admin/mini-services/vision", data={
            "enabled": "1", "provider": "local", "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "", "ollama_timeout": "60",
            "meta_endpoint": "", "meta_model": "", "meta_api_key": "",
        })
        with patch.dict(os.environ, {"MODEL_API_KEY": "env-only-key"}):
            effective = vision_config.effective_settings({})
        self.assertEqual("", effective["meta_api_key"])

    def test_ml_switch_off_disables_analysis(self):
        from app.inventory_capture_vision import capture_ml_label

        self.login()
        self.client.post("/admin/mini-services/vision", data={
            "provider": "local", "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "", "ollama_timeout": "60",
            "meta_endpoint": "", "meta_model": "", "meta_api_key": "",
        })
        result = capture_ml_label(b"fake-png-bytes", "")
        self.assertFalse(result["available"])
        self.assertEqual("disabled", result["error_code"])

    def test_error_codes_distinguish_causes(self):
        from app.inventory_capture_vision import _ml_error_code

        self.assertEqual("no_demo_root", _ml_error_code(PermissionError("Meta inference is limited to a synthetic demo root")))
        self.assertEqual("no_consent", _ml_error_code(PermissionError("Meta inference needs explicit consent")))
        self.assertEqual("no_key", _ml_error_code(PermissionError("Meta inference needs a Model API key")))
        self.assertEqual("not_configured", _ml_error_code(PermissionError("Meta inference is not configured")))
        self.assertEqual("unavailable", _ml_error_code(ValueError("boom")))

    def test_mcp_key_section_admin_only_and_encrypted(self):
        self.login("vision-user")
        body = self.client.get("/settings/mcp").get_data(as_text=True)
        self.assertNotIn("meta_api_key", body)
        before = vision_config.settings_path().exists()
        self.client.post("/settings/mcp", data={"meta_form": "1", "meta_api_key": "sk-nope"})
        self.assertEqual(before, vision_config.settings_path().exists())
        self.login()
        response = self.client.post("/settings/mcp", data={"meta_form": "1", "meta_api_key": "sk-mcp-key"})
        self.assertEqual(302, response.status_code)
        raw = vision_config.settings_path().read_text(encoding="utf-8")
        self.assertNotIn("sk-mcp-key", raw)
        body = self.client.get("/settings/mcp").get_data(as_text=True)
        self.assertNotIn("sk-mcp-key", body)
        self.assertIn("meta_api_key", body)

    def test_mcp_switch_admin_only_and_live(self):
        self.login("vision-user")
        self.client.post("/settings/mcp", data={"mcp_form": "1"})
        self.assertTrue(mcp_config.is_enabled())
        self.login()
        response = self.client.post("/settings/mcp", data={"mcp_form": "1"})
        self.assertEqual(302, response.status_code)
        self.assertFalse(mcp_config.is_enabled())
        self.assertFalse(app.config["MCP_ENABLED"])
        body = self.client.get("/settings/mcp").get_data(as_text=True)
        self.assertNotIn('id="mcp-enabled" checked', body)


if __name__ == "__main__":
    unittest.main()
