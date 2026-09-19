import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, g
from app.network_boot_http import bp, federation_bp
from simpleoffice_mini_core import DEFAULT_CONFIG
from simpleoffice_mini_runtime import DhcpService
from simpleoffice_network_boot import DEFAULT_BOOT_SETTINGS, TftpService

SECRET = 'Authorization=private-token /private/secret-file'


class DiagnosticPrivacyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'mini.json'
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DOCUMENT_ROOT=directory.name)
        self.app.register_blueprint(bp)
        self.app.register_blueprint(federation_bp)
        self.app.before_request(lambda: setattr(g, 'request_id', 'request-test'))
        self.client = self.app.test_client()

    def check_logs(self, captured, event):
        self.assertNotIn(SECRET, '\n'.join(captured.output))
        record = captured.records[-1]
        self.assertIsNone(record.exc_info)
        row = json.loads(record.getMessage())
        self.assertEqual('http-boot', row['service'])
        self.assertEqual(event, row['event'])
        self.assertEqual('error', row['severity'])
        self.assertEqual('request-test', row['request_id'])
        self.assertIsInstance(row['timestamp'], float)
        self.assertIn('diagnostic', row)

    def test_boot_config_and_profile_failures_keep_safe_structured_context(self):
        for target, side_effect, event, status in (
            ('load_boot_settings', PermissionError(SECRET), 'settings_unavailable', 503),
            ('render_ipxe', ValueError(SECRET), 'profile_unavailable', 404),
        ):
            with self.subTest(event=event), patch('app.network_boot_http.load_boot_settings', return_value={'enabled': True}), patch('app.network_boot_http.' + target, side_effect=side_effect), self.assertLogs('simpleoffice.mini_services') as logs:
                response = self.client.get('/network-boot/ipxe')
            self.assertEqual(status, response.status_code)
            self.assertNotIn(SECRET, response.get_data(as_text=True))
            self.check_logs(logs, event)

    def test_file_permission_failure_is_safe_503_and_missing_file_stays_404(self):
        for error, expected in ((PermissionError(SECRET), 503), (FileNotFoundError(SECRET), 404)):
            with patch('app.network_boot_http.load_boot_settings', return_value={'enabled': True}), patch('app.network_boot_http._asset_response', side_effect=error):
                if expected == 503:
                    with self.assertLogs('simpleoffice.mini_services') as logs:
                        response = self.client.get('/network-boot/files/kernel')
                    self.check_logs(logs, 'storage_unavailable')
                    self.assertEqual('no-store', response.headers['Cache-Control'])
                else:
                    response = self.client.get('/network-boot/files/kernel')
            self.assertEqual(expected, response.status_code)
            self.assertNotIn(SECRET, response.get_data(as_text=True))

    def test_federation_validation_and_storage_errors_keep_authentication(self):
        with patch('app.network_boot_http._authorized', return_value=False), patch('app.network_boot_http.save_boot_settings') as save:
            self.assertEqual(401, self.client.put('/federation/v1/network-boot/storage/settings', json={}).status_code)
            save.assert_not_called()
        for error, status, event in ((ValueError(SECRET), 400, 'federation_settings_rejected'),
                                      (PermissionError(SECRET), 503, 'storage_unavailable')):
            with patch('app.network_boot_http._authorized', return_value=True), patch('app.network_boot_http.load_boot_settings', return_value={}), patch('app.network_boot_http.save_boot_settings', side_effect=error), self.assertLogs('simpleoffice.mini_services') as logs:
                response = self.client.put('/federation/v1/network-boot/storage/settings', json={})
            self.assertEqual(status, response.status_code)
            self.assertNotIn(SECRET, response.get_data(as_text=True))
            self.check_logs(logs, event)

    def test_dhcp_callback_never_receives_exception_payload(self):
        events = []
        service = DhcpService(DEFAULT_CONFIG['dhcp'], self.path, events.append)
        service.socket = Mock()
        service.socket.recvfrom.side_effect = [(b'packet', ('127.0.0.1', 1234)), OSError('stopped')]
        with patch.object(service, 'handle_packet', side_effect=ValueError(SECRET)):
            service._loop()
        self.assertEqual(1, len(events))
        self.assertEqual('packet_failed', events[0]['event'])
        self.assertNotIn(SECRET, json.dumps(events))
        self.assertEqual('ValueError', events[0]['diagnostic']['diagnostic'])

    def test_tftp_permission_reply_does_not_expose_server_paths(self):
        service = TftpService(DEFAULT_BOOT_SETTINGS, self.path)
        packet = struct.pack('!H', 1) + b'kernel\0octet\0'
        with patch('simpleoffice_network_boot.safe_asset_path', side_effect=PermissionError(SECRET)), patch.object(service, '_send_once') as send:
            service._serve_rrq(packet, ('127.0.0.1', 1234))
        payload = send.call_args.args[1]
        self.assertEqual((5, 2), struct.unpack('!HH', payload[:4]))
        self.assertNotIn(SECRET.encode(), payload)
        self.assertIn(b'access denied', payload)

    def test_gateway_subprocess_error_omits_command_payload(self):
        from simpleoffice_network_gateway import _run
        with patch('simpleoffice_network_gateway.shutil.which', return_value='/bin/tool'), patch('simpleoffice_network_gateway.subprocess.run', side_effect=OSError(SECRET)):
            result = _run(['tool', 'argument'])
        self.assertFalse(result['ok'])
        self.assertEqual('OSError', result['stderr'])
        self.assertNotIn(SECRET, json.dumps(result))
