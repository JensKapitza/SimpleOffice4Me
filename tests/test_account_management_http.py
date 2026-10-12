"""Account lifecycle contracts exercised exclusively through the HTTP interface."""
import re
from app import app
from account_api_fixture import AccountApiFixture


class AccountManagementHttpTests(AccountApiFixture):
    def manage_path(self, username):
        response = self.admin.get('/auth/account-requests/admin')
        pattern = (r'<strong>' + username + r'</strong>.*?action="([^"]+/invitations/\d+)"')
        match = re.search(pattern, response.get_data(as_text=True), re.S)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_reissue_invalidates_old_secret_and_revoke_keeps_account_disabled(self):
        old = self.token(self.invite())
        path = self.manage_path('candidate')
        fresh = self.token(self.admin.post(path, data={'action': 'reissue',
            '_csrf_token': self.csrf(self.admin, '/auth/account-requests/admin')}))
        self.assertNotEqual(old, fresh)
        self.assertEqual(404, self.redeem(old).status_code)
        self.assertEqual(302, self.admin.post(path, data={'action': 'revoke',
            '_csrf_token': self.csrf(self.admin, '/auth/account-requests/admin')}).status_code)
        self.assertEqual(404, self.redeem(fresh).status_code)
        self.assertEqual('/auth/login', self.login('candidate').get('/auth/profile').headers['Location'])

    def test_accepted_invitation_cannot_be_reissued_and_normal_user_cannot_manage(self):
        token = self.token(self.invite())
        path = self.manage_path('candidate')
        self.assertEqual(302, self.redeem(token).status_code)
        member = self.login('candidate')
        self.assertEqual(403, member.post(path, data={'action': 'reissue', 'is_admin': '1',
            '_csrf_token': self.csrf(member)}).status_code)
        self.assertEqual(404, self.admin.post(path, data={'action': 'reissue',
            '_csrf_token': self.csrf(self.admin, '/auth/account-requests/admin')}).status_code)

    def test_parent_association_is_visible_but_does_not_grant_admin_rights(self):
        page = self.admin.get('/auth/account-requests/admin/invite').get_data(as_text=True)
        parent = re.search(r'<option value="(\d+)">admin</option>', page).group(1)
        token = self.token(self.invite(parent_user_id=parent))
        self.assertEqual(302, self.redeem(token).status_code)
        self.assertIn('Übergeordnet: admin', self.admin.get('/admin/users').get_data(as_text=True))
        self.assertEqual(403, self.login('candidate').get('/admin/users').status_code)
        self.assertEqual(404, self.invite('invalidparent', parent_user_id='999999').status_code)
        self.assertEqual(400, self.invite('invalidparent', parent_user_id='../../admin').status_code)

    def test_unowned_mail_account_cannot_send_and_invitation_still_works(self):
        response = self.invite(send_email='1', mail_account_id='foreign-mail-account')
        token = self.token(response)
        self.assertIn('E-Mail nicht bestätigt', response.get_data(as_text=True))
        self.assertEqual(302, self.redeem(token).status_code)

    def test_pending_request_cannot_login_and_case_duplicate_is_not_disclosed(self):
        guest = app.test_client()
        form = {'username': 'candidate', 'email': 'candidate@example.test',
                'password': self.password, 'is_admin': '1', 'parent_user_id': '1',
                '_csrf_token': self.csrf(guest, '/auth/account-requests/new')}
        first = guest.post('/auth/account-requests/new', data=form)
        second = guest.post('/auth/account-requests/new', data={**form, 'username': 'CANDIDATE'})
        self.assertEqual(302, first.status_code)
        self.assertEqual(first.headers['Location'], second.headers['Location'])
        self.assertEqual('/auth/login', self.login('candidate').get('/auth/profile').headers['Location'])
        pending = self.admin.get('/auth/account-requests/admin').get_data(as_text=True)
        self.assertEqual(1, pending.count('<h2 class="h5">candidate</h2>'))
        self.assertNotIn('CANDIDATE', pending)
        self.assertIn('warten auf Freigabe', pending)
        path = re.search(r'action="([^"]+/admin/\d+)"', pending).group(1)
        self.assertEqual(302, self.admin.post(path, data={'decision': 'approve',
            '_csrf_token': self.csrf(self.admin, '/auth/account-requests/admin')}).status_code)
        member = self.login('candidate')
        self.assertEqual('/documents/settings', member.get('/auth/profile').headers['Location'])
        self.assertEqual(403, member.get('/admin/users').status_code)
        self.assertNotIn('<h2 class="h5">candidate</h2>', self.admin.get('/auth/account-requests/admin').get_data(as_text=True))
        audit = self.admin.get('/admin/logs/export?format=csv&action=account_request_decided')
        self.assertEqual(200, audit.status_code)
        self.assertIn('account_request_decided', audit.get_data(as_text=True))
        self.assertNotIn(self.password, audit.get_data(as_text=True))

    def test_rejected_request_never_creates_login_and_cannot_be_approved_later(self):
        guest = app.test_client()
        guest.post('/auth/account-requests/new', data={'username': 'rejected',
            'email': 'rejected@example.test', 'password': self.password,
            '_csrf_token': self.csrf(guest, '/auth/account-requests/new')})
        page = self.admin.get('/auth/account-requests/admin').get_data(as_text=True)
        path = re.search(r'action="([^"]+/admin/\d+)"', page).group(1)
        data = {'decision': 'reject', '_csrf_token': self.csrf(self.admin, '/auth/account-requests/admin')}
        self.assertEqual(302, self.admin.post(path, data=data).status_code)
        self.assertEqual(404, self.admin.post(path, data={**data, 'decision': 'approve'}).status_code)
        self.assertEqual('/auth/login', self.login('rejected').get('/auth/profile').headers['Location'])

    def test_invalid_settings_are_rejected_without_disabling_requests(self):
        path = '/auth/account-requests/admin/registration'
        for values in ({'invitation_minutes': '0'}, {'invitation_minutes': '10081'},
                       {'requests_per_hour': '101'}, {'requests_per_hour': 'text'}):
            self.assertEqual(400, self.admin.post(path, data={**values,
                '_csrf_token': self.csrf(self.admin, '/auth/account-requests/admin')}).status_code)
        self.assertEqual(200, app.test_client().get('/auth/account-requests/new').status_code)

    def test_concurrent_redemption_has_exactly_one_success(self):
        from concurrent.futures import ThreadPoolExecutor
        token = self.token(self.invite())
        clients = [app.test_client(), app.test_client()]
        forms = [{'invitation_token': token, 'password': self.password,
                  '_csrf_token': self.csrf(client, self.invite_path)} for client in clients]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(client.post, self.invite_path, data=form)
                       for client, form in zip(clients, forms)]
            self.assertEqual([302, 404], sorted(f.result().status_code for f in futures))
        self.assertEqual('/documents/settings', self.login('candidate').get('/auth/profile').headers['Location'])

    def test_invitation_secret_is_absent_from_audit_export(self):
        token = self.token(self.invite())
        self.assertEqual(302, self.redeem(token).status_code)
        response = self.admin.get('/admin/logs/export?format=csv')
        self.assertEqual(200, response.status_code)
        body = response.get_data(as_text=True)
        self.assertIn('account_invitation_created', body)
        self.assertIn('account_invitation_accepted', body)
        self.assertNotIn(token, body)
        self.assertNotIn(self.password, body)
