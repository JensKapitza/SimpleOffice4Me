"""Public request API quotas also count invalid submissions, independently per client."""
from types import SimpleNamespace
from unittest.mock import patch

from app import app
from account_api_fixture import AccountApiFixture


class AccountRequestThrottleTests(AccountApiFixture):
    def test_invalid_requests_consume_five_slots_and_sixth_is_rejected(self):
        guest = app.test_client()
        data = {'username': '!', 'email': 'invalid', 'password': 'weak',
                '_csrf_token': self.csrf(guest)}
        with patch('app.account_requests.time', SimpleNamespace(time=lambda: 3600)):
            for _ in range(5):
                self.assertEqual(400, guest.post('/auth/account-requests/new', data=data,
                    environ_overrides={'REMOTE_ADDR': '192.0.2.10'}).status_code)
            self.assertEqual(429, guest.post('/auth/account-requests/new', data=data,
                environ_overrides={'REMOTE_ADDR': '192.0.2.10'}).status_code)
            self.assertEqual(400, guest.post('/auth/account-requests/new', data=data,
                environ_overrides={'REMOTE_ADDR': '192.0.2.11'}).status_code)
        with patch('app.account_requests.time', SimpleNamespace(time=lambda: 7200)):
            self.assertEqual(400, guest.post('/auth/account-requests/new', data=data,
                environ_overrides={'REMOTE_ADDR': '192.0.2.10'}).status_code)

    def test_disabled_registration_denies_new_requests(self):
        self.assertEqual(302, self.admin.post('/auth/account-requests/admin/registration', data={
            '_csrf_token': self.csrf(self.admin, '/auth/account-requests/admin')}).status_code)
        guest = app.test_client()
        self.assertEqual(403, guest.get('/auth/account-requests/new').status_code)
        self.assertEqual(403, guest.post('/auth/account-requests/new', data={
            'username': 'candidate', 'email': 'candidate@example.test', 'password': self.password,
            '_csrf_token': self.csrf(guest)}).status_code)
