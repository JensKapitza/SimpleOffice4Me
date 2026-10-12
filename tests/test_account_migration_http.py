"""Legacy account/settings data survives the additive onboarding migration."""
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database
from app.password_security import hash_password


class AccountMigrationHttpTests(unittest.TestCase):
    def test_existing_admin_login_and_disabled_registration_survive_repeated_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / 'legacy.sqlite'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE user(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL)')
                db.execute('INSERT INTO user VALUES(17,?,?)',
                           ('existing-owner', hash_password('test-only-existing-owner-passphrase')))
                db.execute('CREATE TABLE registration_settings(id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL)')
                db.execute('INSERT INTO registration_settings VALUES(1,0)')
            previous = app.config.copy()
            try:
                app.config.update(DATABASE=str(database), DOCUMENT_ROOT=Path(temp) / 'documents',
                                  TESTING=True, TEST_CSRF_PROTECTION=True)
                with app.app_context():
                    ensure_auth_database()
                    ensure_auth_database()
                client = app.test_client()
                page = client.get('/auth/login')
                token = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data).group(1).decode()
                self.assertEqual(302, client.post('/auth/login', data={
                    'username': 'existing-owner', 'password': 'test-only-existing-owner-passphrase',
                    '_csrf_token': token}).status_code)
                self.assertEqual(200, client.get('/admin/users').status_code)
                self.assertEqual(403, app.test_client().get('/auth/account-requests/new').status_code)
                settings = client.get('/auth/account-requests/admin')
                self.assertEqual(200, settings.status_code)
                self.assertIn('value="1440"', settings.get_data(as_text=True))
                self.assertIn('value="5"', settings.get_data(as_text=True))
                self.assertNotIn('name="enabled" value="1" checked', settings.get_data(as_text=True))
            finally:
                app.config.clear()
                app.config.update(previous)
