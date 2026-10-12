"""A standard invitation grants profile access, without implicit module/admin access."""
from account_api_fixture import AccountApiFixture


class InvitationDefaultAclTests(AccountApiFixture):
    def test_standard_invitation_is_least_privileged(self):
        token = self.token(self.invite())
        self.assertEqual(302, self.redeem(token).status_code)
        member = self.login('candidate')
        self.assertEqual('/documents/settings', member.get('/auth/profile').headers['Location'])
        for path in ('/auth/account-requests/admin', '/admin/users', '/library', '/documents/', '/inventory',
                     '/documents/mail/reader', '/documents/mail/index'):
            with self.subTest(path=path):
                self.assertEqual(403, member.get(path).status_code)
