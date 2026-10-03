"""Real DAV requests must meter their app account independently of cookies."""
import base64
import sqlite3
import unittest
from unittest.mock import patch

from app import app
from app.calendar_collections import CalendarCollections
from app.contact_store import ContactStore
from app.db import get_db
from app.license_metering import LicenseStore
from app.webdav import activate
from app.virtual_filesystem import VirtualFileSystem
import test_first_run_setup as _fixture


class DavLicenseMeteringTests(unittest.TestCase):
    setUp = _fixture.FirstRunSetupTest.setUp
    tearDown = _fixture.FirstRunSetupTest.tearDown

    def provision(self, username='jens'):
        with app.app_context():
            password = activate(username, username, label='DAV test', scope='write')
            CalendarCollections(self.root).activate(username, password, username)
            ContactStore(self.root).activate_carddav(username, password, username)
            VirtualFileSystem(self.root, {"test-admin"}).set_grants(".", {username: "read"}, "test-admin")
        token = base64.b64encode(f'{username}:{password}'.encode()).decode()
        return {'Authorization': f'Basic {token}', 'Depth': '0'}

    def request_all(self, client, headers, username='jens'):
        for prefix, suffix in (('webdav', 'files'), ('caldav', 'calendars'), ('carddav', 'addressbooks')):
            response = client.open(f'/{prefix}/{suffix}/{username}/', method='PROPFIND', headers=headers, base_url=self.base_url)
            self.assertEqual(207, response.status_code, (prefix, response.get_data(as_text=True)[:200]))

    def usage_rows(self):
        with LicenseStore(self.root)._db() as db:
            return [dict(row) for row in db.execute('SELECT user_id,feature,request_count FROM license_usage ORDER BY feature,user_id')]

    def test_dav_without_browser_session_counts_each_successful_request_once(self):
        headers = self.provision()
        self.request_all(app.test_client(), headers)
        with app.app_context():
            user_id = get_db().execute("SELECT id FROM user WHERE username='jens'").fetchone()['id']
        self.assertEqual([{'user_id': user_id, 'feature': feature, 'request_count': 1}
                          for feature in ('calendar', 'contacts', 'webdav')], self.usage_rows())

    def test_webdav_collection_with_and_without_slash_uses_same_authenticated_route(self):
        headers = self.provision()
        client = app.test_client()
        for suffix in ('', '/'):
            response = client.open('/webdav/files/jens' + suffix, method='PROPFIND', headers=headers, base_url=self.base_url)
            self.assertEqual(207, response.status_code)
            self.assertNotIn('Location', response.headers)
        self.assertEqual([2], [row['request_count'] for row in self.usage_rows()])

    def test_app_account_takes_precedence_over_different_browser_cookie(self):
        app.test_client().post('/auth/register', data={'username': 'dav-user', 'password': 'other-browser-password'}, base_url=self.base_url)
        self.request_all(self.client, self.provision('dav-user'), 'dav-user')
        with app.app_context():
            user_id = get_db().execute("SELECT id FROM user WHERE username='dav-user'").fetchone()['id']
        self.assertEqual({user_id}, {row['user_id'] for row in self.usage_rows()})
        self.assertEqual([1, 1, 1], [row['request_count'] for row in self.usage_rows()])

    def test_rejected_credentials_and_missing_resources_are_not_counted(self):
        headers = self.provision()
        client = app.test_client()
        for prefix, suffix in (('webdav', 'files'), ('caldav', 'calendars'), ('carddav', 'addressbooks')):
            path = f'/{prefix}/{suffix}/jens/'
            self.assertEqual(401, client.open(path, method='PROPFIND', base_url=self.base_url).status_code)
            bad = {'Authorization': 'Basic ' + base64.b64encode(b'jens:wrong').decode()}
            self.assertEqual(401, client.open(path, method='PROPFIND', headers=bad, base_url=self.base_url).status_code)
            extension = {'webdav': '.txt', 'caldav': '.ics', 'carddav': '.vcf'}[prefix]
            self.assertEqual(404, client.get(path + ('default/' if prefix != 'webdav' else '') + 'missing' + extension, headers=headers, base_url=self.base_url).status_code)
        self.assertEqual([], self.usage_rows())

    def test_unmapped_app_account_is_not_attributed_to_browser(self):
        username = "unknown' OR 1=1 --"
        headers = self.provision(username)
        for path in ('/webdav/', '/caldav/', '/carddav/'):
            response = self.client.open(path, method='OPTIONS', headers=headers, base_url=self.base_url)
            self.assertIn(response.status_code, (200, 204))
        self.assertEqual([], self.usage_rows())

    def test_verified_identity_is_reset_between_requests_in_outer_app_context(self):
        headers = self.provision()
        client = app.test_client()
        with app.app_context():
            self.assertEqual(207, client.open('/caldav/calendars/jens/', method='PROPFIND', headers=headers, base_url=self.base_url).status_code)
            self.assertEqual(307, client.get('/.well-known/caldav', base_url=self.base_url).status_code)
        self.assertEqual([1], [row['request_count'] for row in self.usage_rows()])

    def test_meter_database_failure_does_not_fail_authenticated_dav_request(self):
        headers = self.provision()
        with patch('app.license_routes.get_db', side_effect=sqlite3.OperationalError('meter database unavailable')), patch.object(app.logger, 'exception') as log:
            self.assertEqual(207, app.test_client().open('/caldav/calendars/jens/', method='PROPFIND', headers=headers, base_url=self.base_url).status_code)
        log.assert_called_once_with('license usage metering failed')
        self.assertEqual([], self.usage_rows())
