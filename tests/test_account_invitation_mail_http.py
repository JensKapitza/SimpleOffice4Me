"""Invitation API mail side effects via an isolated synthetic SMTP transport."""
from email import policy
from email.parser import BytesParser
from unittest.mock import patch

from account_api_fixture import AccountApiFixture


class SyntheticMailbox:
    """SMTP boundary simulator; submitted messages are observable test output."""
    def __init__(self):
        self.messages = []

    def ehlo(self):
        return (250, b'OK')

    def has_extn(self, name):
        return name == 'starttls'

    def starttls(self, *, context):
        return (220, b'OK')

    def login(self, username, password):
        return (235, b'OK')

    def sendmail(self, sender, recipients, raw):
        self.messages.append((sender, recipients, raw))
        return {}

    def quit(self):
        return (221, b'OK')

    def close(self):
        pass


class InvitationMailHttpTests(AccountApiFixture):
    def configure_account(self, *, writable):
        path = '/documents/mail/accounts'
        response = self.admin.post(path, data={
            'id': 'owned-mail', 'host': 'imap.example.test', 'username': 'admin@example.test',
            'smtp_host': 'smtp.example.test', 'smtp_password': 'fake-test-only-smtp-password',
            'remember_password': '1', '_csrf_token': self.csrf(self.admin, '/documents/mail')})
        self.assertEqual(302, response.status_code)
        if writable:
            response = self.admin.post(path + '/owned-mail/readonly', data={
                'writable': '1', '_csrf_token': self.csrf(self.admin, '/documents/mail')})
            self.assertEqual(302, response.status_code)

    def test_explicit_owned_writable_mail_sends_usable_link_without_password(self):
        self.configure_account(writable=True)
        mailbox = SyntheticMailbox()
        with patch('smtplib.SMTP', return_value=mailbox):
            response = self.invite(send_email='1', mail_account_id='owned-mail')
        token = self.token(response)
        self.assertIn('Einladung versandt.', response.get_data(as_text=True))
        self.assertEqual(1, len(mailbox.messages))
        sender, recipients, raw = mailbox.messages[0]
        self.assertEqual('admin@example.test', sender)
        self.assertEqual(['candidate@example.test'], recipients)
        message = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual('Einladung zu SimpleOffice4Me', str(message['Subject']))
        text = message.get_body(preferencelist=('plain',)).get_content()
        self.assertIn('/auth/account-requests/invite#token=' + token, text)
        self.assertNotIn(self.password, text)
        self.assertNotIn('fake-test-only-smtp-password', text)
        self.assertEqual(302, self.redeem(token).status_code)
        self.assertEqual('/documents/settings', self.login('candidate').get('/auth/profile').headers['Location'])

    def test_read_only_owned_mail_cannot_send_and_invitation_remains_usable(self):
        self.configure_account(writable=False)
        mailbox = SyntheticMailbox()
        with patch('smtplib.SMTP', return_value=mailbox):
            response = self.invite(send_email='1', mail_account_id='owned-mail')
        self.assertIn('E-Mail nicht bestätigt', response.get_data(as_text=True))
        self.assertEqual([], mailbox.messages)
        self.assertEqual(302, self.redeem(self.token(response)).status_code)
