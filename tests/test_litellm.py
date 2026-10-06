"""Gateway negative cases and reversible operations; no provider billing in tests."""
import io
import json
import os
import stat
import socket
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from app import litellm_config as config
from app import litellm_gateway as gateway
from app import litellm_service as service
from werkzeug.security import generate_password_hash


class LiteLLMTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.saved = {key: app.config.get(key) for key in ("TESTING", "DATABASE", "DOCUMENT_ROOT", "TEST_CSRF_PROTECTION")}
        app.config.update(TESTING=True, DATABASE=str(self.root / "db.sqlite"), DOCUMENT_ROOT=str(self.root / "documents"), TEST_CSRF_PROTECTION=False)
        self.path_patch = patch('app.litellm_config.default_config_path', return_value=self.root / 'mini-services.json')
        self.path_patch.start()
        self.context = app.app_context()
        self.context.push()
        database.ensure_auth_database()
        db = database.get_db()
        for name, admin in [('gateway-admin', 1), ('gateway-user', 0)]:
            db.execute('INSERT INTO user(username,password,is_admin,created_at,updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)',
                       (name, generate_password_hash('test-only-password'), admin))
        db.commit()
        self.admin = db.execute("SELECT id FROM user WHERE username='gateway-admin'").fetchone()[0]
        self.user = db.execute("SELECT id FROM user WHERE username='gateway-user'").fetchone()[0]
        self.client = app.test_client()

    def tearDown(self):
        self.context.pop()
        self.path_patch.stop()
        app.config.update(self.saved)
        self.temp.cleanup()

    def login(self, user=None):
        with self.client.session_transaction() as session:
            session['user_id'] = self.admin if user is None else user

    def enable(self, **values):
        result = config.prepare({'enabled': True, 'mode': 'external', 'base_url': 'https://gateway.example/v1',
                                 'model': 'test-model', 'api_key': 'test-only-key', **values})
        config.persist(result)
        return result

    def test_disabled_is_optional_and_no_network(self):
        self.assertFalse(config.settings()['enabled'])
        with patch.object(gateway, '_exchange') as exchange:
            self.assertEqual('disabled', gateway.probe()['code'])
            with self.assertRaisesRegex(gateway.GatewayError, 'disabled'):
                gateway.completion([{'role': 'user', 'content': 'hello'}])
            exchange.assert_not_called()
        self.assertEqual('disabled', service.status()['state'])

    def test_secrets_encrypted_and_never_in_public_settings_or_compose(self):
        self.enable(mode='local', provider_model='openai/test', provider_key='test-only-provider')
        raw = config.settings_path().read_text()
        self.assertNotIn('test-only-key', raw)
        self.assertNotIn('test-only-provider', raw)
        self.assertEqual('test-only-key', config.secret(config.settings(), 'api_key'))
        self.assertFalse(any(k.endswith('_enc') for k in service.status()['config']))
        path = service._compose(config.settings())
        self.assertNotIn('test-only-key', path.read_text())
        self.assertNotIn('test-only-provider', (service.directory() / 'config.yaml').read_text())
        doc = json.loads(path.read_text())['services']['gateway']
        self.assertEqual(['127.0.0.1:4000:4000'], doc['ports'])
        self.assertEqual('on-failure:3', service._compose({**config.settings(), 'autostart': True}) and json.loads(path.read_text())['services']['gateway']['restart'])
        self.assertNotIn('DATABASE_URL', doc['environment'])
        if os.name == 'posix':
            self.assertEqual(0o644, stat.S_IMODE((service.directory() / 'config.yaml').stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(config.settings_path().stat().st_mode))

    def test_invalid_config_rejected_without_changing_settings(self):
        self.enable()
        original = config.settings_path().read_bytes()
        bad_values = [{'base_url': u} for u in ['http://gateway.example', 'https://user:pass@gateway.example',
                      'https://gateway.example?a=key', 'https://gateway.example/anything', 'https://gateway.example#fragment']]
        bad_values += [{'timeout': 'nan'}, {'timeout': 0}, {'retries': 3}, {'port': 80}, {'enabled': 'true'},
                       {'version': 'latest'}, {'version': '1.90.2'}, {'api_key': 'bad\r\nkey'}, {'unexpected': True}]
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                config.prepare(value)
            self.assertEqual(original, config.settings_path().read_bytes())

    def test_ssrf_all_dns_answers_checked_and_private_requires_explicit_cidr(self):
        cfg = self.enable()
        def resolved(ip):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))]
        for ip in ['127.0.0.1', '169.254.169.254', '10.0.0.1', '0.0.0.0', '224.0.0.1']:
            with self.subTest(ip=ip), patch.object(gateway, '_resolve', return_value=resolved(ip)):
                with self.assertRaises(gateway.GatewayError):
                    gateway.addresses(cfg, 'gateway.example', 443)
        with patch.object(gateway, '_resolve', return_value=resolved('10.0.0.1')):
            self.assertEqual('10.0.0.1', gateway.addresses({**cfg, 'allowed_networks': ['10.0.0.1/32']}, 'gateway.example', 443)[0][1][0])
        with patch.object(gateway, '_resolve', return_value=resolved('1.1.1.1') + resolved('127.0.0.1')):
            with self.assertRaises(gateway.GatewayError):
                gateway.addresses(cfg, 'gateway.example', 443)
        site_local = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('fec0::1', 443, 0, 0))]
        with patch.object(gateway, '_resolve', return_value=site_local):
            with self.assertRaisesRegex(gateway.GatewayError, 'endpoint_blocked'):
                gateway.addresses(cfg, 'gateway.example', 443)
        with patch.object(gateway, '_resolve', return_value=site_local):
            allowed = gateway.addresses({**cfg, 'allowed_networks': ['fec0::1/128']}, 'gateway.example', 443)
            self.assertEqual('fec0::1', allowed[0][1][0])

    def test_gateway_hostname_idna_is_validated_before_persist(self):
        self.enable()
        original = config.settings_path().read_bytes()
        with self.assertRaisesRegex(ValueError, 'DNS-Name'):
            config.prepare({'base_url': 'https://' + ('é' * 100) + '.example'})
        self.assertEqual(original, config.settings_path().read_bytes())

    def test_completion_bounds_content_before_serializing_request(self):
        self.enable()
        with patch.object(gateway, 'request_gateway') as request:
            with self.assertRaisesRegex(ValueError, 'zu groß'):
                gateway.completion([{'role': 'user', 'content': 'x' * (gateway.MAX_BYTES + 1)}])
            request.assert_not_called()
        with patch.object(gateway, 'request_gateway') as request:
            chunk = 'ä' * (gateway.MAX_BYTES // 3)
            with self.assertRaisesRegex(ValueError, 'zu groß'):
                gateway.completion([{'role': 'user', 'content': chunk}, {'role': 'assistant', 'content': chunk}])
            request.assert_not_called()

    def test_finite_retry_no_retry_for_auth_or_completion(self):
        self.enable(retries=2)
        with patch.object(gateway, '_exchange', side_effect=OSError('private payload')) as exchange:
            with self.assertRaisesRegex(gateway.GatewayError, '^unreachable$'):
                gateway.request_gateway('/v1/models')
            self.assertEqual(3, exchange.call_count)
        with patch.object(gateway, '_exchange', side_effect=gateway.GatewayError('unauthorized')) as exchange:
            with self.assertRaisesRegex(gateway.GatewayError, 'unauthorized'):
                gateway.request_gateway('/v1/models')
            self.assertEqual(1, exchange.call_count)
        with patch.object(gateway, '_exchange', side_effect=gateway.GatewayError('temporarily_unavailable')) as exchange:
            with self.assertRaises(gateway.GatewayError):
                gateway.completion([{'role': 'user', 'content': 'hello'}])
            self.assertEqual(1, exchange.call_count)

    def test_readiness_and_model_check_without_billable_completion(self):
        self.enable()
        with patch.object(gateway, '_exchange', side_effect=[{}, {}, {'data': [{'id': 'test-model'}]}]) as exchange:
            self.assertTrue(gateway.probe()['ok'])
            self.assertEqual(['/health/liveliness', '/health/readiness', '/v1/models'], [c.args[1] for c in exchange.call_args_list])
        with patch.object(gateway, '_exchange', side_effect=[{}, {}, {'data': []}]):
            self.assertEqual('model_missing', gateway.probe()['code'])
        for code in ('timeout', 'unauthorized', 'unreachable'):
            with patch.object(gateway, '_exchange', side_effect=gateway.GatewayError(code)):
                self.assertEqual(code, gateway.probe()['code'])

    def test_local_lifecycle_and_secret_environment(self):
        self.enable(mode='local', provider_model='openai/test')
        with patch.object(service, '_docker', return_value=b'') as docker:
            service.action('install')
            self.assertEqual('pull', docker.call_args.args[0][0])
            service.action('start')
            self.assertIn('--pull', docker.call_args.args[0])
            self.assertEqual('test-only-key', docker.call_args.kwargs['environment']['LITELLM_MASTER_KEY'])
            service.action('restart')
            self.assertEqual('compose', docker.call_args.args[0][0])
        config.settings_path().write_text('invalid')
        with patch.object(service, '_docker', return_value=b''):
            service.action('stop')  # Damaged settings cannot make the container unstoppable.

    def test_deactivation_stops_owned_container_before_saving(self):
        self.enable(mode='local', provider_model='openai/test')
        with patch.object(service, '_running', return_value=True), patch.object(service, '_stop') as stop:
            service.save_settings({'enabled': False})
            stop.assert_called_once()
        self.assertFalse(config.settings()['enabled'])
        self.enable(mode='local', provider_model='openai/test')
        with patch.object(service, '_running', return_value=True), patch.object(service, '_stop', side_effect=RuntimeError('stop failed')):
            with self.assertRaises(RuntimeError):
                service.save_settings({'enabled': False})
        self.assertTrue(config.settings()['enabled'])

    def test_backup_restore_preserves_encryption_and_forces_disabled(self):
        self.enable()
        backup = service.backup()
        self.assertNotIn(b'test-only-key', backup)
        payload = json.loads(backup)
        with self.assertRaises(ValueError):
            service.restore(payload)
        config.persist(config.prepare({'enabled': False}))
        service.restore(payload)
        restored = config.settings()
        self.assertFalse(restored['enabled'])
        self.assertFalse(restored['autostart'])
        self.assertEqual('test-only-key', config.secret(restored, 'api_key'))
        self.assertEqual(payload['settings']['api_key_enc'], restored['api_key_enc'])
        original = config.settings_path().read_bytes()
        application_key = app.config['SECRET_KEY']
        app.config['SECRET_KEY'] = 'different-test-application-key'
        try:
            with self.assertRaises(ValueError):
                service.restore(payload)
        finally:
            app.config['SECRET_KEY'] = application_key
        self.assertEqual(original, config.settings_path().read_bytes())
        payload['settings']['api_key_enc'] = 'enc:v1:broken'
        with self.assertRaises(ValueError):
            service.restore(payload)
        self.assertEqual(original, config.settings_path().read_bytes())

    def test_deactivation_removes_already_stopped_owned_containers(self):
        self.enable(mode='local', provider_model='openai/test')
        calls = []
        def docker(args, **kwargs):
            calls.append(args)
            return b'abcdef123456\n' if args[0] == 'ps' else b''
        with patch.object(service, '_docker', side_effect=docker):
            service.save_settings({'enabled': False})
        self.assertFalse(config.settings()['enabled'])
        self.assertTrue(any(call[0] == 'ps' and '-aq' in call for call in calls))
        self.assertIn(['stop', '--time', '5', 'abcdef123456'], calls)
        self.assertIn(['rm', 'abcdef123456'], calls)

    def test_admin_permissions_csrf_and_no_secrets_in_html(self):
        for path in ['/admin/mini-services/litellm', '/admin/mini-services/litellm/backup.json']:
            self.assertEqual(302, self.client.get(path).status_code)
        self.login(self.user)
        self.assertEqual(403, self.client.get('/admin/mini-services/litellm').status_code)
        self.assertEqual(403, self.client.post('/api/mini-services/litellm/scan', json={}).status_code)
        self.login()
        self.enable()
        response = self.client.get('/admin/mini-services/litellm')
        self.assertEqual(200, response.status_code)
        self.assertNotIn(b'test-only-key', response.data)
        app.config['TEST_CSRF_PROTECTION'] = True
        self.assertEqual(403, self.client.post('/api/mini-services/litellm/settings', json={'enabled': False}).status_code)
        app.config['TEST_CSRF_PROTECTION'] = False
        self.assertEqual(200, self.client.post('/api/mini-services/litellm/settings', json={'enabled': False}).status_code)
        response = self.client.get('/admin/mini-services/litellm/backup.json')
        self.assertEqual('no-store', response.headers['Cache-Control'])
        restored = self.client.post('/admin/mini-services/litellm/restore', data={'backup': (io.BytesIO(response.data), 'backup.json')})
        self.assertEqual(302, restored.status_code)
        self.assertFalse(config.settings()['enabled'])
        with patch.object(gateway, '_exchange', side_effect=gateway.GatewayError('unauthorized')):
            self.enable()
            failed = self.client.post('/api/mini-services/litellm/scan', json={})
            self.assertEqual(503, failed.status_code)
            self.assertEqual('unauthorized', failed.json['health']['code'])
        events = database.get_db().execute("SELECT outcome FROM security_event WHERE target_id='litellm' AND action='mini_service_scan'").fetchall()
        self.assertEqual('failure', events[-1][0])

    def test_autostart_and_failed_install_diagnostics(self):
        with patch.object(service, 'action') as action:
            service.autostart()
            action.assert_not_called()
            self.enable(autostart=True)
            service.autostart()
            action.assert_not_called()
            self.enable(mode='local', provider_model='openai/test', autostart=True)
            service.autostart()
            action.assert_called_once_with('start')
        self.login()
        with patch.object(service, '_running', return_value=False), patch.object(service, '_docker', side_effect=RuntimeError('private secret payload')):
            response = self.client.post('/admin/mini-services/litellm/action/install', follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn(b'LiteLLM-Aktion fehlgeschlagen', response.data)
        self.assertNotIn(b'private secret payload', response.data)
        self.assertEqual('unknown', service.status()['health']['code'])
        events = database.get_db().execute("SELECT outcome FROM security_event WHERE target_id='litellm' AND action='mini_service_install'").fetchall()
        self.assertEqual('failure', events[-1][0])

    def test_corrupt_config_restore_and_start_failure_do_not_destroy_backup(self):
        self.enable()
        payload = json.loads(service.backup())
        config.settings_path().write_text('corrupt')
        with patch.object(service, '_running', return_value=False):
            service.restore(payload)
        self.assertFalse(config.settings()['enabled'])
        self.assertEqual('test-only-key', config.secret(config.settings(), 'api_key'))
        self.enable(mode='local', provider_model='openai/test')
        before = service.backup()
        with patch.object(service, '_docker', side_effect=FileNotFoundError('missing Docker')):
            with self.assertRaises(FileNotFoundError):
                service.action('start')
        self.assertEqual(before, service.backup())

    def test_corrupt_health_preserves_settings_and_admin_page(self):
        self.enable()
        self.login()
        target = service.directory() / 'health.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        for raw in ('[]', '0', '{"updated_at":"bad"}', '{"updated_at":NaN}', 'broken', '[' * 2000 + '0' + ']' * 2000):
            target.write_text(raw)
            row = service.status()
            self.assertEqual('test-model', row['settings']['model'])
            self.assertFalse(row['health']['ok'])
            self.assertEqual(200, self.client.get('/admin/mini-services/litellm').status_code)

    def test_unused_disabled_local_mode_can_return_to_external_without_docker(self):
        config.persist(config.prepare({'enabled': False, 'mode': 'local'}))
        with patch.object(service, '_running') as running:
            result = service.save_settings({'mode': 'external'})
        self.assertEqual('external', result['mode'])
        running.assert_not_called()

    def test_missing_docker_cannot_disable_or_restore_a_local_gateway(self):
        self.enable(mode='local', provider_model='openai/test')
        original = config.settings_path().read_bytes()
        with patch.object(service.shutil, 'which', return_value=None):
            with self.assertRaises(RuntimeError):
                service.save_settings({'enabled': False})
        self.assertEqual(original, config.settings_path().read_bytes())
        config.persist(config.prepare({'enabled': False}))
        original = config.settings_path().read_bytes()
        with patch.object(service.shutil, 'which', return_value=None):
            with self.assertRaises(RuntimeError):
                service.restore(json.loads(service.backup()))
        self.assertEqual(original, config.settings_path().read_bytes())

    def test_restart_preflight_failure_preserves_running_container(self):
        self.enable(mode='local', provider_model='openai/test')
        for name, failure in [('_environment', ValueError('key unavailable')),
                              ('_compose', OSError('disk full')),
                              ('_docker', RuntimeError('image missing'))]:
            with self.subTest(step=name), patch.object(service, name, side_effect=failure), patch.object(service, '_stop') as stop:
                with self.assertRaises(type(failure)):
                    service.action('restart')
                stop.assert_not_called()

    def test_restart_preflight_completes_before_stop(self):
        self.enable(mode='local', provider_model='openai/test')
        order = []
        def docker(args, **kwargs):
            order.append(args[0])
            return b''
        def stop():
            self.assertTrue((service.directory() / 'compose.json').exists())
            self.assertEqual(['image', 'compose'], order)
            order.append('stop')
        with patch.object(service, '_docker', side_effect=docker), patch.object(service, '_stop', side_effect=stop):
            service.action('restart')
        self.assertEqual(['image', 'compose', 'stop', 'compose'], order)

    def test_lifecycle_states_do_not_report_successful_stop_as_fault(self):
        self.enable(mode='local', provider_model='openai/test')
        with patch.object(service, '_docker', return_value=b''):
            started = service.action('start')
            stopped = service.action('stop')
        self.assertEqual('starting', started['state'])
        self.assertIsNone(started['last_error'])
        self.assertEqual('stopped', stopped['state'])
        self.assertIsNone(stopped['last_error'])
        with patch.object(service.time, 'time', return_value=time.time() + 120):
            self.assertEqual('stopped', service.status()['state'])

    def test_rejected_settings_and_lock_contention_preserve_measured_health(self):
        from tools.service_control import exclusive_lease
        self.enable(mode='local', provider_model='openai/test')
        self.login()
        service._record({'ok': True, 'code': 'ready', 'message': 'Gateway bereit.'})
        original = (service.directory() / 'health.json').read_bytes()
        for payload in ({'timeout': 0}, {'enabled': True}):
            with patch.object(service, '_running', return_value=True):
                self.assertEqual(400, self.client.post('/api/mini-services/litellm/settings', json=payload).status_code)
            self.assertEqual(original, (service.directory() / 'health.json').read_bytes())
        with exclusive_lease(service.directory() / 'control.lock') as acquired:
            self.assertTrue(acquired)
            self.assertEqual(503, self.client.post('/api/mini-services/litellm/scan', json={}).status_code)
        self.assertEqual(original, (service.directory() / 'health.json').read_bytes())
        self.assertEqual('running', service.status()['state'])

    def test_recursive_settings_leave_admin_and_hub_recoverable(self):
        self.enable()
        self.login()
        config.settings_path().write_text('[' * 2000 + '0' + ']' * 2000)
        self.assertEqual('failed', service.status()['state'])
        self.assertEqual(200, self.client.get('/admin/mini-services/litellm').status_code)
        self.assertEqual(200, self.client.get('/api/mini-services').status_code)
        response = self.client.post('/admin/mini-services/litellm/restore', data={
            'backup': (io.BytesIO(('[' * 2000 + '0' + ']' * 2000).encode()), 'backup.json')}, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn(b'Restore fehlgeschlagen', response.data)

    def test_image_digest_validation_and_execution_are_immutable(self):
        cfg = self.enable(mode='local', provider_model='openai/test')
        image = config.image_reference(cfg)
        self.assertTrue(image.endswith('@' + config.DEFAULTS['image_digest']))
        self.assertEqual(image, json.loads(service._compose(cfg).read_text())['services']['gateway']['image'])
        with patch.object(service, '_docker', return_value=b'') as docker:
            service.action('install')
            self.assertEqual(['pull', image], docker.call_args.args[0])
        for digest in ('latest', 'sha256:bad', '', 'sha256:' + 'g' * 64):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                config.prepare({'image_digest': digest})
        with self.assertRaises(ValueError):
            config.prepare({'version': '1.100.2'})
        new_digest = 'sha256:' + '1' * 64
        self.assertEqual(new_digest, config.prepare({'version': '1.100.2', 'image_digest': new_digest})['image_digest'])
