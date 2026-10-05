"""Observable Mini Service contracts; synthetic Docker process, no provider billing."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from app import db as database
from werkzeug.security import generate_password_hash


class LiteLLMPublicApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.saved = {k: app.config.get(k) for k in ('TESTING', 'DATABASE', 'DOCUMENT_ROOT', 'TEST_CSRF_PROTECTION')}
        app.config.update(TESTING=True, DATABASE=str(self.root / 'auth.sqlite'),
                          DOCUMENT_ROOT=str(self.root / 'documents'), TEST_CSRF_PROTECTION=False)
        self.env = patch.dict(os.environ, {
            'SIMPLEOFFICE_MINI_SERVICES_CONFIG': str(self.root / 'mini-services.json'),
            'PATH': str(self.root), 'FAKE_DOCKER_ROOT': str(self.root),
        })
        self.env.start()
        docker = self.root / 'docker'
        docker.write_text('#!' + sys.executable + '\n' + '''
import json, os, sys
from pathlib import Path
root = Path(os.environ['FAKE_DOCKER_ROOT'])
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as out:
    out.write(json.dumps(args) + '\\n')
running = root / 'running'
if args[0] == 'ps':
    if running.exists(): print('abcdef123456')
elif args[0] == 'stop':
    running.unlink(missing_ok=True)
elif args[0] == 'compose':
    running.touch()
''')
        docker.chmod(0o755)
        self.context = app.app_context()
        self.context.push()
        database.ensure_auth_database()
        db = database.get_db()
        db.execute('INSERT INTO user(username,password,is_admin,created_at,updated_at) '
                   'VALUES (?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)',
                   ('public-gateway-admin', generate_password_hash('synthetic-api-password')))
        db.commit()
        ident = db.execute("SELECT id FROM user WHERE username='public-gateway-admin'").fetchone()[0]
        self.admin_id = ident
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = ident

    def tearDown(self):
        self.context.pop()
        app.config.update(self.saved)
        self.env.stop()
        self.temp.cleanup()

    def configure_local(self):
        response = self.client.post('/api/mini-services/litellm/settings', json={
            'enabled': True, 'mode': 'local', 'model': 'synthetic-model',
            'provider_model': 'openai/synthetic-model', 'api_key': 'synthetic-gateway-key',
        })
        self.assertEqual(200, response.status_code, response.data)

    def status(self):
        response = self.client.get('/api/mini-services/litellm')
        self.assertEqual(200, response.status_code, response.data)
        return response.json

    def calls(self):
        path = self.root / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_missing_docker_cannot_confirm_deactivation_or_change_saved_settings(self):
        """An unknown container state yields 503; enabled and encrypted backup stay unchanged."""
        self.configure_local()
        (self.root / 'running').touch()
        original = self.client.get('/admin/mini-services/litellm/backup.json').data
        (self.root / 'docker').unlink()
        response = self.client.post('/api/mini-services/litellm/settings', json={'enabled': False})
        self.assertEqual(503, response.status_code)
        self.assertTrue(self.status()['settings']['enabled'])
        self.assertEqual(original, self.client.get('/admin/mini-services/litellm/backup.json').data)
        self.assertTrue((self.root / 'running').exists())

    def test_start_and_stop_expose_lifecycle_states_without_false_fault(self):
        """POST start/stop return starting/stopped, also visible on a subsequent GET."""
        self.configure_local()
        started = self.client.post('/api/mini-services/litellm/start', json={})
        self.assertEqual(200, started.status_code)
        self.assertEqual('starting', started.json['state'])
        self.assertIsNone(started.json['last_error'])
        stopped = self.client.post('/api/mini-services/litellm/stop', json={})
        self.assertEqual(200, stopped.status_code)
        self.assertEqual('stopped', self.status()['state'])
        self.assertIsNone(self.status()['last_error'])
        self.assertFalse((self.root / 'running').exists())

    def test_restart_with_unusable_secret_keeps_existing_gateway_running(self):
        """A changed application key fails preflight; no Docker stop/remove is sent."""
        self.configure_local()
        (self.root / 'running').touch()
        with patch.dict(app.config, {'SECRET_KEY': 'different-synthetic-application-key'}):
            with self.client.session_transaction() as session:
                session['user_id'] = self.admin_id
            response = self.client.post('/api/mini-services/litellm/restart', json={})
        self.assertEqual(400, response.status_code)
        self.assertTrue((self.root / 'running').exists())
        self.assertFalse(any(call[0] in ('stop', 'rm') for call in self.calls()))

    def test_rejected_settings_preserve_last_successful_connection_status(self):
        """An invalid admin input is an operation error, not a failed health measurement."""
        self.configure_local()
        # Runtime file is a fixture representing an already measured successful probe.
        target = self.root / 'litellm' / 'health.json'
        import time
        target.write_text(json.dumps({'ok': True, 'code': 'ready', 'message': 'Gateway bereit',
                                      'updated_at': time.time()}))
        response = self.client.post('/api/mini-services/litellm/settings', json={'timeout': 0})
        self.assertEqual(400, response.status_code)
        self.assertEqual('running', self.status()['state'])
        self.assertTrue(self.status()['health']['ok'])
        self.assertIsNone(self.status()['last_error'])

    def test_recursive_corrupt_runtime_files_leave_admin_and_catalog_accessible(self):
        """Damaged settings fail safely; damaged health keeps the valid configuration available."""
        self.configure_local()
        raw = '[' * 2000 + '0' + ']' * 2000
        health = self.root / 'litellm' / 'health.json'
        health.write_text(raw)
        self.assertEqual('synthetic-model', self.status()['settings']['model'])
        self.assertEqual('unknown', self.status()['health']['code'])
        (self.root / 'litellm-service.json').write_text(raw)
        self.assertEqual('failed', self.status()['state'])
        self.assertEqual(200, self.client.get('/api/mini-services').status_code)
        self.assertEqual(200, self.client.get('/admin/mini-services/litellm').status_code)

    def test_invalid_restore_displays_failure_and_preserves_encrypted_backup(self):
        """The public restore form reports invalid JSON and does not replace configuration."""
        original = self.client.get('/admin/mini-services/litellm/backup.json').data
        response = self.client.post('/admin/mini-services/litellm/restore',
                                    data={'backup': (io.BytesIO(b'broken'), 'backup.json')}, follow_redirects=True)
        self.assertEqual(200, response.status_code)
        self.assertIn('Restore fehlgeschlagen', response.get_data(as_text=True))
        self.assertEqual(original, self.client.get('/admin/mini-services/litellm/backup.json').data)

    def test_install_and_start_use_reviewed_immutable_image(self):
        """Both image pull and Compose runtime reference the independently recorded CI digest."""
        self.configure_local()
        self.assertEqual(200, self.client.post('/api/mini-services/litellm/install', json={}).status_code)
        self.assertEqual(200, self.client.post('/api/mini-services/litellm/start', json={}).status_code)
        expected = ('docker.litellm.ai/berriai/litellm:v1.100.1@sha256:'
                    'a3715fa7ad8387941ab697259bd2881d68931657247a41984f90fae6d11c62bf')
        self.assertIn(['pull', expected], self.calls())
        compose = json.loads((self.root / 'litellm' / 'compose.json').read_text())
        self.assertEqual(expected, compose['services']['gateway']['image'])
        # Isolate the digest contract from the separate running-container edit guard.
        self.assertEqual(200, self.client.post('/api/mini-services/litellm/stop', json={}).status_code)
        response = self.client.post('/api/mini-services/litellm/settings', json={'version': '1.100.2'})
        self.assertEqual(400, response.status_code)
        self.assertEqual('1.100.1', self.status()['settings']['version'])
