"""Isolated HTTP clients and seed data for account API contract tests."""
import re
import tempfile
import unittest
from pathlib import Path

from app import app
from app.db import ensure_auth_database, get_db
from app.password_security import hash_password


class AccountApiFixture(unittest.TestCase):
    password = 'independent-account-test-password'
    invite_path = '/auth/account-requests/invite'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {k: app.config.get(k) for k in
                      ('DATABASE', 'DOCUMENT_ROOT', 'TESTING', 'TEST_CSRF_PROTECTION')}
        app.config.update(DATABASE=str(Path(self.temp.name) / 'auth.sqlite'),
                          DOCUMENT_ROOT=Path(self.temp.name) / 'documents',
                          TESTING=True, TEST_CSRF_PROTECTION=True)
        with app.app_context():
            ensure_auth_database()
            db = get_db()
            db.execute('INSERT INTO user(username,password,is_admin) VALUES (?,?,1)',
                       ('admin', hash_password(self.password)))
            db.commit()
        self.admin = self.login('admin')

    def tearDown(self):
        app.config.update(self.saved)
        self.temp.cleanup()

    def csrf(self, client, path='/auth/login'):
        response = client.get(path)
        match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match, response.data[:300])
        return match.group(1).decode()

    def login(self, username, password=None):
        client = app.test_client()
        client.post('/auth/login', data={'username': username, 'password': password or self.password,
                                        '_csrf_token': self.csrf(client)})
        return client

    def token(self, response):
        self.assertEqual(200, response.status_code)
        match = re.search(rb'/auth/account-requests/invite#token=([A-Za-z0-9_-]{43})', response.data)
        self.assertIsNotNone(match, response.data[:300])
        self.assertEqual('no-store', response.headers['Cache-Control'])
        self.assertEqual('no-referrer', response.headers['Referrer-Policy'])
        return match.group(1).decode()

    def invite(self, username='candidate', **extra):
        return self.admin.post('/auth/account-requests/admin/invite', data={
            'username': username, 'email': username + '@example.test',
            '_csrf_token': self.csrf(self.admin, '/auth/account-requests/admin/invite'), **extra})

    def redeem(self, token, client=None, password=None):
        client = client or app.test_client()
        return client.post(self.invite_path, data={
            'invitation_token': token, 'password': password or self.password,
            '_csrf_token': self.csrf(client, self.invite_path)})
