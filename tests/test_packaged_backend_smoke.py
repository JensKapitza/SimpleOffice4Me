"""Public CLI contracts; native onefile lifecycle is checked using the real CI binary."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "desktop" / "python-setup" / "smoke_packaged_backend.py"


@unittest.skipIf(os.name == 'nt', 'POSIX executable fixture; Windows uses the real packaged CI binary')
class PackagedSmokePublicContracts(unittest.TestCase):
    def invoke(self, *, status=200, charset='utf-8', content='SimpleOffice4Me'):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'independent-http-backend'
            binary.write_text('#!' + sys.executable + '\n' + '''
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = os.environ['FIXTURE_CONTENT'].encode('utf-8')
        self.send_response(int(os.environ['FIXTURE_STATUS']))
        self.send_header('Content-Type', 'text/html; charset=' + os.environ['FIXTURE_CHARSET'])
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

server = HTTPServer(('127.0.0.1', int(os.environ['SIMPLEOFFICE_PORT'])), Handler)
server.handle_request()
server.server_close()
''', encoding='utf-8')
            binary.chmod(0o700)
            environment = {**os.environ, 'FIXTURE_STATUS': str(status),
                           'FIXTURE_CHARSET': charset, 'FIXTURE_CONTENT': content}
            return subprocess.run([sys.executable, str(SCRIPT), str(binary)],
                                  env=environment, capture_output=True, text=True, timeout=20)

    def test_public_application_http_200_utf8_is_accepted(self):
        """GET / with literal application identity and UTF-8 succeeds through the public CLI."""
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('HTTP 200, UTF-8', result.stdout)

    def test_http_error_page_is_not_a_successful_backend_start(self):
        """GET / returning 404 or 500 must make the CLI fail, unlike the old <500 gate."""
        for status in (404, 500):
            with self.subTest(status=status):
                result = self.invoke(status=status)
                self.assertNotEqual(0, result.returncode)
                self.assertNotIn('HTTP smoke passed', result.stdout)

    def test_unrelated_or_non_utf8_http_200_is_rejected(self):
        """A wrong listener or wrong encoding cannot satisfy the public startup contract."""
        for values in ({'content': 'Unrelated service'}, {'charset': 'windows-1252'}):
            with self.subTest(values=values):
                result = self.invoke(**values)
                self.assertNotEqual(0, result.returncode)
                self.assertNotIn('HTTP smoke passed', result.stdout)


if __name__ == "__main__":
    unittest.main()
