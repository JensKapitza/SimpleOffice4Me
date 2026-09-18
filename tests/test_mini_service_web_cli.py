import io
import getpass
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, MagicMock
from email.message import Message
from urllib.error import HTTPError
from tools.mini_services_web import MAX_RESPONSE, NoRedirect

from werkzeug.serving import WSGIRequestHandler, make_server
from app import app
from app.db import ensure_auth_database, get_db
from tools.mini_services import main
from tools.mini_services_web import ClientError, WebClient, read_password, validate_url


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


class WebCliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        saved = dict(app.config)
        def restore_config():
            app.config.clear()
            app.config.update(saved)
        self.addCleanup(restore_config)
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=False,
                          DATABASE=str(root / 'users.sqlite'), DOCUMENT_ROOT=str(root / 'docs'))
        env = patch.dict(os.environ, {'SIMPLEOFFICE_MINI_SERVICES_CONFIG': str(root / 'mini.json')})
        env.start(); self.addCleanup(env.stop)
        with app.app_context():
            ensure_auth_database()
        app.test_client().post('/auth/register', data={'username': 'admin', 'password': 'test-password-long'})
        app.config['TEST_CSRF_PROTECTION'] = True
        self.server = make_server('127.0.0.1', 0, app, request_handler=QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.client = WebClient(f'http://127.0.0.1:{self.server.server_port}')

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def login(self):
        self.client.login('admin', 'test-password-long')

    def test_login_rotates_csrf_and_audio_actions_use_existing_manager(self):
        self.client.load_token()
        token = self.client.token
        self.login()
        self.assertNotEqual(token, self.client.token)
        with patch('app.audio_streamer_admin.manager') as manager:
            manager.configured_start.return_value = {'state': 'running'}
            result, code = self.client.command('audio-sender', 'start')
            self.assertEqual(({'state': 'running'}, 0), (result, code))
            manager.configured_start.assert_called_once_with('sender', {})
            self.assertEqual(0, self.client.command('audio-sender', 'stop')[1])
            manager.stop_sender.assert_called_once_with()
        self.client.token = token
        with patch('app.audio_streamer_admin.manager') as manager:
            with self.assertRaises(ClientError):
                self.client.command('audio-sender', 'stop')
            manager.stop_sender.assert_not_called()

    def test_login_failure_non_admin_and_expired_session_never_execute_action(self):
        with self.assertRaisesRegex(ClientError, 'Anmeldung fehlgeschlagen'):
            self.client.login('admin', 'wrong-password')
        self.login()
        with app.app_context():
            get_db().execute("UPDATE user SET is_admin=0 WHERE username='admin'")
            get_db().commit()
        with patch('app.audio_streamer_admin.manager') as manager:
            with self.assertRaises(ClientError):
                self.client.command('audio-sender', 'stop')
            manager.stop_sender.assert_not_called()
        anonymous = WebClient(self.client.base)
        with self.assertRaises(ClientError):
            anonymous.command('http-boot', 'status')

    def test_boot_lifecycle_and_scan_with_real_routes_and_storage(self):
        self.login()
        stopped, code = self.client.command('http-boot', 'stop')
        self.assertEqual(('stopped', 0), (stopped['state'], code))
        self.assertEqual(0, self.client.command('http-boot', 'stop')[1])
        self.assertEqual(3, self.client.command('http-boot', 'status')[1])
        # With no boot profile the serving gate starts, but readiness remains waiting.
        started, code = self.client.command('http-boot', 'start')
        self.assertEqual(('waiting', 3), (started['state'], code))
        self.assertEqual(3, self.client.command('http-boot', 'restart')[1])
        scanned, code = self.client.command('http-boot', 'scan')
        self.assertEqual(('completed', 0), (scanned['state'], code))
        self.assertEqual([], scanned['targets'])

    def test_receiver_restart_output_lifecycle_and_network_scan_use_existing_api(self):
        self.login()
        with patch('app.audio_streamer_admin.manager') as manager:
            manager.configured_start.return_value = {'state': 'running'}
            self.assertEqual(0, self.client.command('audio-receiver', 'restart')[1])
            manager.configured_start.assert_called_once_with('receiver', restart=True)
        with patch('app.audio_output_admin.worker') as worker:
            worker.status.return_value = {'state': 'running'}
            self.assertEqual(0, self.client.command('audio-output', 'restart')[1])
            worker.stop.assert_called_once_with()
            worker.start.assert_called_once_with()
        with patch('app.network_system_status.network_interfaces', return_value=[{'name': 'eth0'}]):
            result, code = self.client.command('dns', 'scan')
            self.assertEqual(0, code)
            self.assertEqual([{'name': 'eth0'}], result['targets'])

    def test_audio_scan_failure_reports_safe_error(self):
        self.login()
        with patch('app.audio_output_discovery.discover_receiver_outputs', side_effect=OSError('secret-device')):
            with self.assertRaises(ClientError) as error:
                self.client.command('audio-receiver', 'scan')
        self.assertIn('HTTP 503', str(error.exception))
        self.assertNotIn('secret-device', str(error.exception))


class WebCliSafetyTests(unittest.TestCase):
    def test_url_validation_preserves_ipv6_and_requires_tls_outside_loopback(self):
        for value in ('http://127.0.0.1:8080/', 'http://[::1]:8080', 'https://office.example.test'):
            self.assertEqual(value.rstrip('/'), validate_url(value))
        for value in ('http://localhost:8080', 'http://192.168.1.2', 'https://user:secret@host',
                      'https://host/?token=secret', 'https://host/path', 'https://host/#secret',
                      'https://host:0', 'https://host:99999', 'https://host\n', 'file:///tmp/test'):
            with self.subTest(value=value), self.assertRaises(ClientError):
                validate_url(value)

    def test_password_is_explicit_and_never_a_cli_argument(self):
        with patch('sys.stdin', io.StringIO('test-password\n')):
            self.assertEqual('test-password', read_password(True))
            with self.assertRaises(ClientError):
                read_password()
        with patch('sys.stdin.isatty', return_value=True), patch('getpass.getpass', side_effect=getpass.GetPassWarning):
            with self.assertRaises(ClientError):
                read_password()
        for args in (['scan'], ['stop', '--username', 'admin'],
                     ['stop', '--service', 'dns', '--web-url', 'https://remote.test'],
                     ['stop', '--service', 'http-boot', '--username', 'admin', '--config', 'other.json'],
                     ['stop', '--service', 'http-boot', '--username', 'admin', '--wait', '5'],
                     ['stop', '--service', 'audio-output'],
                     ['status', '--service', 'http-boot', '--username', 'admin', '--operation', 'id']):
            with patch('sys.stderr', io.StringIO()), patch('tools.mini_services.Worker') as worker:
                with self.assertRaises(SystemExit) as error:
                    main(args)
                self.assertEqual(2, error.exception.code)
                worker.assert_not_called()

    def test_cli_routes_only_to_web_owner_and_prints_json(self):
        output = io.StringIO()
        with patch('tools.mini_services.web_command', return_value=({'state': 'stopped'}, 0)) as command, patch('tools.mini_services.Worker') as worker, patch('tools.service_control.stop') as stop, redirect_stdout(output):
            main(['stop', '--service', 'audio-output', '--username', 'admin'])
        self.assertEqual('stopped', json.loads(output.getvalue())['state'])
        command.assert_called_once()
        worker.assert_not_called()
        stop.assert_not_called()

    def test_redirects_and_unexpected_or_oversized_responses_are_rejected(self):
        client = WebClient('http://127.0.0.1:8080')
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://outside.test'))
        headers = Message()
        headers['Content-Type'] = 'application/json'
        for content_type, body in [('text/html', b'<html>secret</html>'),
                                   ('application/json', b'[]'),
                                   ('application/json', b'{invalid secret'),
                                   ('application/json', b'x' * (MAX_RESPONSE + 1))]:
            headers.replace_header('Content-Type', content_type)
            response = MagicMock()
            response.__enter__.return_value = response
            response.headers = headers
            response.read.return_value = body
            with patch.object(client.opener, 'open', return_value=response):
                with self.assertRaises(ClientError) as error:
                    client.command('http-boot', 'status')
                self.assertNotIn('secret', str(error.exception))
                response.read.assert_called_once_with(MAX_RESPONSE + 1)
        for code in (302, 307, 429, 500):
            error = HTTPError(client.base, code, 'secret', Message(), io.BytesIO(b'secret'))
            with patch.object(client.opener, 'open', side_effect=error) as opened:
                with self.assertRaises(ClientError) as caught:
                    client.command('http-boot', 'stop')
                self.assertNotIn('secret', str(caught.exception))
                opened.assert_called_once()

    def test_response_errors_and_timeouts_are_not_retried_or_leaked(self):
        client = WebClient('http://127.0.0.1:8080')
        with patch.object(client.opener, 'open', side_effect=TimeoutError('secret-url')) as opened:
            with self.assertRaises(ClientError) as error:
                client.command('audio-output', 'restart')
            self.assertNotIn('secret-url', str(error.exception))
            opened.assert_called_once()
        for state, expected in [('failed', 1), ('waiting', 3), ('running', 0), ('stopped', 3)]:
            with patch.object(client, 'request', return_value={'state': state}):
                self.assertEqual(expected, client.command('audio-output', 'status')[1])


if __name__ == '__main__':
    unittest.main()
