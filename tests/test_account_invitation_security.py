"""Public invitation API: authorization, activation, expiry and one-use secrets."""
from types import SimpleNamespace
from unittest.mock import patch

from app import app
from account_api_fixture import AccountApiFixture


class InvitationSecurityTests(AccountApiFixture):
    def test_invitation_requires_admin_and_csrf(self):
        guest = app.test_client()
        self.assertEqual(403, guest.get('/auth/account-requests/admin/invite').status_code)
        self.assertEqual(403, guest.post('/auth/account-requests/admin/invite', data={
            'username': 'blocked', 'email': 'blocked@example.test',
            '_csrf_token': self.csrf(guest)}).status_code)
        self.assertEqual(403, self.admin.post('/auth/account-requests/admin/invite', data={
            'username': 'blocked', 'email': 'blocked@example.test'}).status_code)

    def test_explicit_admin_selection_grants_admin_only_after_activation(self):
        token = self.token(self.invite('privileged', is_admin='1'))
        self.assertEqual('/auth/login', self.login('privileged').get('/auth/profile').headers['Location'])
        self.assertEqual(302, self.redeem(token).status_code)
        self.assertEqual(200, self.login('privileged').get('/auth/account-requests/admin').status_code)

    def test_configured_one_minute_invitation_expires(self):
        self.assertEqual(302, self.admin.post('/auth/account-requests/admin/registration', data={
            'enabled': '1', 'invitation_minutes': '1', 'requests_per_hour': '5',
            '_csrf_token': self.csrf(self.admin, '/auth/account-requests/admin')}).status_code)
        with patch('app.account_requests.time', SimpleNamespace(time=lambda: 1000)):
            token = self.token(self.invite('expired'))
        with patch('app.account_requests.time', SimpleNamespace(time=lambda: 1060)):
            self.assertEqual(404, self.redeem(token).status_code)
        self.assertEqual('/auth/login', self.login('expired').get('/auth/profile').headers['Location'])

    def test_invite_rejects_case_variant_without_changing_existing_login(self):
        self.assertEqual(409, self.invite('ADMIN').status_code)
        self.assertEqual(200, self.login('admin').get('/auth/account-requests/admin').status_code)

    def test_unknown_secret_cannot_activate_an_account(self):
        self.assertEqual(404, self.redeem('x' * 43).status_code)
        self.assertEqual('/auth/login', self.login('unknown').get('/auth/profile').headers['Location'])

    def test_invitation_csrf_single_use_and_password_cannot_be_reset_by_replay(self):
        token = self.token(self.invite('newmember'))
        guest = app.test_client()
        self.assertEqual(403, guest.post(self.invite_path, data={
            'invitation_token': token, 'password': self.password}).status_code)
        self.assertEqual(302, self.redeem(token).status_code)
        self.assertEqual(404, self.redeem(token, password='test-only-replacement-password-123').status_code)
        member = self.login('newmember')
        self.assertEqual('/documents/settings', member.get('/auth/profile').headers['Location'])
        self.assertEqual(403, member.get('/auth/account-requests/admin').status_code)
        self.assertEqual('/auth/login', self.login('newmember', 'test-only-replacement-password-123').get('/auth/profile').headers['Location'])

    def test_short_password_preserves_invitation(self):
        token = self.token(self.invite())
        self.assertEqual(400, self.redeem(token, password='short').status_code)
        self.assertEqual(302, self.redeem(token).status_code)
