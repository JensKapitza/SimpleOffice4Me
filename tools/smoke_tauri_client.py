#!/usr/bin/env python3
"""Exercise the packaged Linux Tauri UI through its public WebDriver interface.

Requires tauri-driver, WebKitWebDriver, Xvfb, xdotool and an ephemeral CI host
where a temporary localhost certificate can be added to system trust.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ELEMENT = 'element-6066-11e4-a52e-4f735466cecf'


def run(application):
    with tempfile.TemporaryDirectory(prefix='simpleoffice-tauri-smoke-') as directory:
        root = Path(directory)
        cert, key = root / 'localhost.crt', root / 'localhost.key'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-days', '1', '-subj', '/CN=localhost', '-addext',
                        'subjectAltName=DNS:localhost,IP:127.0.0.1', '-keyout',
                        str(key), '-out', str(cert)], check=True, capture_output=True)
        trust = Path('/usr/local/share/ca-certificates') / (root.name + '.crt')
        prefix = [] if os.geteuid() == 0 else ['sudo']
        subprocess.run(prefix + ['cp', str(cert), str(trust)], check=True)
        subprocess.run(prefix + ['update-ca-certificates'], check=True, capture_output=True)
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                payload = b'<h1 id="portal">Isolated HTTPS portal</h1>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                return

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:4444'
        session = None
        log = (root / 'driver.log').open('w+')
        driver = subprocess.Popen(['tauri-driver', '--native-driver', 'WebKitWebDriver'],
                                  stdout=log, stderr=subprocess.STDOUT)

        def command(method, endpoint, payload=None):
            encoded = None if payload is None else json.dumps(payload).encode()
            request = urllib.request.Request(base + endpoint, data=encoded, method=method,
                                             headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
            if isinstance(result.get('value'), dict) and result['value'].get('error'):
                raise RuntimeError(result['value'])
            return result.get('value')

        def wait_for(action):
            deadline = time.monotonic() + 30
            last = None
            while time.monotonic() < deadline:
                try:
                    result = action()
                    if result:
                        return result
                except (OSError, ValueError, urllib.error.URLError) as error:
                    last = error
                time.sleep(0.1)
            raise AssertionError(f'Public UI did not reach the expected state: {last}')

        try:
            wait_for(lambda: command('GET', '/status'))
            session = command('POST', '/session', {'capabilities': {'alwaysMatch': {
                'tauri:options': {'application': str(Path(application).resolve())}}}})['sessionId']
            route = '/session/' + session

            def element(selector):
                return command('POST', route + '/element',
                               {'using': 'css selector', 'value': selector})[ELEMENT]

            def text(selector):
                return command('GET', route + '/element/' + element(selector) + '/text')

            def submit(value):
                field = element('#endpoint')
                command('POST', route + '/element/' + field + '/clear', {})
                command('POST', route + '/element/' + field + '/value', {'text': value})
                command('POST', route + '/element/' + element('button[type=submit]') + '/click', {})

            chooser = command('GET', route + '/url')
            assert text('h1') == 'Mit Server verbinden'
            for value, expected in (
                ('http://localhost/office/', 'Eine HTTPS-Verbindung wird benötigt.'),
                ('https://user:password@localhost/', 'Nur die Serveradresse ohne Zugangsdaten oder Parameter angeben.'),
                ('https://localhost/?token=test', 'Nur die Serveradresse ohne Zugangsdaten oder Parameter angeben.'),
            ):
                submit(value)
                assert text('#error') == expected
                assert command('GET', route + '/url') == chooser
            target = f'https://localhost:{server.server_port}/office/'
            submit(target)
            wait_for(lambda: text('#portal') == 'Isolated HTTPS portal')
            assert command('GET', route + '/url') == target
            assert '/office/' in requests
            command('POST', route + '/execute/sync',
                    {'script': "window.location.assign('file:///etc/passwd'); return true;", 'args': []})
            time.sleep(0.5)
            assert command('GET', route + '/url') == target
            assert text('#portal') == 'Isolated HTTPS portal'
            subprocess.run(['xdotool', 'key', '--clearmodifiers', 'ctrl+l'], check=True)
            wait_for(lambda: text('h1') == 'Mit Server verbinden')
            assert command('GET', route + '/url') == chooser
            value = command('GET', route + '/element/' + element('#endpoint') + '/property/value')
            assert value == target
            print(json.dumps({'ok': True, 'checks': ['http-rejected', 'credentials-rejected',
                  'query-rejected', 'trusted-https-with-mount-path', 'file-navigation-blocked',
                  'native-menu-return-with-saved-endpoint']}))
        finally:
            if session:
                try:
                    command('DELETE', '/session/' + session)
                except (OSError, ValueError, urllib.error.URLError):
                    pass  # The process cleanup below also handles an exited WebView.
            driver.terminate()
            try:
                driver.wait(timeout=10)
            except subprocess.TimeoutExpired:
                driver.kill()
                driver.wait(timeout=5)
            server.shutdown()
            server.server_close()
            subprocess.run(prefix + ['rm', '-f', str(trust)], check=True)
            subprocess.run(prefix + ['update-ca-certificates', '--fresh'], check=True, capture_output=True)
            log.seek(0)
            print(log.read()[-6000:])
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application')
    args = parser.parse_args()
    for executable in ('tauri-driver', 'WebKitWebDriver', 'openssl', 'xdotool'):
        if not shutil.which(executable):
            parser.error(f'Required public UI test executable is missing: {executable}')
    run(args.application)


if __name__ == '__main__':
    main()
