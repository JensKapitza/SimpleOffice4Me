"""Admin, migration, and authentication tests for public discovery reads."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash

from app import app, db as database
from app.federation_local_profile import local_profile
from app.settings_store import (
    DEFAULT_FEDERATION_PROFILE_COUNTRY,
    DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED,
    SettingsStore,
)


class FederationPublicDirectorySettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = {
            key: app.config.get(key)
            for key in (
                "DATABASE", "DOCUMENT_ROOT", "SECRET_KEY", "TESTING",
                "TEST_CSRF_PROTECTION",
            )
        }
        app.config.update(
            DATABASE=str(self.root / "auth.sqlite3"),
            DOCUMENT_ROOT=str(self.root),
            SECRET_KEY="federation-directory-settings-test",
            TESTING=True,
            TEST_CSRF_PROTECTION=False,
        )
        self.environment = patch.dict(os.environ, {
            "SIMPLEOFFICE_FEDERATION_PUBLIC_DIRECTORY": "0",
            "SIMPLEOFFICE_FEDERATION_DIRECTORY_TOKEN": "",
            "SIMPLEOFFICE_FEDERATION_TOKEN": "",
            "SIMPLEOFFICE_FEDERATION_PUBLIC_URL": "",
            "SIMPLEOFFICE_FEDERATION_COUNTRY": "",
        })
        self.environment.start()
        with app.app_context():
            database.ensure_auth_database()
            db = database.get_db()
            for username, is_admin in (("federation-admin", 1), ("federation-user", 0)):
                db.execute(
                    "INSERT INTO user(username,password,is_admin,created_at,updated_at) "
                    "VALUES(?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (username, generate_password_hash(username), is_admin),
                )
            db.commit()
        self.admin = app.test_client()
        self.user = app.test_client()
        self.admin.post(
            "/auth/login",
            data={"username": "federation-admin", "password": "federation-admin"},
        )
        self.user.post(
            "/auth/login",
            data={"username": "federation-user", "password": "federation-user"},
        )
        for client, token in ((self.admin, "a" * 40), (self.user, "u" * 40)):
            with client.session_transaction() as session:
                session["_csrf_token"] = token
        app.config["TEST_CSRF_PROTECTION"] = True

    def tearDown(self):
        app.config.update(self.config)
        self.environment.stop()
        self.temp.cleanup()

    def post_setting(self, client, data, *, token="a" * 40):
        return client.post(
            "/admin/settings/integrations/federation-public-directory",
            data=data,
            headers={"X-CSRF-Token": token},
        )

    def test_public_directory_is_admin_only_and_does_not_open_write_endpoints(self):
        integrations = SettingsStore(self.root).settings()["integrations"]
        self.assertEqual(
            DEFAULT_FEDERATION_PUBLIC_DIRECTORY_ENABLED,
            integrations["federation_public_directory_enabled"],
        )
        page = self.admin.get("/admin/settings/integrations")
        self.assertEqual(403, self.user.get("/admin/settings/integrations").status_code)
        self.assertIn(
            'name="federation_public_directory_enabled"',
            page.get_data(as_text=True),
        )
        testing = app.testing
        app.testing = False  # Federation auth has a deliberate no-token test-mode bypass.
        try:
            self.assertEqual(401, app.test_client().get(
                "/federation/v1/discovery/peers",
            ).status_code)

            path = "/admin/settings/integrations/federation-public-directory"
            self.assertEqual(403, self.user.post(path, data={
                "federation_public_directory_enabled": "1",
            }).status_code)
            self.assertEqual(403, self.admin.post(path, data={
                "federation_public_directory_enabled": "1",
            }).status_code)

            saved = self.post_setting(
                self.admin, {"federation_public_directory_enabled": "1"},
            )
            self.assertEqual(302, saved.status_code)
            self.assertTrue(
                SettingsStore(self.root).settings()["integrations"][
                    "federation_public_directory_enabled"
                ],
            )
            public_client = app.test_client()
            response = public_client.get("/federation/v1/discovery/peers")
            self.assertEqual(200, response.status_code)
            self.assertEqual([], response.get_json()["peers"])
            self.assertEqual(
                401,
                public_client.post("/federation/v1/discovery/register", json={}).status_code,
            )

            disabled = self.post_setting(
                self.admin, {"federation_public_directory_enabled": "0"},
            )
            self.assertEqual(302, disabled.status_code)
            self.assertEqual(
                401,
                public_client.get("/federation/v1/discovery/peers").status_code,
            )
        finally:
            app.testing = testing
        with app.app_context():
            row = database.get_db().execute(
                "SELECT target_id,detail FROM security_event WHERE action=? "
                "ORDER BY rowid DESC LIMIT 1",
                ("federation_public_directory_settings_updated",),
            ).fetchone()
        self.assertEqual(
            "integrations.federation_public_directory_enabled", row["target_id"],
        )
        self.assertFalse(json.loads(row["detail"])["enabled"])

    def test_legacy_optin_migrates_once_invalid_values_reject_and_reset_is_safe(self):
        store = SettingsStore(self.root)
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PUBLIC_DIRECTORY": "yes"}):
            self.assertTrue(
                store.settings()["integrations"]["federation_public_directory_enabled"],
            )
            store.save({"integrations": {"federation_autoscan_seconds": 10800}}, "federation-admin")

        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_PUBLIC_DIRECTORY": "0"}):
            self.assertTrue(
                store.settings()["integrations"]["federation_public_directory_enabled"],
            )
            for invalid in ("", "2", "true", "off"):
                with self.subTest(invalid=invalid):
                    self.post_setting(
                        self.admin,
                        {"federation_public_directory_enabled": invalid},
                    )
                    self.assertTrue(
                        store.settings()["integrations"][
                            "federation_public_directory_enabled"
                        ],
                    )
            reset = self.post_setting(self.admin, {"reset": "1"})

        self.assertEqual(302, reset.status_code)
        integrations = SettingsStore(self.root).settings()["integrations"]
        self.assertFalse(integrations["federation_public_directory_enabled"])
        self.assertEqual(10800, integrations["federation_autoscan_seconds"])
        with app.app_context():
            row = database.get_db().execute(
                "SELECT target_id,detail FROM security_event WHERE action=? "
                "ORDER BY rowid DESC LIMIT 1",
                ("federation_public_directory_settings_updated",),
            ).fetchone()
        self.assertEqual(
            "integrations.federation_public_directory_enabled", row["target_id"],
        )
        self.assertTrue(json.loads(row["detail"])["reset_to_defaults"])
        for invalid in (0, 1, "true", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                store.save(
                    {"integrations": {"federation_public_directory_enabled": invalid}},
                    "federation-admin",
                )

    def test_public_profile_url_is_migrated_validated_and_used_without_network_access(self):
        page = self.admin.get("/admin/settings/integrations")
        self.assertEqual(200, page.status_code)
        self.assertEqual(
            1,
            page.get_data(as_text=True).count('id="federation-public-url"'),
        )
        store = SettingsStore(self.root)
        with patch.dict(
            os.environ,
            {"SIMPLEOFFICE_FEDERATION_PUBLIC_URL": "office.example/simpleoffice"},
        ):
            self.assertEqual(
                "https://office.example/simpleoffice",
                store.settings()["integrations"]["federation_public_url"],
            )
            store.save({"integrations": {"federation_autoscan_seconds": 10800}}, "federation-admin")

        with patch.dict(
            os.environ,
            {"SIMPLEOFFICE_FEDERATION_PUBLIC_URL": "https://changed.example"},
        ):
            self.assertEqual(
                "https://office.example/simpleoffice",
                store.settings()["integrations"]["federation_public_url"],
            )
        with patch(
            "app.federation_local_profile.FederationIdentity.public_identity",
            return_value={"public_key": "public-key", "fingerprint": "sha256:test"},
        ):
            profile = local_profile(self.root, "http://192.168.40.2:8080")
        self.assertEqual("https://office.example/simpleoffice", profile["base_url"])

        path = "/admin/settings/integrations/federation-public-url"
        self.assertEqual(
            403,
            self.user.post(
                path, data={"federation_public_url": "https://new.example"},
            ).status_code,
        )
        self.assertEqual(
            403,
            self.admin.post(
                path, data={"federation_public_url": "https://new.example"},
            ).status_code,
        )
        saved = self.admin.post(
            path,
            data={"federation_public_url": "https://new.example/proxy"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, saved.status_code)
        self.assertEqual(
            "https://new.example/proxy",
            store.settings()["integrations"]["federation_public_url"],
        )
        for invalid in (
            "https://user:password@example.test",
            "https://example.test/?token=value",
            "https://example.test/#fragment",
            "https://example.test/a/../admin",
            "x" * 513,
        ):
            with self.subTest(invalid=invalid):
                self.admin.post(
                    path,
                    data={"federation_public_url": invalid},
                    headers={"X-CSRF-Token": "a" * 40},
                )
                self.assertEqual(
                    "https://new.example/proxy",
                    store.settings()["integrations"]["federation_public_url"],
                )

        cleared = self.admin.post(
            path,
            data={"federation_public_url": ""},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, cleared.status_code)
        self.assertEqual("", store.settings()["integrations"]["federation_public_url"])
        with patch(
            "app.federation_local_profile.FederationIdentity.public_identity",
            return_value={"public_key": "public-key", "fingerprint": "sha256:test"},
        ):
            fallback_profile = local_profile(self.root, "http://192.168.40.2:8080")
        self.assertEqual("http://192.168.40.2:8080", fallback_profile["base_url"])
        self.assertEqual(10800, store.settings()["integrations"]["federation_autoscan_seconds"])

        configured = self.admin.post(
            path,
            data={"federation_public_url": "https://reset.example"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, configured.status_code)
        reset = self.admin.post(
            path,
            data={"reset": "1"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, reset.status_code)
        self.assertEqual("", store.settings()["integrations"]["federation_public_url"])

        with app.app_context():
            row = database.get_db().execute(
                "SELECT target_id,detail FROM security_event WHERE action=? "
                "ORDER BY rowid DESC LIMIT 1",
                ("federation_public_url_updated",),
            ).fetchone()
        self.assertEqual("integrations.federation_public_url", row["target_id"])
        self.assertFalse(json.loads(row["detail"])["configured"])
        self.assertTrue(json.loads(row["detail"])["reset_to_defaults"])

    def test_profile_country_is_admin_managed_persistent_and_used_in_public_profiles(self):
        page = self.admin.get("/admin/settings/integrations")
        self.assertIn('name="federation_profile_country"', page.get_data(as_text=True))
        store = SettingsStore(self.root)
        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_COUNTRY": "de"}):
            self.assertEqual(
                "DE", store.settings()["integrations"]["federation_profile_country"],
            )
            store.save({"integrations": {"federation_autoscan_seconds": 10800}}, "federation-admin")

        with patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_COUNTRY": "FR"}):
            self.assertEqual(
                "DE", store.settings()["integrations"]["federation_profile_country"],
            )
            with patch(
                "app.federation_local_profile.FederationIdentity.public_identity",
                return_value={"public_key": "public-key", "fingerprint": "sha256:test"},
            ):
                self.assertEqual(
                    "DE", local_profile(self.root, "https://profile.example")["country"],
                )

        path = "/admin/settings/integrations/federation-profile-country"
        self.assertEqual(403, self.user.post(
            path, data={"federation_profile_country": "FR"},
        ).status_code)
        self.assertEqual(403, self.admin.post(
            path, data={"federation_profile_country": "FR"},
        ).status_code)
        saved = self.admin.post(
            path,
            data={"federation_profile_country": "fr"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, saved.status_code)
        self.assertEqual(
            "FR", store.settings()["integrations"]["federation_profile_country"],
        )
        with patch(
            "app.federation_local_profile.FederationIdentity.public_identity",
            return_value={"public_key": "public-key", "fingerprint": "sha256:test"},
        ):
            self.assertEqual("FR", local_profile(self.root, "https://profile.example")["country"])

        for invalid in ("U", "USA", "1A", "ÅA"):
            with self.subTest(invalid=invalid):
                self.admin.post(
                    path,
                    data={"federation_profile_country": invalid},
                    headers={"X-CSRF-Token": "a" * 40},
                )
                self.assertEqual(
                    "FR", store.settings()["integrations"]["federation_profile_country"],
                )

        reset = self.admin.post(
            path,
            data={"reset": "1"},
            headers={"X-CSRF-Token": "a" * 40},
        )
        self.assertEqual(302, reset.status_code)
        integrations = store.settings()["integrations"]
        self.assertEqual(DEFAULT_FEDERATION_PROFILE_COUNTRY, integrations["federation_profile_country"])
        self.assertEqual(10800, integrations["federation_autoscan_seconds"])
        with app.app_context():
            row = database.get_db().execute(
                "SELECT target_id,detail FROM security_event WHERE action=? "
                "ORDER BY rowid DESC LIMIT 1",
                ("federation_profile_country_updated",),
            ).fetchone()
        self.assertEqual("integrations.federation_profile_country", row["target_id"])
        self.assertTrue(json.loads(row["detail"])["reset_to_defaults"])
