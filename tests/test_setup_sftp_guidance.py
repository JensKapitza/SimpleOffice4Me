"""The setup UI and downloaded guide must respect the selected SFTP backend."""
import sys
import unittest
from unittest.mock import patch

from app.sftp_service import DEFAULTS
import test_first_run_setup as _fixture


class SetupSftpGuidanceTest(unittest.TestCase):
    setUp = _fixture.FirstRunSetupTest.setUp
    tearDown = _fixture.FirstRunSetupTest.tearDown

    def _status(self, mode, state='running', message='Dienst verfügbar.'):
        return {'settings': {**DEFAULTS, 'mode': mode, 'system_port': 2200},
                'state': state, 'health': {'ok': state == 'running', 'message': message}}

    def test_system_mode_uses_os_accounts_in_page_credentials_and_export(self):
        with patch('app.sftp_service.safe_status', return_value=self._status('system')), patch.dict(sys.modules, {'paramiko': None}):
            page = self.client.get('/documents/setup', base_url=self.base_url).get_data(as_text=True)
            credentials = self.client.post('/documents/setup/access', base_url=self.base_url).get_data(as_text=True)
            exported = self.client.get('/documents/setup/export.txt', base_url=self.base_url).get_data(as_text=True)
        for body in (page, credentials, exported):
            with self.subTest(body=body[:30]):
                self.assertIn('Betriebssystem', body)
                self.assertIn('2200', body)
                self.assertNotIn('sftp://jens@', body)
                self.assertNotIn('sshfs -p', body)
                self.assertNotIn('rsync -a', body)
                self.assertNotIn('keinen Shellzugang', body)
                self.assertNotIn('Optionale SFTP-Abhängigkeit installieren', body)
        self.assertIn('WebDAV-Passwort gilt nicht', credentials)

    def test_unavailable_service_explains_cause_in_page_and_export(self):
        message = 'SFTP-Einstellungen sind ungültig. Vollständige Einstellungen speichern.'
        with patch('app.sftp_service.safe_status', return_value=self._status('integrated', 'unavailable', message)):
            page = self.client.get('/documents/setup', base_url=self.base_url).get_data(as_text=True)
            exported = self.client.get('/documents/setup/export.txt', base_url=self.base_url).get_data(as_text=True)
        self.assertIn('Nicht verfügbar', page)
        self.assertNotIn('>Gestoppt<', page)
        for body in (page, exported):
            self.assertIn(message, body)

    def test_integrated_mode_keeps_app_credentials_and_dependency_instructions(self):
        with patch('app.sftp_service.safe_status', return_value=self._status('integrated', 'unavailable', 'Paramiko fehlt.')), patch.dict(sys.modules, {'paramiko': None}):
            page = self.client.get('/documents/setup', base_url=self.base_url).get_data(as_text=True)
            credentials = self.client.post('/documents/setup/access', base_url=self.base_url).get_data(as_text=True)
            exported = self.client.get('/documents/setup/export.txt', base_url=self.base_url).get_data(as_text=True)
        self.assertIn('Optionale SFTP-Abhängigkeit installieren', page)
        self.assertIn('benötigt kein OpenSSH', page)
        self.assertIn('sftp://jens@office.example:2222/', page)
        self.assertIn('WebDAV-Passwort gilt zugleich', credentials)
        self.assertIn('sshfs -p 2222 jens@office.example:/', exported)
        self.assertIn('erlaubt keine Shell', exported)
